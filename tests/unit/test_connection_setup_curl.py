"""Real native curl preserves Secure cookies without persistent or unbounded client state."""

from __future__ import annotations

import io
import json
import subprocess
import sys
import time
from http import HTTPStatus
from pathlib import Path
from typing import TYPE_CHECKING, Any, cast
from uuid import UUID

import pytest

from maivn import cli
from maivn._internal import connection_setup_curl as transport
from maivn._internal.connection_setup_http import HumanSetupSession, SetupRefusalError
from maivn._internal.local_file_security import private_directory, private_file
from tests.unit.test_connection_setup_cli import (
    PASSWORD,
    SECRET,
    arguments,
    platform,
)

if TYPE_CHECKING:
    from collections.abc import Sequence

AUTH_CREATE_LOGOUT_REQUEST_COUNT = 6
OUTER_TIMEOUT_ASSERTION_SECONDS = 5


def _missing_curl(_name: str) -> None:
    return None


def test_local_secure_cookie_jar_is_private_ephemeral_and_secrets_never_enter_argv(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """Inspect the real child launch and OS permissions, then require logout and jar deletion."""
    monkeypatch.setattr(transport.tempfile, 'gettempdir', lambda: str(tmp_path))
    original = transport.subprocess.Popen
    launches: list[list[str]] = []

    def launch(args: Sequence[str], **kwargs: Any) -> subprocess.Popen[bytes]:
        argv = list(args)
        launches.append(argv)
        assert argv[1] == '--disable'
        assert '--location' not in argv
        assert '--retry' not in argv
        assert PASSWORD not in ' '.join(argv)
        assert SECRET not in ' '.join(argv)
        jar = Path(kwargs['cwd']) / argv[argv.index('--cookie-jar') + 1]
        private_directory(jar.parent)
        private_file(jar)
        assert kwargs['stderr'] == subprocess.DEVNULL
        return cast('subprocess.Popen[bytes]', original(args, **kwargs))

    monkeypatch.setattr(transport.subprocess, 'Popen', launch)
    monkeypatch.setattr('sys.stdin', io.StringIO(json.dumps({'password': PASSWORD})))
    with platform() as (origin, requests):
        monkeypatch.setattr('sys.argv', arguments(origin, tmp_path / 'output' / 'secret.json'))
        cli.main()
    assert len(launches) == AUTH_CREATE_LOGOUT_REQUEST_COUNT
    assert requests[-1]['path'].endswith('/logout')
    assert not list(tmp_path.glob('maivn-connection-session-*'))
    captured = capsys.readouterr()
    assert not captured.err
    assert PASSWORD not in captured.out
    assert SECRET not in captured.out


def test_missing_curl_fails_before_private_prompt_or_http(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """Give a safe actionable prerequisite error before consuming any credential."""
    monkeypatch.setattr(transport.shutil, 'which', _missing_curl)
    supplied = io.StringIO(json.dumps({'password': PASSWORD}))
    monkeypatch.setattr('sys.stdin', supplied)
    target = tmp_path / 'private' / 'output.json'
    with platform() as (origin, requests):
        monkeypatch.setattr('sys.argv', arguments(origin, target))
        with pytest.raises(SystemExit):
            cli.main()
    assert supplied.tell() == 0
    assert not requests
    assert not target.exists()
    captured = capsys.readouterr()
    assert 'native curl' in captured.err
    assert not captured.out
    assert PASSWORD not in captured.err


def test_https_does_not_require_curl_or_create_temporary_cookies(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The existing HTTPS transport keeps its ordinary in-memory cookie engine."""
    monkeypatch.setattr(transport.shutil, 'which', _missing_curl)
    monkeypatch.setattr(transport.tempfile, 'gettempdir', lambda: str(tmp_path))
    session = HumanSetupSession('https://platform.example')
    assert not session.has_cookies
    assert session.logout()
    assert not list(tmp_path.iterdir())


def test_wrong_password_reaches_401_without_inventing_authenticated_logout(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Match the live platform: rejected login has only CSRF state, still deleted locally."""
    monkeypatch.setattr(transport.tempfile, 'gettempdir', lambda: str(tmp_path))
    with platform(login_status=401) as (origin, requests):
        session = HumanSetupSession(origin)
        with pytest.raises(SetupRefusalError) as failure:
            session.login('developer@example.test', PASSWORD)
        assert failure.value.status == HTTPStatus.UNAUTHORIZED
        assert not session.logout()
    assert 'session=human' not in requests[-1]['headers'].get('Cookie', '')
    assert not list(tmp_path.iterdir())


def test_equals_in_temporary_parent_is_a_cookie_file_not_literal_cookie_data(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Curl interprets --cookie values containing '=' as data, even in absolute file paths."""
    parent = tmp_path / 'developer=local'
    parent.mkdir()
    monkeypatch.setattr(transport.tempfile, 'gettempdir', lambda: str(parent))
    with platform() as (origin, requests):
        session = HumanSetupSession(origin)
        try:
            session.login('developer@example.test', PASSWORD)
        finally:
            session.logout()
    assert requests[0]['headers']['Cookie'] == 'csrf=bootstrap'
    assert not list(parent.iterdir())


def test_cookie_directory_collision_preserves_preexisting_files(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Even a forced UUID collision must never turn cleanup into deletion of existing state."""
    identifier = UUID('10000000-0000-4000-8000-000000000001')
    directory = tmp_path / f'maivn-connection-session-{identifier.hex}'
    private_directory(directory)
    original = directory / 'cookies.txt'
    original.write_text('preexisting-private-state')
    monkeypatch.setattr(transport.tempfile, 'gettempdir', lambda: str(tmp_path))
    monkeypatch.setattr(transport, 'uuid4', lambda: identifier)
    with pytest.raises(FileExistsError):
        transport.LocalCurlSession(65_536)
    assert original.read_text() == 'preexisting-private-state'


def test_curl_ignores_default_config_and_proxy_and_preserves_literal_private_body(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """User curl configuration cannot add headers or reroute this one-shot private request."""
    (tmp_path / '.curlrc').write_text('header = "X-Unexpected: private-default"\n')
    (tmp_path / '_curlrc').write_text('header = "X-Unexpected: private-default"\n')
    monkeypatch.setenv('CURL_HOME', str(tmp_path))
    monkeypatch.setenv('http_proxy', 'http://127.0.0.1:1')
    monkeypatch.setenv('ALL_PROXY', 'http://127.0.0.1:1')
    literal = 'synthetic"\\\r\n\tpassword'
    with platform() as (origin, requests):
        session = HumanSetupSession(origin)
        try:
            session.login('developer@example.test', literal)
        finally:
            assert session.logout()
    assert requests[0]['body']['password'] == literal
    assert all('X-Unexpected' not in item['headers'] for item in requests)
    assert requests[0]['headers']['Host'] == origin.removeprefix('http://')


@pytest.mark.parametrize('logout_status', [200, 503])
def test_cookie_directory_removed_after_logout_even_on_refusal(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, logout_status: int
) -> None:
    """A failed remote logout must not leave the local human session behind."""
    monkeypatch.setattr(transport.tempfile, 'gettempdir', lambda: str(tmp_path))
    with platform(logout_status=logout_status) as (origin, _requests):
        session = HumanSetupSession(origin)
        session.login('developer@example.test', PASSWORD)
        assert len(list(tmp_path.glob('maivn-connection-session-*'))) == 1
        assert session.logout() == (logout_status == HTTPStatus.OK)
    assert not list(tmp_path.iterdir())


def _process(code: str) -> subprocess.Popen[bytes]:
    return subprocess.Popen(  # noqa: S603 - fixed local Python fixture, no network or shell.
        [sys.executable, '-c', code],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
    )


def test_child_that_never_reads_stdin_is_killed_and_reaped(monkeypatch: pytest.MonkeyPatch) -> None:
    """The outer deadline also covers a stuck config writer, without relying on curl itself."""
    monkeypatch.setattr(transport, 'PROCESS_TIMEOUT_SECONDS', 0.5)
    process = _process('import time; time.sleep(30)')
    started = time.monotonic()
    with pytest.raises(transport.CurlSetupError, match='curl_timeout'):
        transport.capture_process(process, b'x' * 100_000, 100)
    assert time.monotonic() - started < OUTER_TIMEOUT_ASSERTION_SECONDS
    assert process.poll() is not None


def test_child_output_is_bounded_before_it_can_fill_memory() -> None:
    """A broken or malicious response cannot turn capture into unbounded buffering."""
    process = _process(
        'import sys,time; sys.stdout.write("x"*100000); sys.stdout.flush(); time.sleep(30)'
    )
    with pytest.raises(transport.CurlSetupError, match='curl_response_too_large'):
        transport.capture_process(process, b'', 100)
    assert process.poll() is not None


def test_cleanup_failure_is_reported_instead_of_claiming_cookie_deletion(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Preserve honest cleanup status without leaking a cookie path or contents."""
    with platform() as (origin, _requests):
        session = HumanSetupSession(origin)
        session.login('developer@example.test', PASSWORD)
        original = transport.LocalCurlSession.close

        def failed_close(local: transport.LocalCurlSession) -> bool:
            assert original(local)
            return False

        monkeypatch.setattr(transport.LocalCurlSession, 'close', failed_close)
        assert not session.logout()
