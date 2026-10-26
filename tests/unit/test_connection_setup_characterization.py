"""Characterize ``maivn connections create`` and the pieces it is built from.

Refusals that happen before any socket opens use an HTTPS origin that is never
contacted. Flows that need a platform answer use a loopback HTTP stand-in, which
the command reaches through native curl exactly as it does in development.
Every assertion is on what a person sees (exit code, stdout, stderr) or on what
the platform stand-in received.
"""

from __future__ import annotations

import io
import json
import os
import subprocess
import sys
import threading
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import TYPE_CHECKING

import pytest

from maivn import cli
from maivn._internal.connection_setup_curl import CurlSetupError, LocalCurlSession
from maivn._internal.connection_setup_file import PrivateSetupFile
from maivn._internal.connection_setup_http import HumanSetupSession, SetupRefusalError
from maivn.connection_events import FormPublisher

if TYPE_CHECKING:
    from collections.abc import Generator

PROJECT = 'de000000-0000-4000-8000-000000000021'
CONNECTION = 'conn_characterized'
PASSWORD = 'password-NEVER-PRINTED'  # noqa: S105 - synthetic credential.
MINTED = 'minted-NEVER-PRINTED'
HTTPS_ORIGIN = 'https://platform.invalid'
SETUP_FAILED_ADVICE = (
    'Check the selected private directory and input. Run '
    'maivn connections create --help for the required fields.\n'
)
UNCONFIRMED = (
    'Connection creation succeeded, but private setup output is unconfirmed. '
    'Do not recreate; inspect this connection in Studio or Portal.\n'
)
USAGE_ERROR = 'Invalid connection arguments; run maivn connections create --help.\n'


def _args(origin: str = HTTPS_ORIGIN, **overrides: str | None) -> list[str]:
    """Build `connections create` arguments; a None override drops that option."""
    options: dict[str, str | None] = {
        '--base-url': origin,
        '--project-id': PROJECT,
        '--email': 'developer@example.test',
        '--name': 'Characterized',
        '--kind': 'slack_app',
    }
    options.update({f'--{key.replace("_", "-")}': value for key, value in overrides.items()})
    argv = ['create']
    for name, value in options.items():
        if value is not None:
            argv.extend([name, value])
    return argv


def _run(monkeypatch: pytest.MonkeyPatch, argv: list[str], stdin: str = '') -> int | str | None:
    monkeypatch.setattr(sys, 'argv', ['maivn', 'connections', *argv])
    monkeypatch.setattr(sys, 'stdin', io.StringIO(stdin))
    try:
        cli.main()
    except SystemExit as stopped:
        return stopped.code
    return None


def _refusal(stage: str, status: int | None = None) -> str:
    return json.dumps({'status': stage, 'http_status': status}) + '\n'


# --- argument and public-metadata refusals (no socket is opened) ---------------------------


def test_connections_without_an_operation_prints_help(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """`maivn connections` alone prints the create usage and exits cleanly."""
    assert _run(monkeypatch, []) is None
    captured = capsys.readouterr()
    assert captured.out.startswith('usage: maivn connections create [-h] [--base-url BASE_URL]')
    assert captured.err == ''


def test_connections_with_an_unknown_operation_is_a_usage_error(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """Only `create` exists; anything else is the fixed usage error, exit 2."""
    assert _run(monkeypatch, ['delete', '--secret', MINTED]) == 2  # noqa: PLR2004 - argparse status
    assert capsys.readouterr() == ('', USAGE_ERROR)


@pytest.mark.parametrize(
    ('overrides', 'stage'),
    [
        ({'base_url': 'http://platform.invalid'}, 'invalid_platform_origin'),
        ({'base_url': 'https://platform.invalid/v1'}, 'invalid_platform_origin'),
        ({'project_id': PROJECT.upper()}, 'invalid_project_id'),
        ({'name': 'x' * 121}, 'invalid_public_metadata'),
        ({'email': 'ab'}, 'invalid_public_metadata'),
        ({'email': 'a' * 321}, 'invalid_public_metadata'),
        ({'name': 'line\nbreak'}, 'invalid_public_metadata'),
        ({'email': 'tab\t@example.test'}, 'invalid_public_metadata'),
        ({'kind': 'form_webhook'}, 'minted_secret_requires_new_private_file'),
        ({'secret_file': 'unused.json'}, 'minted_secret_requires_new_private_file'),
        ({'kind': 'stripe_webhook'}, 'stripe_mode_required'),
        ({'stripe_mode': 'live'}, 'stripe_mode_requires_stripe'),
        ({'discord_protocol': 'interactions'}, 'discord_fields_require_discord'),
        ({'kind': 'discord_webhook'}, 'invalid_discord_verification'),
    ],
)
def test_public_metadata_refusals_name_only_a_safe_stage(
    overrides: dict[str, str],
    stage: str,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    tmp_path: Path,
) -> None:
    """Each unusable public field is refused before any file, prompt or request."""
    monkeypatch.chdir(tmp_path)

    assert _run(monkeypatch, _args(**overrides)) == 1
    assert capsys.readouterr() == ('', _refusal(stage))
    assert list(tmp_path.iterdir()) == []


@pytest.mark.parametrize(
    ('application_id', 'public_key'),
    [
        ('0123', 'ab' * 32),
        ('1' * 21, 'ab' * 32),
        (str(2**64), 'ab' * 32),
        ('12345', 'ab' * 31),
        ('12345', 'zz' * 32),
    ],
)
def test_discord_verification_values_are_checked_exactly(
    application_id: str,
    public_key: str,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """A Discord app id must be a 64-bit decimal snowflake and the key 64 hex digits."""
    argv = _args(
        kind='discord_webhook',
        discord_application_id=application_id,
        discord_public_key=public_key,
        discord_protocol='interactions',
    )

    assert _run(monkeypatch, argv) == 1
    assert capsys.readouterr() == ('', _refusal('invalid_discord_verification'))


def test_a_project_id_that_is_not_a_uuid_is_a_generic_setup_failure(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """A malformed UUID fails as `setup_failed` with the help advice, never echoing input."""
    assert _run(monkeypatch, _args(project_id='not-a-uuid-secret')) == 1
    assert capsys.readouterr() == ('', _refusal('setup_failed') + SETUP_FAILED_ADVICE)


# --- private input (still no socket: HTTPS origin, refusal before login) -------------------


def test_private_prompt_without_a_terminal_is_refused(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """Without --credentials-stdin a terminal is required for the hidden prompt."""
    assert _run(monkeypatch, _args()) == 1
    assert capsys.readouterr() == (
        '',
        _refusal('private_prompt_requires_terminal_or_credentials_stdin'),
    )


class _Terminal(io.StringIO):
    """A stdin that reports an attached terminal."""

    def isatty(self) -> bool:
        """Report a terminal so the hidden prompt is used."""
        return True


@pytest.mark.parametrize(
    ('kind', 'expected_prompts'),
    [
        ('slack_app', ['Account password (private): ', 'Signing secret (private): ']),
        ('github_app', ['Account password (private): ', 'Webhook secret (private): ']),
        ('outgoing_webhook', ['Account password (private): ', 'Endpoint url (private): ']),
        ('form_webhook', ['Account password (private): ']),
    ],
)
def test_terminal_prompts_for_password_then_any_provider_secret(
    kind: str,
    expected_prompts: list[str],
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    tmp_path: Path,
) -> None:
    """Each private value is prompted with its label and checked before any login."""
    prompts: list[str] = []

    def hidden(prompt: str) -> str:
        prompts.append(prompt)
        return 'short'

    secret_file = str(tmp_path / 'private' / 'receipt.json') if kind == 'form_webhook' else None
    monkeypatch.setattr('getpass.getpass', hidden)
    argv = ['maivn', 'connections', *_args(kind=kind, secret_file=secret_file)]
    monkeypatch.setattr(sys, 'argv', argv)
    monkeypatch.setattr(sys, 'stdin', _Terminal())

    with pytest.raises(SystemExit) as stopped:
        cli.main()

    assert stopped.value.code == 1
    assert prompts == expected_prompts
    assert capsys.readouterr() == ('', _refusal('invalid_password_length'))
    assert not (tmp_path / 'private' / 'receipt.json').exists()


@pytest.mark.parametrize(
    ('kind', 'field', 'value', 'stage'),
    [
        ('slack_app', 'signing_secret', '', 'invalid_provider_value'),
        ('slack_app', 'signing_secret', 's' * 501, 'invalid_provider_value'),
        (
            'outgoing_webhook',
            'endpoint_url',
            'https://e.example/' + 'p' * 2031,
            'invalid_provider_value',
        ),
        (
            'outgoing_webhook',
            'endpoint_url',
            'http://hooks.example/in',
            'output_endpoint_requires_https',
        ),
        ('discord_output', 'endpoint_url', 'https:///no-host', 'output_endpoint_requires_https'),
        (
            'slack_webhook',
            'endpoint_url',
            'https://user@hooks.example/in',
            'output_endpoint_requires_https',
        ),
        (
            'slack_webhook',
            'endpoint_url',
            'https://:p@hooks.example/in',
            'output_endpoint_requires_https',
        ),
    ],
)
def test_provider_values_are_bounded_and_outputs_must_be_https(  # noqa: PLR0913 - matrix plus fixtures.
    kind: str,
    field: str,
    value: str,
    stage: str,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Provider secrets are 1-500 characters; output URLs are credential-free HTTPS."""
    stdin = json.dumps({'password': PASSWORD, field: value}) + '\n'

    assert _run(monkeypatch, [*_args(kind=kind), '--credentials-stdin'], stdin) == 1
    captured = capsys.readouterr()
    assert captured == ('', _refusal(stage))


def test_password_length_bounds_are_inclusive(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """Passwords of 7 or 257 characters are refused before login."""
    for password in ('p' * 7, 'p' * 257):
        stdin = json.dumps({'password': password, 'signing_secret': 'sig'}) + '\n'
        assert _run(monkeypatch, [*_args(), '--credentials-stdin'], stdin) == 1
        assert capsys.readouterr() == ('', _refusal('invalid_password_length'))


# --- full flows against a loopback platform stand-in ---------------------------------------


@contextmanager
def platform(create: object, status: int = 201) -> Generator[tuple[str, list[str]]]:
    """Accept login and logout; answer the create request with a fixed body."""
    paths: list[str] = []

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, format: str, *args: object) -> None:  # noqa: A002 - stdlib override.
            del format, args

        def do_GET(self) -> None:
            self.reply(200, {'csrf_token': 'token', 'csrf_header_name': 'x-csrf-token'})

        def do_POST(self) -> None:
            _ = self.rfile.read(int(self.headers.get('Content-Length', '0')))
            paths.append(self.path)
            if self.path.endswith('/connections'):
                self.reply(status, create)
            else:
                self.reply(200, {}, cookie=self.path.endswith('/login'))

        def reply(self, code: int, body: object, *, cookie: bool = False) -> None:
            encoded = json.dumps(body).encode()
            self.send_response(code)
            self.send_header('Content-Type', 'application/json')
            self.send_header('Content-Length', str(len(encoded)))
            if cookie:
                self.send_header('Set-Cookie', 'session=human; Path=/; HttpOnly')
            self.end_headers()
            self.wfile.write(encoded)

    server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f'http://localhost:{server.server_port}', paths
    finally:
        server.shutdown()
        server.server_close()
        thread.join()


def _connection(**fields: object) -> dict[str, object]:
    return {'connection_id': CONNECTION, 'project_id': PROJECT, 'status': 'active', **fields}


def _slack(origin: str) -> tuple[list[str], str]:
    stdin = json.dumps({'password': PASSWORD, 'signing_secret': 'sig-NEVER-PRINTED'}) + '\n'
    return [*_args(origin), '--credentials-stdin'], stdin


@pytest.mark.parametrize(
    ('body', 'callback'),
    [
        ({'connection': _connection()}, None),
        (
            {
                'connection': _connection(),
                'callback_path': f'/v1/connections/{CONNECTION}/events',
                'inbound_url': f'https://hooks.example:8443/v1/connections/{CONNECTION}/events',
            },
            f'https://hooks.example:8443/v1/connections/{CONNECTION}/events',
        ),
    ],
)
def test_created_receipt_names_the_connection_and_its_public_callback(
    body: dict[str, object],
    callback: str | None,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """The receipt is one sorted JSON line; a published inbound URL replaces the origin."""
    with platform(body) as (origin, paths):
        argv, stdin = _slack(origin)
        assert _run(monkeypatch, argv, stdin) is None
    assert paths[:2] == ['/v1/auth/login', f'/v1/projects/{PROJECT}/connections']
    assert capsys.readouterr() == (
        json.dumps(
            {'callback_url': callback, 'connection_id': CONNECTION, 'status': 'created'},
            sort_keys=True,
        )
        + '\n',
        '',
    )


@pytest.mark.parametrize(
    'body',
    [
        {'connection': 'not-an-object'},
        {'connection': _connection(connection_id='bad/id')},
        {'connection': _connection(connection_id=7)},
        {'connection': _connection(project_id='de000000-0000-4000-8000-000000000099')},
        {'connection': _connection(status='pending')},
        {'connection': _connection(), 'callback_path': '/v1/connections/other/events'},
        {'connection': _connection(), 'callback_path': 7},
        {
            'connection': _connection(),
            'callback_path': f'/v1/connections/{CONNECTION}/events',
            'inbound_url': 7,
        },
        {
            'connection': _connection(),
            'callback_path': f'/v1/connections/{CONNECTION}/events',
            'inbound_url': 'https://hooks.example/elsewhere',
        },
        {
            'connection': _connection(),
            'callback_path': f'/v1/connections/{CONNECTION}/events',
            'inbound_url': f'https://hooks.example/v1/connections/{CONNECTION}/events?x=1',
        },
        {
            'connection': _connection(),
            'callback_path': f'/v1/connections/{CONNECTION}/events',
            'inbound_url': f'https://hooks.example/v1/connections/{CONNECTION}/events#top',
        },
    ],
)
def test_an_unusable_create_answer_is_reported_as_created_but_unconfirmed(
    body: dict[str, object],
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """After a 201 the command never suggests retrying; it says the outcome needs a look."""
    with platform(body) as (origin, paths):
        argv, stdin = _slack(origin)
        assert _run(monkeypatch, argv, stdin) == 1
    assert paths.count(f'/v1/projects/{PROJECT}/connections') == 1
    assert capsys.readouterr() == (
        '',
        _refusal('create_response_invalid', 201) + UNCONFIRMED,
    )


def test_a_plaintext_remote_inbound_url_is_refused_as_an_unsafe_origin(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """An HTTP callback on a non-loopback host is refused, still as created-but-unconfirmed."""
    body = {
        'connection': _connection(),
        'callback_path': f'/v1/connections/{CONNECTION}/events',
        'inbound_url': f'http://hooks.example/v1/connections/{CONNECTION}/events',
    }
    with platform(body) as (origin, _paths):
        argv, stdin = _slack(origin)
        assert _run(monkeypatch, argv, stdin) == 1
    assert capsys.readouterr() == ('', _refusal('invalid_platform_origin') + UNCONFIRMED)


def test_a_missing_minted_secret_keeps_the_receipt_and_removes_the_empty_file(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], tmp_path: Path
) -> None:
    """A minting kind without a one-time secret prints the receipt as unconfirmed."""
    target = tmp_path / 'private' / 'receipt.json'
    stdin = json.dumps({'password': PASSWORD}) + '\n'
    with platform({'connection': _connection(), 'one_time_secret': None}) as (origin, _paths):
        argv = [
            *_args(origin, kind='form_webhook', secret_file=str(target)),
            '--credentials-stdin',
        ]
        assert _run(monkeypatch, argv, stdin) == 1
    assert capsys.readouterr() == (
        json.dumps(
            {
                'callback_url': None,
                'connection_id': CONNECTION,
                'status': 'created_private_save_unconfirmed',
            },
            sort_keys=True,
        )
        + '\n',
        _refusal('minted_secret_missing', 201) + UNCONFIRMED,
    )
    assert not target.exists()


def test_receipt_fields_containing_a_private_value_are_redacted(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], tmp_path: Path
) -> None:
    """A minted secret echoed into a public field is printed as `[redacted]`, never verbatim."""
    target = tmp_path / 'private' / 'receipt.json'
    stdin = json.dumps({'password': PASSWORD}) + '\n'
    body = {
        'connection': _connection(connection_id=MINTED),
        'callback_path': f'/v1/connections/{MINTED}/events',
        'one_time_secret': MINTED,
    }
    with platform(body) as (origin, _paths):
        argv = [
            *_args(origin, kind='incoming_webhook', secret_file=str(target)),
            '--credentials-stdin',
        ]
        assert _run(monkeypatch, argv, stdin) is None
        served_from = origin
    assert capsys.readouterr() == (
        json.dumps(
            {'callback_url': '[redacted]', 'connection_id': '[redacted]', 'status': 'created'},
            sort_keys=True,
        )
        + '\n',
        '',
    )
    assert json.loads(target.read_text(encoding='utf-8')) == {
        'callback_url': f'{served_from}/v1/connections/{MINTED}/events',
        'connection_id': MINTED,
        'one_time_secret': MINTED,
        'project_id': PROJECT,
        'setup_kind': 'incoming_webhook',
        'status': 'created',
    }


# --- session and transport building blocks ----------------------------------------------


@contextmanager
def raw_platform(csrf_body: bytes) -> Generator[str]:
    """Answer every GET with fixed raw bytes, to see how responses are decoded."""

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, format: str, *args: object) -> None:  # noqa: A002 - stdlib override.
            del format, args

        def do_GET(self) -> None:
            self.send_response(200)
            self.send_header('Content-Length', str(len(csrf_body)))
            self.end_headers()
            self.wfile.write(csrf_body)

    server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f'http://127.0.0.1:{server.server_port}'
    finally:
        server.shutdown()
        server.server_close()
        thread.join()


@pytest.mark.parametrize(
    ('raw', 'status'),
    [(b'not json', 200), (b'[1, 2]', 200), (b'\xff\xfe', 200), (b'', None)],
)
def test_csrf_answers_that_are_not_a_json_object_are_refused(
    raw: bytes, status: int | None
) -> None:
    """Undecodable or non-object answers keep their status; an empty one lacks the fields."""
    with raw_platform(raw) as origin:
        session = HumanSetupSession(origin)
        try:
            with pytest.raises(SetupRefusalError) as refused:
                _ = session.csrf()
        finally:
            _ = session.logout()
    expected = 'csrf_response_invalid'
    assert (refused.value.stage, refused.value.status) == (expected, status)
    assert str(refused.value) == expected


def test_closed_local_session_refuses_further_requests(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """After close, a local curl session has no cookies and sends nothing."""
    monkeypatch.setattr('tempfile.tempdir', str(tmp_path))
    session = LocalCurlSession(1024)
    assert session.close() is True
    assert session.has_cookies is False
    with pytest.raises(CurlSetupError) as refused:
        _ = session.request('GET', 'http://127.0.0.1:1/', None, {})
    assert (refused.value.code, refused.value.status) == ('local_session_closed', None)
    assert list(tmp_path.iterdir()) == []


def test_local_session_close_tolerates_an_already_removed_folder(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Cleanup of a folder someone already removed is still a confirmed cleanup."""
    monkeypatch.setattr('tempfile.tempdir', str(tmp_path))
    session = LocalCurlSession(1024)
    (session.directory / 'cookies.txt').unlink()
    session.directory.rmdir()
    assert session.close() is True


def test_local_session_close_reports_a_folder_it_cannot_empty(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Unexpected content it cannot delete makes cleanup unconfirmed, not silently partial."""
    monkeypatch.setattr('tempfile.tempdir', str(tmp_path))
    session = LocalCurlSession(1024)
    (session.directory / 'unexpected').mkdir()
    assert session.close() is False
    assert (session.directory / 'unexpected').is_dir()


def _curl_stand_in(monkeypatch: pytest.MonkeyPatch, tmp_path: Path, script: str) -> None:
    """Launch a Python script wherever native curl would be launched."""
    source = tmp_path / 'curl_stand_in.py'
    _ = source.write_text(script, encoding='utf-8')
    original = subprocess.Popen

    def launch(args: list[str], **options: object) -> subprocess.Popen[bytes]:
        assert Path(args[0]).stem.lower() == 'curl'
        return original([sys.executable, str(source)], **options)  # pyright: ignore[reportCallIssue, reportArgumentType]

    monkeypatch.setattr(subprocess, 'Popen', launch)
    monkeypatch.setattr('tempfile.tempdir', str(tmp_path))


def _emit(output: bytes, exit_code: int = 0) -> str:
    """Script a curl run: read the private config, print raw bytes, exit with a code."""
    head = 'import sys\nsys.stdin.read()\n'
    return f'{head}sys.stdout.buffer.write({output!r})\nsys.exit({exit_code})\n'


@pytest.mark.parametrize(
    ('script', 'code', 'status'),
    [
        (_emit(b'body'), 'curl_transport_failed', None),
        (_emit(b'body\nMAIVN_HTTP_STATUS:000\n'), 'curl_transport_failed', None),
        (_emit(b'body\nMAIVN_HTTP_STATUS:200\n', 28), 'curl_transfer_incomplete', 200),
        (_emit(b'x' * 11 + b'\nMAIVN_HTTP_STATUS:200\n'), 'curl_response_too_large', 200),
    ],
)
def test_curl_outcomes_map_to_safe_transport_codes(
    script: str,
    code: str,
    status: int | None,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Missing status, failed transfers and oversize bodies become fixed codes, never text."""
    _curl_stand_in(monkeypatch, tmp_path, script)
    session = LocalCurlSession(10)
    try:
        with pytest.raises(CurlSetupError) as refused:
            _ = session.request('GET', 'http://127.0.0.1:1/v1/auth/csrf', None, {})
    finally:
        assert session.close()
    assert (refused.value.code, refused.value.status) == (code, status)


def test_curl_success_returns_status_and_exact_body(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A complete transfer returns the status written after the marker and the bytes before it."""
    _curl_stand_in(monkeypatch, tmp_path, _emit(b'0123456789\nMAIVN_HTTP_STATUS:202\n'))
    session = LocalCurlSession(10)
    try:
        assert session.request('POST', 'http://127.0.0.1:1/x', b'{}', {}) == (202, b'0123456789')
    finally:
        assert session.close()


# --- private destination file ----------------------------------------------------------


def test_reserved_file_filled_by_someone_else_is_never_removed(tmp_path: Path) -> None:
    """Cleanup removes only our own still-empty reservation."""
    target = tmp_path / 'private' / 'receipt.json'
    reservation = PrivateSetupFile(str(target))
    with target.open('ab') as other:
        _ = other.write(b'someone else')
    reservation.close(remove_empty=True)
    assert target.read_bytes() == b'someone else'


def test_write_refuses_when_the_reserved_file_was_swapped(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A destination replaced after reservation is refused; the replacement is untouched."""
    target = tmp_path / 'private' / 'receipt.json'
    reservation = PrivateSetupFile(str(target))
    if os.name == 'nt':
        # Windows cannot unlink a file this process holds open, so stand in for the swap
        # by reporting the open handle and the path as different files.
        def different(_left: os.stat_result, _right: os.stat_result) -> bool:
            return False

        monkeypatch.setattr(os.path, 'samestat', different)
    else:
        target.unlink()
        _ = target.write_bytes(b'')
        target.chmod(0o600)
    replacement = target
    try:
        with pytest.raises(ValueError, match=r'^private destination changed$'):
            reservation.write({'one_time_secret': MINTED})
    finally:
        reservation.close(remove_empty=True)
    assert replacement.read_bytes() == b''


def test_reservation_closed_without_removal_keeps_the_empty_file(tmp_path: Path) -> None:
    """`remove_empty=False` leaves the empty private file in place."""
    target = tmp_path / 'private' / 'receipt.json'
    PrivateSetupFile(str(target)).close(remove_empty=False)
    assert target.read_bytes() == b''


def test_written_receipt_is_indented_json_with_a_trailing_newline(tmp_path: Path) -> None:
    """The private file holds exactly the receipt, two-space indented, newline-terminated."""
    target = tmp_path / 'private' / 'receipt.json'
    reservation = PrivateSetupFile(str(target))
    try:
        reservation.write({'b': 1, 'a': 'x'})
    finally:
        reservation.close(remove_empty=True)
    assert target.read_bytes() == b'{\n  "b": 1,\n  "a": "x"\n}\n'


# --- form publisher configuration --------------------------------------------------------


@pytest.mark.parametrize('secret', ['', None, 7])
def test_form_publisher_requires_a_nonempty_secret_string(secret: object) -> None:
    """A missing or non-string signing secret is refused at construction."""
    with pytest.raises(ValueError, match=r'^a nonempty form signing secret is required$'):
        _ = FormPublisher(
            receiver_url='https://hooks.example/v1/connections/conn_form/events',
            connection_id='conn_form',
            secret=secret,  # pyright: ignore[reportArgumentType]
        )
