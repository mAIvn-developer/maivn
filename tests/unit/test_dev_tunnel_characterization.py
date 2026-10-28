"""Characterize the developer tunnel: the loopback receiver proxy and the cloudflared client.

The proxy is exercised with real loopback sockets. The cloudflared client is a
Python stand-in reached through ``PATH`` and the installer downloads from an
in-memory ``httpx.MockTransport``. Nothing here touches the network.
"""

from __future__ import annotations

import hashlib
import http.client
import io
import os
import platform
import socket
import subprocess
import sys
import tarfile
import tempfile
import time
from contextlib import contextmanager
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from threading import Thread
from typing import TYPE_CHECKING

import httpx
import pytest

from maivn.dev_tunnel import (
    CLOUDFLARED_VERSION,
    CloudflaredTunnel,
    ReceiverProxy,
    find_cloudflared,
    install_cloudflared,
)

if TYPE_CHECKING:
    import ssl
    from collections.abc import Callable, Generator, Iterator

RECEIVER = '/v1/connections/conn-proof/events'
MAX_BODY = 1_048_576
CONCURRENT_SLOTS = 32
SOCKET_DEADLINE_SECONDS = 5
NOT_FOUND_BODY = b'{"detail":"Not found"}'
UNSUPPORTED = (
    'Automatic cloudflared installation is not supported on this platform. '
    'Install an official compatible client on PATH.'
)
DOWNLOAD_FAILED = 'Could not download cloudflared from GitHub. Check network access and retry.'


# --- loopback receiver proxy -------------------------------------------------------------


@contextmanager
def upstream(payload: bytes = b'{}', status: int = 202) -> Generator[tuple[int, list[bytes]]]:
    """Run a local API stand-in that records each body it receives."""
    received: list[bytes] = []

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self) -> None:
            received.append(self.rfile.read(int(self.headers['Content-Length'])))
            self.send_response(status)
            self.send_header('Content-Length', str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)

        def log_message(self, format: str, *args: object) -> None:  # noqa: A002 - stdlib override.
            del format, args

    server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield server.server_port, received
    finally:
        server.shutdown()
        server.server_close()
        thread.join()


def _raw_exchange(port: int, data: bytes, *, half_close: bool = False) -> bytes:
    """Send raw bytes and read the whole reply, without any client-side normalization."""
    with socket.create_connection(('127.0.0.1', port), timeout=10) as connection:
        connection.sendall(data)
        if half_close:
            connection.shutdown(socket.SHUT_WR)
        chunks: list[bytes] = []
        while chunk := connection.recv(65_536):
            chunks.append(chunk)
    return b''.join(chunks)


def _status_and_body(reply: bytes) -> tuple[int, bytes]:
    head, _, body = reply.partition(b'\r\n\r\n')
    return int(head.split(b' ', 2)[1]), body


def _unused_port() -> int:
    with socket.socket() as probe:
        probe.bind(('127.0.0.1', 0))
        return int(probe.getsockname()[1])


@pytest.mark.parametrize('port', [0, 65_536])
def test_proxy_refuses_an_upstream_port_outside_the_tcp_range(port: int) -> None:
    """The fixed upstream must be a real TCP port; nothing is bound otherwise."""
    with pytest.raises(ValueError, match=r'^The local API port must be between 1 and 65535\.$'):
        _ = ReceiverProxy(['conn-proof'], upstream_port=port)


def test_proxy_accepts_both_ends_of_the_port_range() -> None:
    """Ports 1 and 65535 are valid upstreams, and paths follow the receiver grammar."""
    for port in (1, 65_535):
        proxy = ReceiverProxy(['conn-a', 'conn-b'], upstream_port=port)
        with proxy:
            assert proxy.paths == frozenset(
                {'/v1/connections/conn-a/events', '/v1/connections/conn-b/events'}
            )


def test_malformed_request_line_is_answered_by_the_http_layer_only() -> None:
    """A request the HTTP parser rejects never reaches the allowlist or the upstream."""
    with upstream() as (port, received), ReceiverProxy(['conn-proof'], upstream_port=port) as proxy:
        reply = _raw_exchange(
            proxy.port, b'POST /v1/connections/conn-proof/events HTTP/9.9\r\n\r\n'
        )
    assert b'Error code: 505' in reply
    assert NOT_FOUND_BODY not in reply
    assert received == []


@pytest.mark.parametrize(
    'headers',
    [
        b'Transfer-Encoding: chunked\r\n',
        b'Content-Length: 2\r\nContent-Length: 2\r\n',
        b'Content-Length: 1048577\r\n',
        b'Content-Length: two\r\n',
    ],
)
def test_refused_route_with_unusable_framing_still_gets_the_uniform_404(headers: bytes) -> None:
    """Ambiguous or oversized framing is never read on a refused route; the 404 is unchanged."""
    request = b'POST /v1/connections/conn-other/events HTTP/1.1\r\nHost: x\r\n' + headers + b'\r\n'
    with upstream() as (port, received), ReceiverProxy(['conn-proof'], upstream_port=port) as proxy:
        reply = _raw_exchange(proxy.port, request + b'{}')
    assert _status_and_body(reply) == (HTTPStatus.NOT_FOUND, NOT_FOUND_BODY)
    assert received == []


def test_refused_route_whose_body_never_arrives_still_gets_the_uniform_404() -> None:
    """A declared body that stalls is abandoned at the socket deadline, then refused."""
    request = (
        b'POST /v1/connections/conn-other/events HTTP/1.1\r\nHost: x\r\nContent-Length: 10\r\n\r\n'
    )
    with upstream() as (port, received), ReceiverProxy(['conn-proof'], upstream_port=port) as proxy:
        started = time.monotonic()
        reply = _raw_exchange(proxy.port, request)
        waited = time.monotonic() - started
    assert waited >= SOCKET_DEADLINE_SECONDS - 0.5
    assert _status_and_body(reply) == (HTTPStatus.NOT_FOUND, NOT_FOUND_BODY)
    assert received == []


@pytest.mark.parametrize('method', ['GET', 'PUT', 'DELETE'])
def test_other_methods_on_the_receiver_path_get_the_uniform_404(method: str) -> None:
    """Only POST is relayed; any other method on an allowed path looks like an unknown route."""
    request = f'{method} {RECEIVER} HTTP/1.1\r\nHost: x\r\nContent-Length: 2\r\n\r\n{{}}'.encode()
    with upstream() as (port, received), ReceiverProxy(['conn-proof'], upstream_port=port) as proxy:
        reply = _raw_exchange(proxy.port, request)
    assert _status_and_body(reply) == (HTTPStatus.NOT_FOUND, NOT_FOUND_BODY)
    assert received == []


@pytest.mark.parametrize('length', ['+2', '-1', '0x2', ' '])
def test_receiver_post_with_a_non_decimal_length_is_a_bad_request(length: str) -> None:
    """Only plain decimal Content-Length values are forwarded."""
    request = (
        f'POST {RECEIVER} HTTP/1.1\r\nHost: x\r\nContent-Length: {length}\r\n\r\n{{}}'
    ).encode()
    with upstream() as (port, received), ReceiverProxy(['conn-proof'], upstream_port=port) as proxy:
        reply = _raw_exchange(proxy.port, request)
    assert _status_and_body(reply)[0] == HTTPStatus.BAD_REQUEST
    assert received == []


def test_receiver_post_with_a_duplicated_length_is_a_bad_request() -> None:
    """Two Content-Length headers are ambiguous framing and are refused."""
    request = (
        f'POST {RECEIVER} HTTP/1.1\r\nHost: x\r\nContent-Length: 2\r\nContent-Length: 2\r\n\r\n{{}}'
    ).encode()
    with upstream() as (port, received), ReceiverProxy(['conn-proof'], upstream_port=port) as proxy:
        reply = _raw_exchange(proxy.port, request)
    assert _status_and_body(reply)[0] == HTTPStatus.BAD_REQUEST
    assert received == []


def test_receiver_post_with_a_truncated_body_is_a_bad_request() -> None:
    """A body shorter than its declared length is never forwarded."""
    request = f'POST {RECEIVER} HTTP/1.1\r\nHost: x\r\nContent-Length: 10\r\n\r\n{{}}'.encode()
    with upstream() as (port, received), ReceiverProxy(['conn-proof'], upstream_port=port) as proxy:
        reply = _raw_exchange(proxy.port, request, half_close=True)
    assert _status_and_body(reply)[0] == HTTPStatus.BAD_REQUEST
    assert received == []


def test_receiver_body_at_the_limit_is_forwarded_exactly() -> None:
    """Exactly 1 MiB is accepted and forwarded byte-for-byte."""
    body = b'x' * MAX_BODY
    with upstream() as (port, received), ReceiverProxy(['conn-proof'], upstream_port=port) as proxy:
        connection = http.client.HTTPConnection('127.0.0.1', proxy.port, timeout=10)
        try:
            connection.request('POST', RECEIVER, body=body)
            response = connection.getresponse()
            assert (response.status, response.read()) == (HTTPStatus.ACCEPTED, b'{}')
        finally:
            connection.close()
    assert received == [body]


@pytest.mark.parametrize(
    ('size', 'status'),
    [(MAX_BODY, HTTPStatus.ACCEPTED), (MAX_BODY + 1, HTTPStatus.BAD_GATEWAY)],
)
def test_upstream_reply_larger_than_the_limit_becomes_bad_gateway(size: int, status: int) -> None:
    """The local API's reply is relayed up to 1 MiB; anything larger is withheld."""
    with (
        upstream(payload=b'y' * size) as (port, received),
        ReceiverProxy(['conn-proof'], upstream_port=port) as proxy,
    ):
        connection = http.client.HTTPConnection('127.0.0.1', proxy.port, timeout=10)
        try:
            connection.request('POST', RECEIVER, body=b'{}')
            response = connection.getresponse()
            payload = response.read()
        finally:
            connection.close()
    assert response.status == status
    if status == HTTPStatus.ACCEPTED:
        assert payload == b'y' * size
        assert response.getheader('Content-Type') == 'application/json'
    else:
        assert b'yyyy' not in payload
    assert received == [b'{}']


def test_unreachable_local_api_becomes_bad_gateway() -> None:
    """A stopped local API is a 502, not a hang or a traceback."""
    with ReceiverProxy(['conn-proof'], upstream_port=_unused_port()) as proxy:
        connection = http.client.HTTPConnection('127.0.0.1', proxy.port, timeout=15)
        try:
            connection.request('POST', RECEIVER, body=b'{}')
            assert connection.getresponse().status == HTTPStatus.BAD_GATEWAY
        finally:
            connection.close()


def test_connections_beyond_the_concurrency_bound_are_closed_without_a_reply() -> None:
    """Thirty-two requests may be in flight; the next connection is dropped immediately."""
    with upstream() as (port, received), ReceiverProxy(['conn-proof'], upstream_port=port) as proxy:
        holders = [
            socket.create_connection(('127.0.0.1', proxy.port), timeout=10)
            for _ in range(CONCURRENT_SLOTS)
        ]
        try:
            for holder in holders:
                holder.sendall(b'POST ')
            time.sleep(0.5)
            try:
                excess = _raw_exchange(proxy.port, f'POST {RECEIVER} HTTP/1.1\r\n\r\n'.encode())
            except (ConnectionAbortedError, ConnectionResetError):
                # Closing a socket with unread input resets it on some stacks: still no reply.
                excess = b''
        finally:
            for holder in holders:
                holder.close()
        assert excess == b''
        time.sleep(0.3)
        connection = http.client.HTTPConnection('127.0.0.1', proxy.port, timeout=10)
        try:
            connection.request('POST', RECEIVER, body=b'{}')
            assert connection.getresponse().status == HTTPStatus.ACCEPTED
        finally:
            connection.close()
    assert received == [b'{}']


# --- cloudflared child ------------------------------------------------------------------


@pytest.fixture
def isolated_tools(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Private home, empty PATH and private temporary directory on a supported platform."""
    home = tmp_path / 'home'
    home.mkdir()
    temporary = tmp_path / 'temp'
    temporary.mkdir()
    monkeypatch.setenv('HOME', str(home))
    monkeypatch.setenv('USERPROFILE', str(home))
    monkeypatch.setenv('PATH', str(tmp_path / 'empty-path'))
    monkeypatch.setattr(tempfile, 'tempdir', str(temporary))
    monkeypatch.setattr(platform, 'system', lambda: 'Linux')
    monkeypatch.setattr(platform, 'machine', lambda: 'x86_64')
    return home


def _client_name() -> str:
    return 'cloudflared.exe' if sys.platform == 'win32' else 'cloudflared'


def _put_client_on_path(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    bin_dir = tmp_path / 'bin'
    bin_dir.mkdir()
    executable = bin_dir / _client_name()
    _ = executable.write_text('stand-in', encoding='utf-8')
    executable.chmod(0o700)
    monkeypatch.setenv('PATH', str(bin_dir))
    return executable


def _stand_in(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, script: str
) -> list[subprocess.Popen[str]]:
    """Run a Python script whenever the client on PATH is launched."""
    executable = _put_client_on_path(tmp_path, monkeypatch)
    source = tmp_path / 'stand_in.py'
    _ = source.write_text(script, encoding='utf-8')
    started: list[subprocess.Popen[str]] = []
    original = subprocess.Popen

    def launch(args: list[str], **options: object) -> subprocess.Popen[str]:
        assert Path(args[0]).name.lower() == executable.name
        process: subprocess.Popen[str] = original(  # pyright: ignore[reportCallIssue]
            [sys.executable, str(source), *args[1:]],
            **options,  # pyright: ignore[reportArgumentType]
        )
        started.append(process)
        return process

    monkeypatch.setattr(subprocess, 'Popen', launch)
    return started


def _leftover_configs(tmp_path: Path) -> list[str]:
    return sorted(path.name for path in (tmp_path / 'temp').glob('maivn-dev-tunnel-*'))


def test_tunnel_reports_liveness_and_ignores_later_urls(
    isolated_tools: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The first trycloudflare origin wins; `alive` follows the owned child."""
    del isolated_tools
    started = _stand_in(
        tmp_path,
        monkeypatch,
        'import sys, time\n'
        "print('noise https://evil.example.com', flush=True)\n"
        "print('https://first-proof.trycloudflare.com', flush=True)\n"
        "print('https://second-proof.trycloudflare.com', flush=True)\n"
        'sys.stdin.read()\n'
        'time.sleep(0.4)\n',
    )
    with CloudflaredTunnel(4321, startup_timeout=5) as tunnel:
        assert tunnel.public_url == 'https://first-proof.trycloudflare.com'
        assert tunnel.alive is True
        with pytest.raises(
            RuntimeError,
            match=r'^cloudflared exited \(code 0\); the temporary tunnel is closed\.$',
        ):
            tunnel.wait()
        assert tunnel.alive is False
    assert started[0].returncode == 0
    assert _leftover_configs(tmp_path) == []


def test_waiting_on_an_unstarted_tunnel_is_refused(
    isolated_tools: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A tunnel object that was never entered owns no child and cannot be waited on."""
    del isolated_tools
    _ = _put_client_on_path(tmp_path, monkeypatch)
    tunnel = CloudflaredTunnel(4321)

    assert tunnel.alive is False
    assert tunnel.public_url == ''
    with pytest.raises(RuntimeError, match=r'^The tunnel has not started\.$'):
        tunnel.wait()


def test_tunnel_that_cannot_launch_removes_its_private_config(
    isolated_tools: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A launch failure propagates unchanged and leaves no temporary configuration behind."""
    del isolated_tools
    _ = _put_client_on_path(tmp_path, monkeypatch)

    def refuse(args: list[str], **_options: object) -> subprocess.Popen[str]:
        message = f'cannot execute {Path(args[0]).name}'
        raise PermissionError(message)

    monkeypatch.setattr(subprocess, 'Popen', refuse)

    with pytest.raises(PermissionError, match=r'^cannot execute cloudflared'), CloudflaredTunnel(1):
        pytest.fail('a tunnel that never launched cannot be entered')
    assert _leftover_configs(tmp_path) == []


def test_tunnel_without_a_usable_temporary_directory_launches_nothing(
    isolated_tools: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Configuration isolation comes first: no private directory, no child process."""
    del isolated_tools
    started = _stand_in(tmp_path, monkeypatch, 'raise SystemExit(0)\n')
    monkeypatch.setattr(tempfile, 'tempdir', str(tmp_path / 'missing-temp'))

    with pytest.raises(FileNotFoundError), CloudflaredTunnel(1):
        pytest.fail('a tunnel without private configuration cannot be entered')
    assert started == []


# --- managed installation ---------------------------------------------------------------


def _managed(home: Path, asset: str, filename: str = 'cloudflared') -> Path:
    return home / '.maivn' / 'tools' / 'cloudflared' / CLOUDFLARED_VERSION / asset / filename


def _download(
    monkeypatch: pytest.MonkeyPatch,
    key: tuple[str, str],
    asset: str,
    handler: Callable[[httpx.Request], httpx.Response],
    digest: str,
) -> None:
    from maivn._internal import cloudflared_install  # noqa: PLC0415 - the pinned digest table

    monkeypatch.setitem(cloudflared_install.ASSETS, key, (asset, digest))
    original = httpx.Client

    def client(*, timeout: float, follow_redirects: bool, verify: ssl.SSLContext) -> httpx.Client:
        return original(
            transport=httpx.MockTransport(handler),
            timeout=timeout,
            follow_redirects=follow_redirects,
            verify=verify,
        )

    monkeypatch.setattr(httpx, 'Client', client)


def _tgz(members: dict[str, bytes]) -> bytes:
    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode='w:gz') as bundle:
        for name, content in members.items():
            member = tarfile.TarInfo(name)
            member.size = len(content)
            bundle.addfile(member, io.BytesIO(content))
    return buffer.getvalue()


def test_find_prefers_the_managed_client_over_path(
    isolated_tools: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A managed install for this platform wins; otherwise PATH is consulted."""
    on_path = _put_client_on_path(tmp_path, monkeypatch)
    assert find_cloudflared() is not None
    assert Path(str(find_cloudflared())).parent == on_path.parent

    managed = _managed(isolated_tools, 'cloudflared-linux-amd64')
    managed.parent.mkdir(parents=True)
    _ = managed.write_bytes(b'client')
    assert find_cloudflared() == str(managed)


def test_find_on_an_unsupported_platform_falls_back_to_path(
    isolated_tools: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """No managed asset exists for the platform, so only PATH can answer."""
    del isolated_tools
    monkeypatch.setattr(platform, 'machine', lambda: 'riscv64')
    assert find_cloudflared() is None

    on_path = _put_client_on_path(tmp_path, monkeypatch)
    assert Path(str(find_cloudflared())).parent == on_path.parent


@pytest.mark.parametrize(
    ('system', 'machine', 'pinned', 'asset'),
    [
        ('Linux', 'aarch64', 'arm64', 'cloudflared-linux-arm64'),
        ('Linux', 'armv7l', 'arm', 'cloudflared-linux-arm'),
        ('Linux', 'i686', '386', 'cloudflared-linux-386'),
        ('Linux', 'i386', '386', 'cloudflared-linux-386'),
        ('Windows', 'x86', '386', 'cloudflared-windows-386.exe'),
        ('Windows', 'AMD64', 'amd64', 'cloudflared-windows-amd64.exe'),
    ],
)
def test_machine_aliases_select_the_pinned_asset(  # noqa: PLR0913 - platform matrix plus fixtures.
    system: str,
    machine: str,
    pinned: str,
    asset: str,
    isolated_tools: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Common machine spellings map onto the official release asset names."""
    monkeypatch.setattr(platform, 'system', lambda: system)
    monkeypatch.setattr(platform, 'machine', lambda: machine)
    requested: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requested.append(request.url.path)
        return httpx.Response(200, content=b'bytes')

    _download(
        monkeypatch,
        (system, pinned),
        asset,
        handler,
        hashlib.sha256(b'bytes').hexdigest(),
    )
    filename = 'cloudflared.exe' if asset.endswith('.exe') else 'cloudflared'

    assert install_cloudflared() == _managed(isolated_tools, asset, filename)
    assert requested == [f'/cloudflare/cloudflared/releases/download/{CLOUDFLARED_VERSION}/{asset}']


def test_darwin_archive_installs_only_its_executable(
    isolated_tools: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Darwin assets are archives: the single cloudflared member becomes the client."""
    monkeypatch.setattr(platform, 'system', lambda: 'Darwin')
    monkeypatch.setattr(platform, 'machine', lambda: 'arm64')
    archive = _tgz({'./cloudflared': b'darwin client', 'README': b'ignored'})
    _download(
        monkeypatch,
        ('Darwin', 'arm64'),
        'cloudflared-darwin-arm64.tgz',
        lambda _request: httpx.Response(200, content=archive),
        hashlib.sha256(archive).hexdigest(),
    )

    installed = install_cloudflared()

    assert installed == _managed(isolated_tools, 'cloudflared-darwin-arm64.tgz')
    assert installed.read_bytes() == b'darwin client'
    assert sorted(path.name for path in installed.parent.iterdir()) == ['cloudflared']


def test_corrupt_verified_archive_is_reported_as_a_failed_download(
    isolated_tools: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """An archive that is not gzip, even with a matching digest, installs nothing."""
    monkeypatch.setattr(platform, 'system', lambda: 'Darwin')
    monkeypatch.setattr(platform, 'machine', lambda: 'x86_64')
    _download(
        monkeypatch,
        ('Darwin', 'amd64'),
        'cloudflared-darwin-amd64.tgz',
        lambda _request: httpx.Response(200, content=b'not an archive'),
        hashlib.sha256(b'not an archive').hexdigest(),
    )

    with pytest.raises(RuntimeError, match=f'^{DOWNLOAD_FAILED}$'):
        _ = install_cloudflared()
    folder = _managed(isolated_tools, 'cloudflared-darwin-amd64.tgz').parent
    assert list(folder.iterdir()) == []


def test_http_error_from_the_release_host_is_a_failed_download(
    isolated_tools: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A 404 from GitHub is reported without its body and leaves nothing installed."""
    _download(
        monkeypatch,
        ('Linux', 'amd64'),
        'cloudflared-linux-amd64',
        lambda _request: httpx.Response(404, content=b'private-body'),
        '0' * 64,
    )

    with pytest.raises(RuntimeError, match=f'^{DOWNLOAD_FAILED}$'):
        _ = install_cloudflared()
    assert list(_managed(isolated_tools, 'cloudflared-linux-amd64').parent.iterdir()) == []


def test_redirect_to_plain_http_is_refused(
    isolated_tools: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A release download that ends on HTTP is refused before any byte is written."""

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.scheme == 'https':
            return httpx.Response(302, headers={'Location': 'http://mirror.invalid/client'})
        return httpx.Response(200, content=b'bytes')

    _download(
        monkeypatch,
        ('Linux', 'amd64'),
        'cloudflared-linux-amd64',
        handler,
        hashlib.sha256(b'bytes').hexdigest(),
    )

    with pytest.raises(RuntimeError, match=r'^cloudflared download requires HTTPS\.$'):
        _ = install_cloudflared()
    assert list(_managed(isolated_tools, 'cloudflared-linux-amd64').parent.iterdir()) == []


def test_download_past_its_time_budget_is_abandoned(
    isolated_tools: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A transfer outliving the two-minute budget installs nothing and says to retry."""
    ticks = iter([0.0, 121.0])

    def chunks() -> Iterator[bytes]:
        yield b'first'
        yield b'second'

    from maivn._internal import cloudflared_install  # noqa: PLC0415 - its clock

    monkeypatch.setattr(cloudflared_install.time, 'monotonic', lambda: next(ticks, 121.0))
    _download(
        monkeypatch,
        ('Linux', 'amd64'),
        'cloudflared-linux-amd64',
        lambda _request: httpx.Response(200, content=chunks()),
        '0' * 64,
    )

    with pytest.raises(
        RuntimeError,
        match=r'^cloudflared download exceeded its size or time limit\. Try again\.$',
    ):
        _ = install_cloudflared()
    assert list(_managed(isolated_tools, 'cloudflared-linux-amd64').parent.iterdir()) == []


def _link_directory(link: Path, target: Path) -> None:
    if os.name == 'nt':
        executable = Path(os.environ['SYSTEMROOT']) / 'System32' / 'cmd.exe'
        result = subprocess.run(  # noqa: S603 - local junction fixture on isolated paths.
            [str(executable), '/c', 'mklink', '/J', str(link), str(target)],
            capture_output=True,
            check=False,
        )
        assert result.returncode == 0
    else:
        link.symlink_to(target, target_is_directory=True)


def test_install_refuses_a_linked_tools_directory(
    isolated_tools: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Managed storage under a symlink or junction is refused before anything is written."""
    elsewhere = tmp_path / 'elsewhere'
    elsewhere.mkdir()
    _link_directory(isolated_tools / '.maivn', elsewhere)

    def unexpected(_request: httpx.Request) -> httpx.Response:
        pytest.fail('downloaded through a linked tools directory')

    _download(monkeypatch, ('Linux', 'amd64'), 'cloudflared-linux-amd64', unexpected, '0' * 64)

    with pytest.raises(
        RuntimeError, match=r'^cloudflared tools storage cannot use symlinks or junctions\.$'
    ):
        _ = install_cloudflared()
    assert list(elsewhere.iterdir()) == []
