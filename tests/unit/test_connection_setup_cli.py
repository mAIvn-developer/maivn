"""One-shot human connection setup uses real HTTP, private input and current CSRF rotation."""

from __future__ import annotations

import getpass
import io
import json
import threading
import warnings
from contextlib import contextmanager
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import TYPE_CHECKING, Any

import pytest

from maivn import cli
from maivn._internal.connection_setup_cli import MINTING_KINDS, PRIVATE_FIELDS, SETUP_KINDS
from maivn._internal.connection_setup_file import PrivateSetupFile
from maivn._internal.connection_setup_http import HumanSetupSession

if TYPE_CHECKING:
    from collections.abc import Generator
    from pathlib import Path

PROJECT = 'de000000-0000-4000-8000-000000000020'
PASSWORD = 'password-DO-NOT-PRINT'  # noqa: S105 - Synthetic test credential.
SECRET = 'minted-DO-NOT-PRINT'  # noqa: S105 - Synthetic test signing secret.
CONNECTION = 'conn_terminal_fixture'


@contextmanager
def platform(  # noqa: C901 - fixture covers the existing bootstrap/login/create/logout HTTP branches.
    **options: Any,
) -> Generator[tuple[str, list[dict[str, Any]]]]:
    """Require cookie + current CSRF on each mutation; emit secret error bodies."""
    requests: list[dict[str, Any]] = []

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, format: str, *args: object) -> None:  # noqa: A002 - stdlib override.
            del format, args

        def do_GET(self) -> None:
            signed_in = 'session=human' in self.headers.get('Cookie', '')
            token = 'rotated' if signed_in else 'bootstrap'
            self.reply(
                200,
                options.get(
                    'csrf_body',
                    {
                        'csrf_token': token,
                        'csrf_header_name': options.get('csrf_name', 'x-csrf-token'),
                    },
                ),
                token,
            )

        def do_POST(self) -> None:
            body = json.loads(
                self.rfile.read(int(self.headers.get('Content-Length', '0'))) or b'{}'
            )
            requests.append({'path': self.path, 'body': body, 'headers': dict(self.headers)})
            expected = 'bootstrap' if self.path.endswith('/login') else 'rotated'
            if (
                self.headers.get(options.get('csrf_name', 'x-csrf-token')) != expected
                or (
                    not self.path.endswith('/login')
                    and 'session=human' not in self.headers.get('Cookie', '')
                )
                or f'csrf={expected}' not in self.headers.get('Cookie', '')
            ):
                self.reply(403, {'error': SECRET})
            elif self.path.endswith('/login'):
                status = int(options.get('login_status', 200))
                self.reply(
                    status,
                    {'secret': PASSWORD},
                    'rotated' if status == HTTPStatus.OK else 'bootstrap',
                    login=status == HTTPStatus.OK,
                )
            elif self.path.endswith('/logout'):
                self.reply(int(options.get('logout_status', 200)), {'success': True})
            else:
                if options.get('drop_ack'):
                    self.close_connection = True
                    return
                callback = options.get('on_create')
                if callable(callback):
                    callback()
                response_body = {
                    'connection': {
                        'connection_id': CONNECTION,
                        'status': 'active',
                        'project_id': PROJECT,
                    },
                    'callback_path': f'/v1/connections/{CONNECTION}/events',
                    'one_time_secret': SECRET if body['setup_kind'] in MINTING_KINDS else None,
                    'private_debug': PASSWORD,
                }
                self.reply(
                    int(options.get('create_status', 201)),
                    options.get('response_body', response_body),
                )

        def reply(
            self, status: int, body: object, csrf: str | None = None, *, login: bool = False
        ) -> None:
            encoded = b'' if body is None else json.dumps(body).encode()
            self.send_response(status)
            self.send_header('Content-Type', 'application/json')
            self.send_header('Content-Length', str(len(encoded)))
            if csrf:
                self.send_header('Set-Cookie', f'csrf={csrf}; Path=/; Secure')
            if login:
                self.send_header('Set-Cookie', 'session=human; Path=/; HttpOnly; Secure')
            if status == HTTPStatus.FOUND:
                self.send_header('Location', 'https://never-follow.example/secret')
            self.end_headers()
            self.wfile.write(encoded)

    server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f'http://localhost:{server.server_port}', requests
    finally:
        server.shutdown()
        server.server_close()
        thread.join()


def arguments(origin: str, destination: Path) -> list[str]:
    """Only public metadata and the selected private destination appear in argv."""
    return [
        'maivn',
        'connections',
        'create',
        '--base-url',
        origin,
        '--project-id',
        PROJECT,
        '--email',
        'developer@example.test',
        '--name',
        'Terminal forms',
        '--kind',
        'form_webhook',
        '--secret-file',
        str(destination),
        '--credentials-stdin',
    ]


def test_real_http_rotates_csrf_logs_out_and_saves_secret_privately(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """Exercise the public CLI all the way through an actual TCP receiver."""
    target = tmp_path / 'private' / 'connection.json'
    monkeypatch.setattr('sys.stdin', io.StringIO(json.dumps({'password': PASSWORD}) + '\n'))
    with platform() as (origin, requests):
        monkeypatch.setattr('sys.argv', arguments(origin, target))
        cli.main()
    assert [item['path'] for item in requests] == [
        '/v1/auth/login',
        f'/v1/projects/{PROJECT}/connections',
        '/v1/auth/logout',
    ]
    assert {key.lower(): value for key, value in requests[1]['headers'].items()}[
        'x-csrf-token'
    ] == 'rotated'
    assert requests[1]['body'] == {'display_name': 'Terminal forms', 'setup_kind': 'form_webhook'}
    assert requests[0]['body'] == {
        'email': 'developer@example.test',
        'password': PASSWORD,
        'remember_me': False,
    }
    assert json.loads(target.read_text())['one_time_secret'] == SECRET
    captured = capsys.readouterr()
    assert PASSWORD not in captured.out + captured.err
    assert SECRET not in captured.out + captured.err
    assert json.loads(captured.out)['connection_id'] == CONNECTION


@pytest.mark.parametrize('status', [401, 403, 422, 503])
def test_create_refusal_logs_out_and_removes_only_owned_empty_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], status: int
) -> None:
    """Current refusal status survives without leaking the server's error body."""
    target = tmp_path / 'private' / 'connection.json'
    monkeypatch.setattr('sys.stdin', io.StringIO(json.dumps({'password': PASSWORD}) + '\n'))
    with platform(create_status=status) as (origin, requests):
        monkeypatch.setattr('sys.argv', arguments(origin, target))
        with pytest.raises(SystemExit, match='1'):
            cli.main()
    assert requests[-1]['path'] == '/v1/auth/logout'
    assert not target.exists()
    captured = capsys.readouterr()
    assert str(status) in captured.err
    assert SECRET not in captured.out + captured.err
    assert PASSWORD not in captured.out + captured.err


def test_existing_destination_is_never_overwritten_or_followed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """Refuse before even bootstrapping login when the destination already exists."""
    target = tmp_path / 'existing.json'
    target.write_text('owned-before-command')
    monkeypatch.setattr('sys.stdin', io.StringIO(json.dumps({'password': PASSWORD})))
    with platform() as (origin, requests):
        monkeypatch.setattr('sys.argv', arguments(origin, target))
        with pytest.raises(SystemExit):
            cli.main()
    assert not requests
    assert target.read_text() == 'owned-before-command'
    assert PASSWORD not in capsys.readouterr().err


@pytest.mark.parametrize('kind', SETUP_KINDS)
def test_each_existing_setup_kind_uses_only_its_owner_fields(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], kind: str
) -> None:
    """Exercise all twelve current setup paths without adding an auth permission."""
    destination = tmp_path / 'private' / 'receipt.json'
    private = {'password': PASSWORD}
    field = PRIVATE_FIELDS.get(kind)
    if field:
        private[field] = (
            'https://provider.example/private-token' if field == 'endpoint_url' else SECRET
        )
    monkeypatch.setattr('sys.stdin', io.StringIO(json.dumps(private)))
    with platform() as (origin, requests):
        args = arguments(origin, destination)
        args[args.index('form_webhook')] = kind
        if kind not in MINTING_KINDS:
            index = args.index('--secret-file')
            del args[index : index + 2]
        expected: dict[str, object] = {'setup_kind': kind, 'display_name': 'Terminal forms'}
        if field:
            expected[field] = private[field]
        if kind == 'stripe_webhook':
            args.extend(['--stripe-mode', 'test'])
            expected['stripe_livemode'] = False
        if kind == 'discord_webhook':
            args.extend(
                [
                    '--discord-application-id',
                    '12345',
                    '--discord-public-key',
                    'ab' * 32,
                    '--discord-protocol',
                    'webhook_events',
                ]
            )
            expected.update(
                discord_application_id='12345',
                discord_public_key='ab' * 32,
                discord_protocol='webhook_events',
            )
        monkeypatch.setattr('sys.argv', args)
        cli.main()
    assert requests[1]['body'] == expected
    assert destination.exists() == (kind in MINTING_KINDS)
    captured = capsys.readouterr()
    assert not captured.err
    assert all(value not in captured.out for value in private.values())


@pytest.mark.parametrize('status', [401, 403, 429, 503, 302])
def test_login_refusals_never_create_or_expose_response(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], status: int
) -> None:
    """Do not promote rejected credentials or follow redirects into a different origin."""
    target = tmp_path / 'private' / 'receipt.json'
    monkeypatch.setattr('sys.stdin', io.StringIO(json.dumps({'password': PASSWORD})))
    with platform(login_status=status) as (origin, requests):
        monkeypatch.setattr('sys.argv', arguments(origin, target))
        with pytest.raises(SystemExit):
            cli.main()
    assert all(not item['path'].endswith('/connections') for item in requests)
    assert requests[-1]['path'] == '/v1/auth/logout'
    assert not target.exists()
    captured = capsys.readouterr()
    assert str(status) in captured.err
    assert PASSWORD not in captured.err


@pytest.mark.parametrize('option', ['--password', '--webhook-secret', '--endpoint-url'])
def test_private_values_on_argv_are_rejected_without_echo(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], option: str
) -> None:
    """Even argparse's unknown-argument path must not repeat a pasted secret."""
    monkeypatch.setattr(
        'sys.argv', [*arguments('http://127.0.0.1:1', tmp_path / 'unused'), option, SECRET]
    )
    with pytest.raises(SystemExit):
        cli.main()
    captured = capsys.readouterr()
    assert SECRET not in captured.out + captured.err


def test_failed_private_save_reports_created_identity_without_retry(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """A disk failure after successful create cannot be presented as 'try create again'."""

    def failed_write(_self: object, _receipt: object) -> None:
        raise OSError(SECRET)

    monkeypatch.setattr(PrivateSetupFile, 'write', failed_write)
    monkeypatch.setattr('sys.stdin', io.StringIO(json.dumps({'password': PASSWORD})))
    with platform() as (origin, requests):
        monkeypatch.setattr('sys.argv', arguments(origin, tmp_path / 'private' / 'receipt.json'))
        with pytest.raises(SystemExit):
            cli.main()
    captured = capsys.readouterr()
    assert json.loads(captured.out) == {
        'connection_id': CONNECTION,
        'callback_url': f'{origin}/v1/connections/{CONNECTION}/events',
        'status': 'created_private_save_unconfirmed',
    }
    assert 'Do not recreate' in captured.err
    assert SECRET not in captured.out + captured.err
    assert sum(item['path'].endswith('/connections') for item in requests) == 1
    assert requests[-1]['path'].endswith('/logout')


def test_lost_create_ack_is_not_retried_and_logout_still_runs(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """Preserve uncertainty after the server may have committed the request."""
    monkeypatch.setattr('sys.stdin', io.StringIO(json.dumps({'password': PASSWORD})))
    with platform(drop_ack=True) as (origin, requests):
        monkeypatch.setattr('sys.argv', arguments(origin, tmp_path / 'private' / 'receipt.json'))
        with pytest.raises(SystemExit):
            cli.main()
    assert sum(item['path'].endswith('/connections') for item in requests) == 1
    assert requests[-1]['path'].endswith('/logout')
    assert 'outcome is unknown' in capsys.readouterr().err


@pytest.mark.parametrize('response', [SECRET, {'private': SECRET}, {'large': SECRET * 10000}])
def test_malformed_create_response_still_logs_out_without_body_echo(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    response: object,
) -> None:
    """Bound malformed or oversized success bodies without exposing their content."""
    monkeypatch.setattr('sys.stdin', io.StringIO(json.dumps({'password': PASSWORD})))
    with platform(response_body=response) as (origin, requests):
        monkeypatch.setattr('sys.argv', arguments(origin, tmp_path / 'private' / 'receipt.json'))
        with pytest.raises(SystemExit):
            cli.main()
    assert requests[-1]['path'].endswith('/logout')
    captured = capsys.readouterr()
    assert SECRET not in captured.out + captured.err


def test_private_prompt_refuses_getpass_echo_fallback(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """A terminal without hidden input must fail before a password can be echoed."""

    def fallback(_prompt: str) -> str:
        warnings.warn('terminal cannot hide input', getpass.GetPassWarning, stacklevel=2)
        return PASSWORD

    monkeypatch.setattr(getpass, 'getpass', fallback)
    monkeypatch.setattr('sys.stdin.isatty', lambda: True)
    args = arguments('http://127.0.0.1:1', tmp_path / 'private' / 'receipt.json')
    args.remove('--credentials-stdin')
    monkeypatch.setattr('sys.argv', args)
    with pytest.raises(SystemExit):
        cli.main()
    assert PASSWORD not in capsys.readouterr().err


@pytest.mark.parametrize(
    'origin',
    [
        'http://remote.example',
        'http://192.168.1.3',
        'https://user:secret@example.test',
        'https://example.test/path',
        'https://example.test?token=secret',
        'file:///tmp/output',
    ],
)
def test_unsafe_platform_destination_refuses_before_reserving_output(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, origin: str
) -> None:
    """Never send passwords to plaintext remote hosts or credential-bearing URLs."""
    target = tmp_path / 'private' / 'receipt.json'
    monkeypatch.setattr('sys.argv', arguments(origin, target))
    with pytest.raises(SystemExit):
        cli.main()
    assert not target.parent.exists()


@pytest.mark.parametrize(
    'raw',
    [
        '',
        '[]',
        '{"password":true}',
        '{"password":"short"}',
        '{"password":"long-enough","api_key":"never-allowed"}',
        'x' * 8193,
    ],
)
def test_invalid_private_input_never_reaches_login(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, raw: str
) -> None:
    """The deliberate stdin protocol is bounded and rejects unknown private fields."""
    target = tmp_path / 'private' / 'receipt.json'
    monkeypatch.setattr('sys.stdin', io.StringIO(raw))
    with platform() as (origin, requests):
        monkeypatch.setattr('sys.argv', arguments(origin, target))
        with pytest.raises(SystemExit):
            cli.main()
    assert not requests
    assert not target.exists()


@pytest.mark.parametrize(
    'body',
    [
        {'csrf_token': SECRET, 'csrf_header_name': 'Cookie'},
        {'csrf_token': 'injected\r\nvalue', 'csrf_header_name': 'x-csrf-token'},
        {'csrf_token': None, 'csrf_header_name': 'x-csrf-token'},
    ],
)
def test_malformed_csrf_response_never_sends_password(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    body: object,
) -> None:
    """Reject malformed session bootstrap fields before constructing private headers."""
    monkeypatch.setattr('sys.stdin', io.StringIO(json.dumps({'password': PASSWORD})))
    with platform(csrf_body=body) as (origin, requests):
        monkeypatch.setattr('sys.argv', arguments(origin, tmp_path / 'private' / 'receipt.json'))
        with pytest.raises(SystemExit):
            cli.main()
    assert not requests
    assert SECRET not in capsys.readouterr().err


@pytest.mark.parametrize('logout_status', [200, 403, 503])
def test_cookies_are_discarded_even_when_server_logout_is_unconfirmed(logout_status: int) -> None:
    """The wrapper does not become a persistent session product after failure."""
    with platform(logout_status=logout_status) as (origin, requests):
        session = HumanSetupSession(origin)
        session.login('developer@example.test', PASSWORD)
        assert session.has_cookies
        assert session.logout() == (logout_status == HTTPStatus.OK)
        assert not session.has_cookies
    assert requests[-1]['path'].endswith('/logout')


def test_current_configured_csrf_header_name_is_respected() -> None:
    """Consume the server's existing configured header, rather than imposing an X- prefix."""
    with platform(csrf_name='Csrf-Token') as (origin, requests):
        session = HumanSetupSession(origin)
        try:
            session.login('developer@example.test', PASSWORD)
        finally:
            session.logout()
    assert {key.lower(): value for key, value in requests[0]['headers'].items()}[
        'csrf-token'
    ] == 'bootstrap'
