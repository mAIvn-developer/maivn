"""Exercise the public receiver proxy with real loopback HTTP traffic."""

from __future__ import annotations

import _thread
import http.client
import json
import os
import socket
import subprocess
import sys
import time
from contextlib import contextmanager
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from threading import Thread, Timer
from typing import TYPE_CHECKING

import pytest

from maivn import cli
from maivn._internal import dev_tunnel
from maivn._internal.dev_tunnel import CloudflaredTunnel, ReceiverProxy

INTERRUPTED_EXIT_CODE = 130
INTERRUPT_DISPATCH_LIMIT_SECONDS = 1.5

if TYPE_CHECKING:
    from collections.abc import Generator


def test_tunnel_help_needs_neither_studio_nor_cloudflared(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """The CLI must offer a discoverable tunnel command without opening a tunnel."""
    monkeypatch.setattr('sys.argv', ['maivn', 'dev-tunnel', '--help'])
    cli.main()
    output = capsys.readouterr().out
    assert '--connection-id' in output
    assert 'Ctrl-C' in output


@contextmanager
def upstream_server(
    status: int = 202,
    payload: bytes = b'{}',
) -> Generator[tuple[int, list[tuple[str, bytes, dict[str, str]]]]]:
    """Capture real requests without logging bodies or headers."""
    received: list[tuple[str, bytes, dict[str, str]]] = []

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self) -> None:
            received.append(
                (
                    self.path,
                    self.rfile.read(int(self.headers['Content-Length'])),
                    dict(self.headers),
                )
            )
            self.send_response(status)
            self.send_header('Content-Type', 'application/json')
            self.send_header('Content-Length', str(len(payload)))
            if status == HTTPStatus.FOUND:
                self.send_header('Location', 'http://169.254.169.254/latest/meta-data/')
            self.end_headers()
            self.wfile.write(payload)

        def log_message(self, format: str, *args: object) -> None:  # noqa: A002
            pass

    server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield server.server_port, received
    finally:
        server.shutdown()
        server.server_close()
        thread.join()


def request(
    port: int,
    target: str,
    *,
    method: str = 'POST',
    body: bytes = b'{}',
    headers: dict[str, str] | None = None,
) -> tuple[int, bytes]:
    """Send an HTTP request without URL normalization or redirects."""
    connection = http.client.HTTPConnection('127.0.0.1', port, timeout=3)
    try:
        connection.request(method, target, body=body, headers=headers or {})
        response = connection.getresponse()
        return response.status, response.read()
    finally:
        connection.close()


def test_signed_bytes_reach_only_the_named_connection(capsys: pytest.CaptureFixture[str]) -> None:
    """JSON reserialization or header rewriting would invalidate the provider signature."""
    raw = b'{ "text": "escaped\\n", "value": [1,2] }\r\n'
    with (
        upstream_server() as (upstream, received),
        ReceiverProxy(['conn-proof'], upstream_port=upstream) as proxy,
    ):
        result = request(
            proxy.port,
            '/v1/connections/conn-proof/events',
            body=raw,
            headers={
                'Content-Type': 'application/json',
                'X-Slack-Signature': 'v0=test-value',
                'X-Slack-Request-Timestamp': '1788580000',
                'X-Hub-Signature-256': 'sha256=test',
                'X-GitHub-Delivery': 'delivery-proof',
                'X-Maivn-Signature': 'test-house-signature',
                'Host': '169.254.169.254',
                'Authorization': 'Bearer private-local-fixture',
                'Cookie': 'private-local-fixture',
                'X-Forwarded-Host': '169.254.169.254',
            },
        )
    assert result == (202, b'{}')
    assert received[0][0:2] == ('/v1/connections/conn-proof/events', raw)
    assert received[0][2]['X-Slack-Signature'] == 'v0=test-value'
    assert received[0][2]['X-Hub-Signature-256'] == 'sha256=test'
    assert received[0][2]['X-Maivn-Signature'] == 'test-house-signature'
    assert received[0][2]['Host'].startswith('127.0.0.1:')
    assert 'Authorization' not in received[0][2]
    assert 'Cookie' not in received[0][2]
    assert 'X-Forwarded-Host' not in received[0][2]
    assert capsys.readouterr() == ('', '')


@pytest.mark.parametrize(
    'target',
    [
        '/',
        '/api/platform/login',
        '/v1/auth/login',
        '/v1/connections/conn-other/events',
        '/v1/connections/conn-proof/events?url=http://169.254.169.254',
        '//v1/connections/conn-proof/events',
        '/v1/connections/conn-proof/events/',
        '/v1/connections/%63onn-proof/events',
        '/v1/connections/../conn-proof/events',
        '/v1/connections/conn-proof%2fevents',
        '/v1/connections/conn-proof\\events',
        'http://169.254.169.254/v1/connections/conn-proof/events',
    ],
)
def test_path_tricks_never_reach_upstream(target: str) -> None:
    """Raw-target allowlisting must precede any HTTP library normalization."""
    with (
        upstream_server() as (upstream, received),
        ReceiverProxy(['conn-proof'], upstream_port=upstream) as proxy,
    ):
        assert request(proxy.port, target)[0] == HTTPStatus.NOT_FOUND
    assert received == []


@pytest.mark.parametrize(
    ('method', 'target'),
    [
        ('POST', '/v1/connections/conn-unknown/events'),
        ('POST', '/v1/auth/login'),
        ('GET', '/v1/connections/conn-proof/events'),
        ('POST', '//v1/connections/conn-proof/events'),
    ],
)
def test_local_404_matches_receiver_signature_refusal_bytes(method: str, target: str) -> None:
    """Route probing must not distinguish a local rejection from the API's uniform refusal."""
    refusal = b'{"detail":"Not found"}'
    with (
        upstream_server(HTTPStatus.NOT_FOUND, refusal) as (upstream, received),
        ReceiverProxy(['conn-proof'], upstream_port=upstream) as proxy,
    ):
        signature_refusal = request(proxy.port, '/v1/connections/conn-proof/events')
        denied = request(proxy.port, target, method=method)
        assert denied == signature_refusal == (HTTPStatus.NOT_FOUND, refusal)
        connection = http.client.HTTPConnection('127.0.0.1', proxy.port, timeout=3)
        try:
            connection.request(method, target, body=b'{}')
            response = connection.getresponse()
            assert response.getheader('Content-Type') == 'application/json'
            response.read()
        finally:
            connection.close()
    assert len(received) == 1


@pytest.mark.parametrize('method', ['GET', 'HEAD', 'PUT', 'DELETE', 'OPTIONS', 'PATCH', 'CONNECT'])
def test_other_methods_never_reach_upstream(method: str) -> None:
    """A receiver tunnel must never become a management or generic HTTP proxy."""
    with (
        upstream_server() as (upstream, received),
        ReceiverProxy(['conn-proof'], upstream_port=upstream) as proxy,
    ):
        assert (
            request(proxy.port, '/v1/connections/conn-proof/events', method=method)[0]
            == HTTPStatus.NOT_FOUND
        )
    assert received == []


def test_oversized_and_ambiguous_bodies_are_refused() -> None:
    """A large Content-Length or transfer encoding must not allocate/forward a body."""
    with (
        upstream_server() as (upstream, received),
        ReceiverProxy(['conn-proof'], upstream_port=upstream) as proxy,
    ):
        assert (
            request(
                proxy.port,
                '/v1/connections/conn-proof/events',
                headers={
                    'Content-Length': '1048577',
                },
            )[0]
            == HTTPStatus.REQUEST_ENTITY_TOO_LARGE
        )
        assert (
            request(
                proxy.port,
                '/v1/connections/conn-proof/events',
                headers={
                    'Transfer-Encoding': 'chunked',
                },
            )[0]
            == HTTPStatus.BAD_REQUEST
        )
    assert received == []


def test_upstream_redirect_cannot_redirect_the_receiver() -> None:
    """Even a compromised local receiver cannot make the proxy follow an arbitrary URL."""
    with (
        upstream_server(302) as (upstream, received),
        ReceiverProxy(['conn-proof'], upstream_port=upstream) as proxy,
    ):
        assert request(proxy.port, '/v1/connections/conn-proof/events')[0] == HTTPStatus.BAD_GATEWAY
    assert len(received) == 1


def test_rejected_fragmented_post_returns_complete_refusal() -> None:
    """A body arriving after headers must not reset the 404 response on Windows."""
    with (
        upstream_server() as (upstream, received),
        ReceiverProxy(['conn-proof'], upstream_port=upstream) as proxy,
    ):
        connection = http.client.HTTPConnection('127.0.0.1', proxy.port, timeout=2)
        try:
            connection.putrequest('POST', '/v1/connections/conn-other/events')
            connection.putheader('Content-Length', '2')
            connection.endheaders()
            time.sleep(0.05)
            connection.send(b'{}')
            response = connection.getresponse()
            assert response.status == HTTPStatus.NOT_FOUND
            assert json.loads(response.read()) == {'detail': 'Not found'}
        finally:
            connection.close()
    assert received == []


def test_proxy_stop_releases_listener() -> None:
    """Leaving the lifecycle context must remove the local listener."""
    with ReceiverProxy(['conn-proof']) as proxy:
        port = proxy.port
        assert request(port, '/')[0] == HTTPStatus.NOT_FOUND
    with socket.socket() as connection:
        connection.settimeout(1)
        assert connection.connect_ex(('127.0.0.1', port)) != 0


@pytest.mark.parametrize('identifiers', [[], ['../x'], ['x/events'], ['x?z'], ['é'], ['x' * 201]])
def test_invalid_connection_identifiers_cannot_open_a_listener(identifiers: list[str]) -> None:
    """Operator input cannot expand the allowed path grammar."""
    with pytest.raises(ValueError, match='connection'):
        ReceiverProxy(identifiers)


@pytest.mark.parametrize('connection_id', ['conn.local:github', 'x' * 129, 'x' * 200])
def test_existing_connection_ids_reach_their_exact_receiver(connection_id: str) -> None:
    """Every safe ID the API accepts must remain usable through the tunnel."""
    target = f'/v1/connections/{connection_id}/events'
    with (
        upstream_server() as (upstream, received),
        ReceiverProxy([connection_id], upstream_port=upstream) as proxy,
    ):
        assert request(proxy.port, target) == (202, b'{}')
    assert received[0][0] == target


def fake_cloudflared(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, script: str
) -> list[subprocess.Popen[str]]:
    """Replace only the external binary while exercising real subprocess cleanup."""
    executable = tmp_path / 'fake_cloudflared.py'
    executable.write_text(script, encoding='utf-8')
    processes: list[subprocess.Popen[str]] = []
    original = subprocess.Popen

    def launch(  # noqa: PLR0913 - the external Popen keyword contract is exercised intact
        args: list[str],
        *,
        stdin: int,
        stdout: int,
        stderr: int,
        text: bool,
        encoding: str,
        errors: str,
        env: dict[str, str],
        creationflags: int,
    ) -> subprocess.Popen[str]:
        if os.name == 'nt':
            assert creationflags & subprocess.CREATE_NO_WINDOW
        process = original(
            [sys.executable, str(executable), *args[1:]],
            stdin=stdin,
            stdout=stdout,
            stderr=stderr,
            text=text,
            encoding=encoding,
            errors=errors,
            env=env,
            creationflags=creationflags,
        )
        processes.append(process)
        return process

    def locate() -> str:
        return sys.executable

    monkeypatch.setattr(dev_tunnel, 'find_cloudflared', locate)
    monkeypatch.setattr(dev_tunnel.subprocess, 'Popen', launch)
    return processes


def test_cloudflared_stop_cleans_child_and_private_config(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """Exiting the context must terminate its child and remove its isolated config."""
    monkeypatch.setenv('MAIVN_API_KEY', 'test-only-key-not-for-cloudflared')
    monkeypatch.setenv('TUNNEL_URL', 'http://169.254.169.254')
    monkeypatch.setenv('TUNNEL_TOKEN', 'test-only-token-not-for-cloudflared')
    processes = fake_cloudflared(
        monkeypatch,
        tmp_path,
        """
import os, pathlib, sys, time
assert 'MAIVN_API_KEY' not in os.environ
assert 'TUNNEL_URL' not in os.environ
assert 'TUNNEL_TOKEN' not in os.environ
config = pathlib.Path(sys.argv[sys.argv.index('--config') + 1])
assert config.read_text().strip() == '{}'
assert sys.argv[sys.argv.index('--url') + 1] == 'http://127.0.0.1:12345'
print('private-provider-token never-display-this', flush=True)
print('https://proof-local-only.trycloudflare.com', flush=True)
time.sleep(60)
""",
    )
    with CloudflaredTunnel(12345, startup_timeout=3) as tunnel:
        assert tunnel.public_url == 'https://proof-local-only.trycloudflare.com'
        assert processes[0].poll() is None
        arguments = processes[0].args
        assert isinstance(arguments, list)
        config = Path(str(arguments[arguments.index('--config') + 1]))
        assert config.exists()
    assert processes[0].poll() is not None
    assert not config.exists()
    assert capsys.readouterr() == ('', '')


@pytest.mark.parametrize(
    ('script', 'expected'),
    [
        ("import sys; print('private-token'); sys.exit(7)", 'exited'),
        ('import time; time.sleep(60)', 'timed out'),
    ],
)
def test_cloudflared_failed_start_cleans_child(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    script: str,
    expected: str,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Failure before startup must clean up too, without echoing untrusted process logs."""
    processes = fake_cloudflared(monkeypatch, tmp_path, script)
    with pytest.raises(RuntimeError, match=expected), CloudflaredTunnel(12345, startup_timeout=0.5):
        pytest.fail('A failed startup cannot report a usable public URL')
    assert processes[0].poll() is not None
    assert 'private-token' not in str(capsys.readouterr())


def test_missing_cloudflared_explains_install_without_opening_proxy(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """Missing prerequisites must produce an actionable error, never auto-install."""

    def locate() -> None:
        return None

    monkeypatch.setattr(dev_tunnel, 'find_cloudflared', locate)
    monkeypatch.setattr('sys.argv', ['maivn', 'dev-tunnel', '--connection-id', 'conn-proof'])
    with pytest.raises(SystemExit) as result:
        cli.main()
    assert result.value.code == 1
    output = capsys.readouterr().err
    assert 'cloudflared' in output
    assert 'https://' in output


def test_ctrl_c_closes_both_cli_resources(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """A CLI interrupt must unwind both the real child and loopback listener."""
    processes = fake_cloudflared(
        monkeypatch,
        tmp_path,
        ("import time; print('https://proof.trycloudflare.com', flush=True); time.sleep(60)"),
    )

    def interrupt_wait(_self: CloudflaredTunnel) -> None:
        raise KeyboardInterrupt

    monkeypatch.setattr(CloudflaredTunnel, 'wait', interrupt_wait)
    monkeypatch.setattr('sys.argv', ['maivn', 'dev-tunnel', '--connection-id', 'conn-proof'])
    with pytest.raises(SystemExit) as result:
        cli.main()
    assert result.value.code == INTERRUPTED_EXIT_CODE
    assert processes[0].poll() is not None
    arguments = processes[0].args
    assert isinstance(arguments, list)
    proxy_origin = str(arguments[arguments.index('--url') + 1])
    port = int(proxy_origin.rsplit(':', 1)[1])
    with socket.socket() as connection:
        connection.settimeout(1)
        assert connection.connect_ex(('127.0.0.1', port)) != 0
    output = capsys.readouterr()
    assert 'https://proof.trycloudflare.com/v1/connections/conn-proof/events' in output.out
    assert 'stopped' in output.err


def test_wait_dispatches_interrupt_before_child_exits(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """Windows must dispatch a pending interrupt while cloudflared is still running."""
    processes = fake_cloudflared(
        monkeypatch,
        tmp_path,
        ("import time; print('https://proof.trycloudflare.com', flush=True); time.sleep(3)"),
    )
    with CloudflaredTunnel(12345, startup_timeout=3) as tunnel:
        interrupt = Timer(0.2, _thread.interrupt_main)
        started = time.monotonic()
        interrupt.start()
        try:
            with pytest.raises(KeyboardInterrupt):
                tunnel.wait()
        finally:
            interrupt.cancel()
            interrupt.join()
        # The child is deliberately still alive: the signal, not child exit, must wake wait().
        assert processes[0].poll() is None
        assert time.monotonic() - started < INTERRUPT_DISPATCH_LIMIT_SECONDS
    assert processes[0].poll() is not None
