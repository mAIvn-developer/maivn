"""One-shot human cookie/CSRF client; no persistent session or auth policy."""

from __future__ import annotations

import http.client
import ipaddress
import json
import re
from http.cookiejar import CookieJar
from typing import NoReturn, cast
from urllib.parse import urlsplit
from urllib.request import Request

from maivn._internal.connection_setup_curl import CurlSetupError, LocalCurlSession

MAX_RESPONSE_BYTES = 65_536
MAX_CSRF_LENGTH = 4096
ASCII_SPACE = 32
ASCII_DELETE = 127
ASCII_FIRST_GRAPHIC = 33
ASCII_LAST_GRAPHIC = 126
RESERVED_HEADERS = frozenset(
    {
        'authorization',
        'proxy-authorization',
        'cookie',
        'host',
        'connection',
        'content-length',
        'content-type',
        'transfer-encoding',
        'upgrade',
    }
)


class SetupRefusalError(RuntimeError):
    """Safe operation and HTTP status, never response text or private request data."""

    def __init__(self, stage: str, status: int | None = None) -> None:
        """Keep only caller-owned operation names and numeric status."""
        super().__init__(stage)
        self.stage = stage
        self.status = status


def refuse(stage: str, status: int | None = None) -> NoReturn:
    """Refuse with a safe operation code, never caller input."""
    raise SetupRefusalError(stage, status)


def safe_origin(value: str) -> str:
    """Require HTTPS, permitting HTTP only for explicit loopback destinations."""
    parsed = urlsplit(value)
    hostname = parsed.hostname or ''
    try:
        loopback = ipaddress.ip_address(hostname).is_loopback
    except ValueError:
        loopback = hostname == 'localhost'
    if (
        parsed.scheme not in {'http', 'https'}
        or not hostname
        or (parsed.scheme == 'http' and not loopback)
        or parsed.username is not None
        or parsed.password is not None
        or parsed.path not in {'', '/'}
        or parsed.query
        or parsed.fragment
        or any(ord(char) < ASCII_SPACE or ord(char) == ASCII_DELETE for char in value)
    ):
        refuse('invalid_platform_origin')
    _ = parsed.port
    return value.rstrip('/')


class HumanSetupSession:
    """HTTPS cookies stay in memory; local curl cookies use an ephemeral private jar."""

    def __init__(self, origin: str) -> None:
        """Validate the destination before opening any socket."""
        self.origin = safe_origin(origin)
        self.cookies = CookieJar()
        try:
            self._local = (
                LocalCurlSession(MAX_RESPONSE_BYTES)
                if urlsplit(self.origin).scheme == 'http'
                else None
            )
        except CurlSetupError as error:
            refuse(error.code)

    @property
    def has_cookies(self) -> bool:
        """Return whether cleanup should attempt the existing server logout flow."""
        return self._local.has_cookies if self._local is not None else bool(self.cookies)

    def request(  # noqa: PLR0913 - explicit method, path, status, body and CSRF wire fields.
        self,
        method: str,
        path: str,
        *,
        stage: str,
        expected: int,
        body: dict[str, object] | None = None,
        csrf: tuple[str, str] | None = None,
    ) -> dict[str, object]:
        """Bound every response and return JSON only after the expected status."""
        encoded = None if body is None else json.dumps(body).encode('utf-8')
        request = Request(self.origin + path, data=encoded, method=method)  # noqa: S310 - origin validated.
        request.add_header('Accept', 'application/json')
        if body is not None:
            request.add_header('Content-Type', 'application/json')
        if csrf is not None:
            request.add_header(*csrf)
        if self._local is not None:
            try:
                status, raw = self._local.request(
                    method, self.origin + path, encoded, dict(request.header_items())
                )
            except CurlSetupError as error:
                refuse(f'{stage}_response_invalid' if error.status else stage, error.status)
            return _decode_response(raw, status, expected, stage)
        self.cookies.add_cookie_header(request)
        parsed = urlsplit(self.origin)
        connection_type = (
            http.client.HTTPSConnection if parsed.scheme == 'https' else http.client.HTTPConnection
        )
        connection = connection_type(parsed.hostname or '', parsed.port, timeout=15)
        try:
            connection.request(method, path, body=encoded, headers=dict(request.header_items()))
            response = connection.getresponse()
            self.cookies.extract_cookies(response, request)
            raw = response.read(MAX_RESPONSE_BYTES + 1)
            return _decode_response(raw, response.status, expected, stage)
        finally:
            connection.close()

    def csrf(self) -> tuple[str, str]:
        """Ask the current session for its current token, including after login rotation."""
        body = self.request('GET', '/v1/auth/csrf', stage='csrf', expected=200)
        name, token = body.get('csrf_header_name'), body.get('csrf_token')
        if (
            not isinstance(name, str)
            or re.fullmatch(r"[!#$%&'*+.^_`|~0-9A-Za-z-]{1,64}", name) is None
            or name.lower() in RESERVED_HEADERS
            or not isinstance(token, str)
            or not 1 <= len(token) <= MAX_CSRF_LENGTH
            or any(not ASCII_FIRST_GRAPHIC <= ord(char) <= ASCII_LAST_GRAPHIC for char in token)
        ):
            refuse('csrf_response_invalid')
        return name, token

    def login(self, email: str, password: str) -> None:
        """Use the current double-submit login and let its cookies rotate normally."""
        _ = self.request(
            'POST',
            '/v1/auth/login',
            stage='login',
            expected=200,
            body={'email': email, 'password': password, 'remember_me': False},
            csrf=self.csrf(),
        )

    def logout(self) -> bool:
        """Attempt logout even after a failure, then discard all local cookies."""
        confirmed = True
        try:
            if self.has_cookies:
                _ = self.request(
                    'POST',
                    '/v1/auth/logout',
                    stage='logout',
                    expected=200,
                    csrf=self.csrf(),
                )
        except (OSError, ValueError, http.client.HTTPException, SetupRefusalError):
            confirmed = False
        finally:
            self.cookies.clear()
            if self._local is not None and not self._local.close():
                confirmed = False
        return confirmed


def _decode_response(raw: bytes, status: int, expected: int, stage: str) -> dict[str, object]:
    if status != expected:
        refuse(stage, status)
    if len(raw) > MAX_RESPONSE_BYTES:
        refuse(f'{stage}_response_invalid', status)
    if not raw:
        return {}
    try:
        parsed_body: object = json.loads(raw)
    except (ValueError, UnicodeError):
        refuse(f'{stage}_response_invalid', status)
    if not isinstance(parsed_body, dict):
        refuse(f'{stage}_response_invalid', status)
    return cast('dict[str, object]', parsed_body)
