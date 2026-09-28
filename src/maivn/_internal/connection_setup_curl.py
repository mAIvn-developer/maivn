"""Local HTTP transport using curl's normal cookie engine and private ephemeral state."""

from __future__ import annotations

import os
import shutil
import subprocess
import tempfile
import threading
from contextlib import suppress
from pathlib import Path
from typing import TYPE_CHECKING
from uuid import uuid4

from maivn._internal.connection_setup_file import PrivateSetupFile
from maivn._internal.local_file_security import private_directory, private_file

if TYPE_CHECKING:
    from collections.abc import Mapping

PROCESS_TIMEOUT_SECONDS = 18
STATUS_MARKER = b'\nMAIVN_HTTP_STATUS:'
MAX_STATUS_BYTES = 32


class CurlSetupError(OSError):
    """A safe transport code and optional HTTP status, never curl's diagnostic text."""

    def __init__(self, code: str, status: int | None = None) -> None:
        """Retain only locally selected codes and numeric status."""
        super().__init__(code)
        self.code = code
        self.status = status


def _quote(value: str) -> str:
    escaped = value.replace('\\', '\\\\').replace('"', '\\"')
    return '"' + escaped.replace('\r', '\\r').replace('\n', '\\n').replace('\t', '\\t') + '"'


def _kill(process: subprocess.Popen[bytes]) -> None:
    # The request may have finished concurrently with its deadline.
    with suppress(OSError):
        process.kill()


def capture_process(
    process: subprocess.Popen[bytes], config: bytes, limit: int
) -> tuple[int, bytes]:
    """Bound output in memory and kill/reap the child even if it ignores its own timeout."""
    expired = threading.Event()

    def timeout() -> None:
        expired.set()
        _kill(process)

    def write() -> None:
        if process.stdin is not None:
            try:
                _ = process.stdin.write(config)
                process.stdin.close()
            except OSError:
                pass  # Curl may reject a config or close the pipe before reading it all.

    writer = threading.Thread(target=write, daemon=True)
    timer = threading.Timer(PROCESS_TIMEOUT_SECONDS, timeout)
    timer.daemon = True
    writer.start()
    timer.start()
    try:
        raw = b'' if process.stdout is None else process.stdout.read(limit + 1)
        if len(raw) > limit:
            _kill(process)
            message = 'curl_response_too_large'
            raise CurlSetupError(message)
        code = process.wait(timeout=PROCESS_TIMEOUT_SECONDS)
        if expired.is_set():
            message = 'curl_timeout'
            raise CurlSetupError(message)
        return code, raw
    finally:
        timer.cancel()
        if process.poll() is None:
            _kill(process)
        _ = process.wait(timeout=PROCESS_TIMEOUT_SECONDS)
        writer.join(timeout=1)
        if process.stdout is not None:
            process.stdout.close()
        if process.stdin is not None:
            process.stdin.close()


class LocalCurlSession:
    """Use native localhost cookie semantics without rewriting hosts or cookie attributes."""

    def __init__(self, maximum_response: int) -> None:
        """Fail before login if native curl or owner-private temporary storage is unavailable."""
        executable = shutil.which('curl.exe' if os.name == 'nt' else 'curl')
        if executable is None:
            message = 'local_curl_required'
            raise CurlSetupError(message)
        self._executable = str(Path(executable).absolute())
        self._maximum_response = maximum_response
        self.directory = Path(tempfile.gettempdir()) / f'maivn-connection-session-{uuid4().hex}'
        self.jar = self.directory / 'cookies.txt'
        self._closed = False
        self._owns_directory = False
        try:
            private_directory(self.directory, exclusive=True)
            self._owns_directory = True
            reservation = PrivateSetupFile(str(self.jar))
            reservation.close(remove_empty=False)
        except BaseException:
            self.close()
            raise

    @property
    def has_cookies(self) -> bool:
        """Track possible session state without copying private cookies into another engine."""
        return not self._closed and self.jar.stat().st_size > 0

    def request(
        self, method: str, url: str, body: bytes | None, headers: Mapping[str, str]
    ) -> tuple[int, bytes]:
        """Pass private headers/body only through stdin; never follow redirects or retry."""
        if self._closed:
            message = 'local_session_closed'
            raise CurlSetupError(message)
        private_directory(self.directory)
        private_file(self.jar)
        config = [f'url = {_quote(url)}', f'request = {_quote(method)}']
        config.extend(f'header = {_quote(name + ": " + value)}' for name, value in headers.items())
        if body is not None:
            config.append(f'data-binary = {_quote(body.decode("utf-8"))}')
        process = subprocess.Popen(  # noqa: S603 - resolved native executable, fixed flags, no shell.
            self._arguments(),
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            cwd=self.directory,
            creationflags=subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0,
        )
        try:
            code, raw = capture_process(
                process,
                ('\n'.join(config) + '\n').encode(),
                self._maximum_response + MAX_STATUS_BYTES,
            )
        except subprocess.SubprocessError:
            message = 'curl_transport_failed'
            raise CurlSetupError(message) from None
        # Curl may atomically replace its jar. Its parent was owner-only before spawning,
        # so protect the new regular file before any later request or cleanup.
        if os.name != 'nt':
            self.jar.chmod(0o600)
        private_file(self.jar, created=True)
        payload, separator, status_raw = raw.rpartition(STATUS_MARKER)
        status = int(status_raw.strip()) if status_raw.strip().isdigit() else 0
        if not separator or not status:
            message = 'curl_transport_failed'
            raise CurlSetupError(message)
        if code:
            message = 'curl_transfer_incomplete'
            raise CurlSetupError(message, status)
        if len(payload) > self._maximum_response:
            message = 'curl_response_too_large'
            raise CurlSetupError(message, status)
        return status, payload

    def _arguments(self) -> list[str]:
        return [
            self._executable,
            '--disable',  # Must be first: never load user or system curl config files.
            '--silent',
            '--max-time',
            '15',
            '--connect-timeout',
            '5',
            '--max-filesize',
            str(self._maximum_response),
            '--noproxy',
            '*',
            '--proto',
            '=http',
            '--cookie',
            self.jar.name,
            '--cookie-jar',
            self.jar.name,
            '--config',
            '-',
            '--write-out',
            STATUS_MARKER.decode() + '%{http_code}\n',
        ]

    def close(self) -> bool:
        """Delete only this session's private temporary files; report incomplete cleanup."""
        self._closed = True
        if not self._owns_directory:
            return True
        try:
            if not self.directory.exists():
                return True
            private_directory(self.directory)
            for entry in self.directory.iterdir():
                # A killed curl can leave its same-directory atomic-write temporary file.
                entry.unlink()
            self.directory.rmdir()
        except (OSError, ValueError):
            return False
        return True
