"""Install a pinned official Apache-2.0 cloudflared release without system changes."""

from __future__ import annotations

import hashlib
import platform
import shutil
import sysconfig
import tarfile
import time
from pathlib import Path
from tempfile import TemporaryDirectory

import httpx

from maivn._internal.local_file_security import StateLock, is_link, private_directory

CLOUDFLARED_VERSION = '2026.8.3'
# Official GitHub release asset SHA256 digests, pinned together with the version.
ASSETS = {
    ('Windows', 'amd64'): (
        'cloudflared-windows-amd64.exe',
        '83e726ed18ea78c5ad5213c4c3a3a27051393950d2bc8ed4de69bec12d14eaae',
    ),
    ('Windows', '386'): (
        'cloudflared-windows-386.exe',
        'bdfab00122a3c2a0772d3f176445f6baf0271fed71656d0902cbc23a0eea7048',
    ),
    ('Linux', 'amd64'): (
        'cloudflared-linux-amd64',
        'f29324fe934d1e100617484c78deef803c4dc2cd351d645bbde42e96b4fccc5e',
    ),
    ('Linux', '386'): (
        'cloudflared-linux-386',
        '691e3a2b8926f90ec4fcec4f4bc8e38b1de14f6232d93a24f6cb7b2c23ab5e92',
    ),
    ('Linux', 'arm64'): (
        'cloudflared-linux-arm64',
        '4bcfd35521a7cbc545ebfd5d57334a71ee180e2a64874981f374c81472118391',
    ),
    ('Linux', 'arm'): (
        'cloudflared-linux-arm',
        '7a7cac4ad4561ff55797eaf27aae1a0be37498c85502715bc87e3bad919d928c',
    ),
    ('Darwin', 'amd64'): (
        'cloudflared-darwin-amd64.tgz',
        '61e1316266a00fd70ce40da011d612badc805367fb65293dd1925f938f704c99',
    ),
    ('Darwin', 'arm64'): (
        'cloudflared-darwin-arm64.tgz',
        '40c9144d86df8937c5b43293a1f7d2d2107029aa74725023dd46b1b27154352f',
    ),
}
_MAX_BYTES = 128 * 1024 * 1024
_MAX_SECONDS = 120


def _tools_root() -> Path:
    return Path.home() / '.maivn' / 'tools' / 'cloudflared'


def _asset() -> tuple[str, str]:
    machine = platform.machine().lower()
    if not machine and platform.system() == 'Windows':
        machine = {'win-amd64': 'amd64', 'win32': '386', 'win-arm64': 'arm64'}.get(
            sysconfig.get_platform(), ''
        )
    machine = {
        'x86_64': 'amd64',
        'x86': '386',
        'i386': '386',
        'i686': '386',
        'aarch64': 'arm64',
        'armv7l': 'arm',
    }.get(machine, machine)
    result = ASSETS.get((platform.system(), machine))
    if result is None:
        message = (
            'Automatic cloudflared installation is not supported on this platform. '
            'Install an official compatible client on PATH.'
        )
        raise RuntimeError(message)
    return result


def _destination(asset: str) -> Path:
    filename = 'cloudflared.exe' if asset.endswith('.exe') else 'cloudflared'
    return _tools_root() / CLOUDFLARED_VERSION / asset / filename


def _regular(path: Path) -> bool:
    return path.is_file() and not any(is_link(item.lstat()) for item in (path, *path.parents))


def find_cloudflared() -> str | None:
    """Find the managed client first, then an existing installation on PATH."""
    try:
        path = _destination(_asset()[0])
        if _regular(path):
            return str(path)
    except (OSError, RuntimeError):
        pass
    return shutil.which('cloudflared')


def extract_binary(archive: Path, destination: Path) -> None:
    """Copy only the bounded regular cloudflared member, never archive paths."""
    with tarfile.open(archive, 'r:gz') as bundle:
        members = [item for item in bundle if item.name in {'cloudflared', './cloudflared'}]
        if len(members) != 1 or not members[0].isreg() or not 0 < members[0].size <= _MAX_BYTES:
            message = 'The cloudflared archive must contain one bounded regular executable.'
            raise RuntimeError(message)
        source = bundle.extractfile(members[0])
        if source is None:
            message = 'The cloudflared archive has no executable.'
            raise RuntimeError(message)
        with source, destination.open('xb') as output:
            shutil.copyfileobj(source, output)


def _download(asset: str, expected: str, destination: Path) -> None:
    url = (
        f'https://github.com/cloudflare/cloudflared/releases/download/{CLOUDFLARED_VERSION}/{asset}'
    )
    digest = hashlib.sha256()
    size = 0
    deadline = time.monotonic() + _MAX_SECONDS
    with (
        httpx.Client(timeout=20, follow_redirects=True) as client,
        client.stream('GET', url) as response,
    ):
        response.raise_for_status()
        if response.url.scheme != 'https':
            message = 'cloudflared download requires HTTPS.'
            raise RuntimeError(message)
        with destination.open('xb') as output:
            for chunk in response.iter_bytes():
                size += len(chunk)
                if size > _MAX_BYTES or time.monotonic() > deadline:
                    message = 'cloudflared download exceeded its size or time limit. Try again.'
                    raise RuntimeError(message)
                digest.update(chunk)
                output.write(chunk)
    if digest.hexdigest() != expected:
        message = 'cloudflared checksum verification failed. Nothing was installed; try again.'
        raise RuntimeError(message)


def install_cloudflared() -> Path:
    """Download and verify the pinned client; never start it or modify PATH."""
    asset, expected = _asset()
    destination = _destination(asset)
    # Validate every existing ancestor before creating private managed storage.
    for parent in reversed(destination.parent.parents):
        if parent.exists() and is_link(parent.lstat()):
            message = 'cloudflared tools storage cannot use symlinks or junctions.'
            raise RuntimeError(message)
    private_directory(_tools_root())
    private_directory(destination.parent)
    try:
        lock = StateLock(_tools_root() / 'install.lock')
    except RuntimeError:
        message = 'Another cloudflared installation is running. Wait and try again.'
        raise RuntimeError(message) from None
    try:
        if _regular(destination):
            return destination
        with TemporaryDirectory(prefix='download-', dir=destination.parent) as temporary:
            archive = Path(temporary) / 'download.part'
            _download(asset, expected, archive)
            binary = Path(temporary) / 'client'
            if asset.endswith('.tgz'):
                extract_binary(archive, binary)
            else:
                archive.rename(binary)
            binary.chmod(0o700)
            binary.replace(destination)
    except (httpx.HTTPError, tarfile.TarError):
        message = 'Could not download cloudflared from GitHub. Check network access and retry.'
        raise RuntimeError(message) from None
    finally:
        lock.close()
    return destination
