"""A bounded loopback receiver proxy for temporary developer tunnels.

The public request never selects an upstream. Only explicitly selected connection
POST paths can reach the local mAIvn API; its signature checks stay authoritative.
"""

from __future__ import annotations

import http.client
import os
import re
import subprocess
import time
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from queue import Empty, Queue
from tempfile import TemporaryDirectory
from threading import BoundedSemaphore, Thread
from typing import TYPE_CHECKING, cast

from maivn._internal.cloudflared_install import find_cloudflared
from maivn._internal.config import LOCAL_BASE_URL

if TYPE_CHECKING:
    import socket
    from collections.abc import Sequence
    from types import TracebackType

    from typing_extensions import Self

_CONNECTION_ID = re.compile(r'[A-Za-z0-9][A-Za-z0-9._:-]{0,199}')
_MAX_BODY = 1_048_576
_MAX_PORT = 65535
LOCAL_API_PORT = int(LOCAL_BASE_URL.rsplit(':', 1)[1])
# The API origin's own answer for a path it does not serve.
_NOT_FOUND_BODY = b'{"detail":"Not found"}'
_HOP_HEADERS = frozenset(
    {
        'connection',
        'keep-alive',
        'proxy-authenticate',
        'proxy-authorization',
        'te',
        'trailer',
        'transfer-encoding',
        'upgrade',
        'host',
        'content-length',
        'authorization',
        'cookie',
        'forwarded',
    }
)
_PUBLIC_URL = re.compile(
    r'https://[a-z0-9](?:[a-z0-9-]*[a-z0-9])?\.trycloudflare\.com(?![a-z0-9.:-])'
)
_INSTALL_HELP = (
    'cloudflared is not installed. Install the open-source client from '
    'https://developers.cloudflare.com/cloudflare-one/networks/connectors/cloudflare-tunnel/downloads/'
    ' and make cloudflared available on PATH. No Cloudflare account is needed.'
)


class _ReceiverServer(ThreadingHTTPServer):
    """Bound concurrent requests and give each accepted socket a read deadline."""

    def __init__(self, paths: frozenset[str], upstream_port: int) -> None:
        self.paths = paths
        self.upstream_port = upstream_port
        self.slots = BoundedSemaphore(32)
        super().__init__(('127.0.0.1', 0), _ReceiverHandler)

    def process_request(
        self, request: socket.socket | tuple[bytes, socket.socket], client_address: tuple[str, int]
    ) -> None:
        """Refuse excess connections before creating another thread."""
        if not self.slots.acquire(blocking=False):
            self.shutdown_request(request)
            return
        cast('socket.socket', request).settimeout(5)
        try:
            super().process_request(request, client_address)
        except BaseException:
            self.slots.release()
            raise

    def process_request_thread(
        self, request: socket.socket | tuple[bytes, socket.socket], client_address: tuple[str, int]
    ) -> None:
        """Release the concurrency slot even for malformed or disconnected clients."""
        try:
            super().process_request_thread(request, client_address)
        finally:
            self.slots.release()

    def handle_error(
        self, request: socket.socket | tuple[bytes, socket.socket], client_address: tuple[str, int]
    ) -> None:
        """Never print request errors, which may contain provider data."""


class _ReceiverHandler(BaseHTTPRequestHandler):
    """Forward exact receiver requests without decoding their payloads."""

    raw_requestline: bytes

    def parse_request(self) -> bool:
        """Check the original target, including leading slashes normalized by stdlib."""
        if not super().parse_request():
            return False
        raw_target = self.raw_requestline.split()[1].decode('iso-8859-1')
        server = cast('_ReceiverServer', self.server)
        if self.command != 'POST' or raw_target not in server.paths:
            self._discard_refused_body()
            self._send_not_found()
            return False
        return True

    def _discard_refused_body(self) -> None:
        """Consume bounded framing before closing so late body bytes cannot reset the reply."""
        lengths = self.headers.get_all('Content-Length', [])
        if (
            self.headers.get('Transfer-Encoding')
            or len(lengths) != 1
            or re.fullmatch(r'[0-9]{1,10}', lengths[0]) is None
            or int(lengths[0]) > _MAX_BODY
        ):
            return
        try:
            self.rfile.read(int(lengths[0]))
        except OSError:
            # The existing socket deadline bounds incomplete refused requests.
            # Their data never reaches an upstream or a log.
            return

    def _send_not_found(self) -> None:
        """Match the API's refusal without revealing the local allowlist."""
        self.close_connection = True
        self.send_response(HTTPStatus.NOT_FOUND)
        self.send_header('Content-Type', 'application/json')
        self.send_header('Content-Length', str(len(_NOT_FOUND_BODY)))
        self.end_headers()
        if self.command != 'HEAD':
            self.wfile.write(_NOT_FOUND_BODY)

    def do_POST(self) -> None:
        """Forward bounded raw bytes to the fixed local API socket."""
        lengths = self.headers.get_all('Content-Length', [])
        if self.headers.get('Transfer-Encoding') or len(lengths) != 1:
            self.send_error(HTTPStatus.BAD_REQUEST)
            return
        if not re.fullmatch(r'[0-9]{1,10}', lengths[0]):
            self.send_error(HTTPStatus.BAD_REQUEST)
            return
        length = int(lengths[0])
        if length > _MAX_BODY:
            self.send_error(HTTPStatus.REQUEST_ENTITY_TOO_LARGE)
            return
        body = self.rfile.read(length)
        if len(body) != length:
            self.send_error(HTTPStatus.BAD_REQUEST)
            return
        self._forward(body)

    def _forward(self, body: bytes) -> None:
        server = cast('_ReceiverServer', self.server)
        connection = http.client.HTTPConnection('127.0.0.1', server.upstream_port, timeout=10)
        try:
            connection.putrequest('POST', self.path, skip_accept_encoding=True)
            for name, value in self.headers.items():
                lowered = name.lower()
                if lowered not in _HOP_HEADERS and not lowered.startswith('x-forwarded-'):
                    connection.putheader(name, value)
            connection.putheader('Content-Length', str(len(body)))
            connection.endheaders(body)
            response = connection.getresponse()
            # HTTPConnection never follows redirects; do not send the caller elsewhere either.
            if HTTPStatus.MULTIPLE_CHOICES <= response.status < HTTPStatus.BAD_REQUEST:
                self.send_error(HTTPStatus.BAD_GATEWAY)
                return
            payload = response.read(_MAX_BODY + 1)
            if len(payload) > _MAX_BODY:
                self.send_error(HTTPStatus.BAD_GATEWAY)
                return
            self.send_response(response.status)
            self.send_header('Content-Type', response.getheader('Content-Type', 'application/json'))
            self.send_header('Content-Length', str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)
        except (OSError, http.client.HTTPException):
            self.send_error(HTTPStatus.BAD_GATEWAY)
        finally:
            connection.close()

    def log_message(self, format: str, *args: object) -> None:  # noqa: A002
        """Suppress access/error logs so provider bodies and tokens cannot leak."""


class ReceiverProxy:
    """Own a loopback-only listener exposing selected connection receiver paths."""

    def __init__(
        self, connection_ids: Sequence[str], *, upstream_port: int = LOCAL_API_PORT
    ) -> None:
        if not connection_ids or any(not _CONNECTION_ID.fullmatch(item) for item in connection_ids):
            message = (
                'Provide connection IDs of 1-200 characters, starting with an ASCII letter '
                'or digit and containing only ASCII letters, digits, ., _, : and -.'
            )
            raise ValueError(message)
        if not 1 <= upstream_port <= _MAX_PORT:
            message = 'The local API port must be between 1 and 65535.'
            raise ValueError(message)
        self.paths = frozenset(f'/v1/connections/{item}/events' for item in connection_ids)
        self._server = _ReceiverServer(self.paths, upstream_port)
        self.port = self._server.server_port
        self._thread = Thread(target=self._server.serve_forever, name='maivn-receiver-proxy')

    def __enter__(self) -> Self:
        """Start accepting requests on the loopback listener."""
        self._thread.start()
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        """Close the listener and wait for bounded in-flight requests to finish."""
        self._server.shutdown()
        self._server.server_close()
        self._thread.join()


def require_cloudflared() -> str:
    """Resolve the installed client or explain how to install it without side effects."""
    executable = find_cloudflared()
    if executable is None:
        message = f'{_INSTALL_HELP} Or run: maivn dev-tunnel --install'
        raise RuntimeError(message)
    return executable


class CloudflaredTunnel:
    """Own one installed Quick Tunnel child without inheriting tunnel configuration."""

    def __init__(self, proxy_port: int, *, startup_timeout: float = 45) -> None:
        self._executable = require_cloudflared()
        self._proxy_port = proxy_port
        self._startup_timeout = startup_timeout
        self._temporary: TemporaryDirectory[str] | None = None
        self._process: subprocess.Popen[str] | None = None
        self._reader: Thread | None = None
        self._urls: Queue[str] = Queue()
        self.public_url = ''

    def __enter__(self) -> Self:
        """Start the child and obtain its temporary HTTPS receiver origin."""
        try:
            self._temporary = TemporaryDirectory(prefix='maivn-dev-tunnel-')
            config = Path(self._temporary.name) / 'config.yml'
            config.write_text('{}\n', encoding='utf-8')
            # Only OS discovery/runtime variables: never API keys, provider credentials,
            # TUNNEL_* overrides, log destinations, or named-tunnel tokens.
            environment = {
                key: value
                for key, value in os.environ.items()
                if key.upper()
                in {
                    'PATH',
                    'SYSTEMROOT',
                    'WINDIR',
                    'HOME',
                    'USERPROFILE',
                    'TMP',
                    'TEMP',
                    'LOCALAPPDATA',
                    'APPDATA',
                }
            }
            self._process = subprocess.Popen(  # noqa: S603 - fixed argv, resolved local executable
                [
                    self._executable,
                    'tunnel',
                    '--config',
                    str(config),
                    '--no-autoupdate',
                    '--url',
                    f'http://127.0.0.1:{self._proxy_port}',
                    '--loglevel',
                    'info',
                ],
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                encoding='utf-8',
                errors='replace',
                env=environment,
                creationflags=subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0,
            )
            self._reader = Thread(target=self._read_url, name='maivn-tunnel-output')
            self._reader.start()
            self.public_url = self._await_url()
        except BaseException:
            self._close()
            raise
        else:
            return self

    def _read_url(self) -> None:
        process = self._process
        if process is None or process.stdout is None:
            return
        emitted = False
        while line := process.stdout.readline(8192):
            match = _PUBLIC_URL.search(line)
            if match is not None and not emitted:
                self._urls.put(match.group())
                emitted = True
            # Discard all raw process output, including any request/provider diagnostics.

    def _await_url(self) -> str:
        deadline = time.monotonic() + self._startup_timeout
        while time.monotonic() < deadline:
            if self._process is None or self._process.poll() is not None:
                message = (
                    'cloudflared exited before opening a tunnel. Check network access and version.'
                )
                raise RuntimeError(message)
            try:
                return self._urls.get(timeout=0.1)
            except Empty:
                continue
        message = 'cloudflared startup timed out. Check network access and try again.'
        raise RuntimeError(message)

    def wait(self) -> None:
        """Remain in the foreground until Ctrl-C or the tunnel process exits."""
        if self._process is None:
            message = 'The tunnel has not started.'
            raise RuntimeError(message)
        # Dispatch Ctrl-C outside Popen.wait: on Windows even its timed form can
        # enter a second native wait from subprocess's own interrupt handler.
        while self._process.poll() is None:
            time.sleep(0.2)
        code = self._process.wait(timeout=0.2)
        message = f'cloudflared exited (code {code}); the temporary tunnel is closed.'
        raise RuntimeError(message)

    @property
    def alive(self) -> bool:
        """Whether this instance still owns a running cloudflared process."""
        return self._process is not None and self._process.poll() is None

    def _close(self) -> None:
        process = self._process
        if process is not None:
            if process.poll() is None:
                process.terminate()
                try:
                    process.wait(timeout=3)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait(timeout=3)
            if self._reader is not None:
                self._reader.join(timeout=3)
            if process.stdout is not None:
                process.stdout.close()
        if self._temporary is not None:
            self._temporary.cleanup()

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        """Terminate only this owned child and remove its temporary configuration."""
        self._close()
