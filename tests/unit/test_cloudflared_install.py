"""Verify installation without trusting downloads or altering the system PATH."""

from __future__ import annotations

import hashlib
import io
import tarfile
from pathlib import Path

import httpx
import pytest

from maivn import cli
from maivn._internal import cloudflared_install as installer


@pytest.fixture
def destination(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Use a private disposable installation root."""
    monkeypatch.setattr(installer, '_tools_root', lambda: tmp_path / 'tools')
    monkeypatch.setattr(installer.platform, 'system', lambda: 'Windows')
    monkeypatch.setattr(installer.platform, 'machine', lambda: 'AMD64')
    return tmp_path / 'tools'


def download(monkeypatch: pytest.MonkeyPatch, payload: bytes, *, valid: bool = True) -> None:
    """Replace network transport while retaining streaming and checksum verification."""
    digest = hashlib.sha256(payload).hexdigest() if valid else '0' * 64
    monkeypatch.setitem(installer.ASSETS, ('Windows', 'amd64'), ('client.exe', digest))
    transport = httpx.MockTransport(lambda _: httpx.Response(200, content=payload))
    original = httpx.Client

    def client(*, timeout: float, follow_redirects: bool) -> httpx.Client:
        return original(transport=transport, timeout=timeout, follow_redirects=follow_redirects)

    monkeypatch.setattr(installer.httpx, 'Client', client)


def test_verified_install_is_atomic_and_reusable(
    destination: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Only verified bytes become the executable; a second call is offline."""
    download(monkeypatch, b'verified executable')
    result = installer.install_cloudflared()
    assert result.is_relative_to(destination)
    assert result.read_bytes() == b'verified executable'

    def unexpected_download(**_: object) -> None:
        pytest.fail('downloaded twice')

    monkeypatch.setattr(installer.httpx, 'Client', unexpected_download)
    assert installer.install_cloudflared() == result
    assert installer.find_cloudflared() == str(result)


def test_checksum_failure_leaves_no_executable_and_can_retry(
    destination: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A corrupted download is never installed or left as a partial executable."""
    with monkeypatch.context() as failed_attempt:
        download(failed_attempt, b'corrupt', valid=False)
        with pytest.raises(RuntimeError, match='checksum'):
            installer.install_cloudflared()
    assert not any(path.is_file() for path in destination.rglob('*.exe'))
    assert not list(destination.rglob('*.part'))
    download(monkeypatch, b'verified retry')
    assert installer.install_cloudflared().read_bytes() == b'verified retry'


def test_empty_windows_machine_uses_python_build(
    destination: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Some isolated Windows runtimes omit the environment used by platform.machine."""
    download(monkeypatch, b'client')
    monkeypatch.setattr(installer.platform, 'machine', lambda: '')
    monkeypatch.setattr(installer.sysconfig, 'get_platform', lambda: 'win-amd64')
    assert installer.install_cloudflared().is_relative_to(destination)


def test_concurrent_install_refuses_without_download(destination: Path) -> None:
    """Two frontends cannot race to replace a managed executable."""
    installer.private_directory(destination)
    lock = installer.StateLock(destination / 'install.lock')
    try:
        with pytest.raises(RuntimeError, match='Another cloudflared installation'):
            installer.install_cloudflared()
    finally:
        lock.close()


def test_unsupported_platform_has_no_side_effects(
    destination: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Do not guess binary compatibility on Windows ARM."""
    monkeypatch.setattr(installer.platform, 'machine', lambda: 'ARM64')
    with pytest.raises(RuntimeError, match='not supported'):
        installer.install_cloudflared()
    assert not destination.exists()


def test_archive_cannot_install_symlink(tmp_path: Path) -> None:
    """A verified archive still must contain a regular executable member."""
    archive = tmp_path / 'download.tgz'
    with tarfile.open(archive, 'w:gz') as bundle:
        member = tarfile.TarInfo('cloudflared')
        member.type = tarfile.SYMTYPE
        member.linkname = '../outside'
        bundle.addfile(member)
    with pytest.raises(RuntimeError, match='regular'):
        installer.extract_binary(archive, tmp_path / 'client')
    assert not (tmp_path / 'client').exists()


def test_archive_extracts_only_expected_member(tmp_path: Path) -> None:
    """No archive path is used as a filesystem destination."""
    archive = tmp_path / 'download.tgz'
    with tarfile.open(archive, 'w:gz') as bundle:
        for name in ('../outside', 'cloudflared'):
            member = tarfile.TarInfo(name)
            member.size = 4
            bundle.addfile(member, io.BytesIO(b'test'))
    installer.extract_binary(archive, tmp_path / 'client')
    assert (tmp_path / 'client').read_bytes() == b'test'


def test_cli_install_does_not_open_listener(monkeypatch: pytest.MonkeyPatch) -> None:
    """Installing is a separate explicit operation from exposing receivers."""
    monkeypatch.setattr(cli, 'install_cloudflared', lambda: Path('client.exe'))

    def unexpected_listener(*_: object) -> None:
        pytest.fail('opened listener')

    monkeypatch.setattr(cli, 'ReceiverProxy', unexpected_listener)
    monkeypatch.setattr('sys.argv', ['maivn', 'dev-tunnel', '--install'])
    cli.main()
    monkeypatch.setattr(
        'sys.argv', ['maivn', 'dev-tunnel', '--install', '--connection-id', 'conn_test']
    )
    with pytest.raises(SystemExit) as error:
        cli.main()
    assert error.value.code == 2  # noqa: PLR2004 - argparse's usage error status
