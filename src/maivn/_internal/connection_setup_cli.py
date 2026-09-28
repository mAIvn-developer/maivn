"""One-shot terminal connection creation through the existing human-session API."""

from __future__ import annotations

import argparse
import getpass
import http.client
import json
import re
import sys
import warnings
from http import HTTPStatus
from typing import TYPE_CHECKING, NoReturn, cast
from urllib.parse import urlsplit
from uuid import UUID

from maivn._internal.config import LOCAL_BASE_URL, local_tool_base_url
from maivn._internal.connection_setup_file import PrivateSetupFile
from maivn._internal.connection_setup_http import (
    HumanSetupSession,
    SetupRefusalError,
    refuse,
    safe_origin,
)

if TYPE_CHECKING:
    from collections.abc import Sequence

MINTING_KINDS = frozenset(
    {
        'incoming_webhook',
        'database_webhook',
        'email_inbox',
        'form_webhook',
        'local_file_watch',
    }
)
PRIVATE_FIELDS = {
    'slack_app': 'signing_secret',
    'github_app': 'webhook_secret',
    'stripe_webhook': 'webhook_secret',
    'outgoing_webhook': 'endpoint_url',
    'slack_webhook': 'endpoint_url',
    'discord_output': 'endpoint_url',
}
SETUP_KINDS = sorted(MINTING_KINDS | PRIVATE_FIELDS.keys() | {'discord_webhook'})
MAX_PRIVATE_INPUT = 8192
MAX_NAME = 120
MIN_EMAIL = 3
MAX_EMAIL = 320
ASCII_SPACE = 32
MIN_PASSWORD = 8
MAX_PASSWORD = 256
MAX_MINTED_SECRET = 4096
MAX_DNS_NAME_LENGTH = 253


class _Parser(argparse.ArgumentParser):
    def error(self, message: str) -> NoReturn:
        del message
        self.exit(2, 'Invalid connection arguments; run maivn connections create --help.\n')


def _parser() -> argparse.ArgumentParser:
    parser = _Parser(
        prog='maivn connections create',
        description=(
            'Create one saved connection using a temporary human login. No API key or '
            'persistent login. HTTPS cookies stay in memory; local HTTP uses native curl '
            'and an owner-private temporary cookie folder, deleted after logout. '
            'A killed process can leave maivn-connection-session-* in your temporary directory; '
            'remove that private folder after the command stops. Password and provider credentials '
            'use private prompts. '
            'No trigger is registered and no local agent is started by this command.'
        ),
    )
    _ = parser.add_argument(
        '--base-url',
        default=None,
        help=(
            'The mAIvn API origin. Defaults to MAIVN_BASE_URL, or to '
            f'{LOCAL_BASE_URL} (a platform running on this machine) when unset.'
        ),
    )
    for name in ('project-id', 'email', 'name'):
        _ = parser.add_argument(f'--{name}', required=True)
    _ = parser.add_argument('--kind', required=True, choices=SETUP_KINDS)
    _ = parser.add_argument(
        '--secret-file',
        help='Required for minted secrets: new file in an owner-private directory; '
        'never overwritten.',
    )
    _ = parser.add_argument('--stripe-mode', choices=('test', 'live'))
    _ = parser.add_argument('--discord-application-id')
    _ = parser.add_argument('--discord-public-key')
    _ = parser.add_argument('--discord-protocol', choices=('interactions', 'webhook_events'))
    _ = parser.add_argument(
        '--credentials-stdin',
        action='store_true',
        help=(
            'Deliberately read one JSON line from stdin: password plus the required '
            'signing_secret (Slack app), webhook_secret (GitHub/Stripe), or endpoint_url '
            '(outgoing webhook). Never put these values in command arguments.'
        ),
    )
    return parser


def _base_url(options: argparse.Namespace) -> str:
    return cast('str | None', options.base_url) or local_tool_base_url()


def _public_body(options: argparse.Namespace) -> dict[str, object]:
    _ = safe_origin(_base_url(options))
    if str(UUID(options.project_id)) != options.project_id:
        refuse('invalid_project_id')
    if not 1 <= len(options.name) <= MAX_NAME or not MIN_EMAIL <= len(options.email) <= MAX_EMAIL:
        refuse('invalid_public_metadata')
    if any(ord(char) < ASCII_SPACE for char in options.name + options.email):
        refuse('invalid_public_metadata')
    if (options.kind in MINTING_KINDS) != bool(options.secret_file):
        refuse('minted_secret_requires_new_private_file')
    body: dict[str, object] = {'display_name': options.name, 'setup_kind': options.kind}
    if options.kind == 'stripe_webhook':
        if options.stripe_mode is None:
            refuse('stripe_mode_required')
        body['stripe_livemode'] = options.stripe_mode == 'live'
    elif options.stripe_mode is not None:
        refuse('stripe_mode_requires_stripe')
    body.update(_discord_fields(options))
    return body


def _discord_fields(options: argparse.Namespace) -> dict[str, object]:
    discord = (options.discord_application_id, options.discord_public_key, options.discord_protocol)
    if options.kind == 'discord_webhook':
        if (
            not all(discord)
            or re.fullmatch(r'[1-9][0-9]{0,19}', discord[0]) is None
            or int(discord[0]) >= 2**64
            or re.fullmatch(r'[0-9a-fA-F]{64}', discord[1]) is None
        ):
            refuse('invalid_discord_verification')
        return dict(
            zip(
                ('discord_application_id', 'discord_public_key', 'discord_protocol'),
                discord,
                strict=True,
            )
        )
    if any(value is not None for value in discord):
        refuse('discord_fields_require_discord')
    return {}


def _prompt(label: str) -> str:
    if not sys.stdin.isatty():
        refuse('private_prompt_requires_terminal_or_credentials_stdin')
    with warnings.catch_warnings():
        warnings.simplefilter('error', getpass.GetPassWarning)
        return getpass.getpass(f'{label} (private): ')


def _stdin_private(required: set[str]) -> dict[str, str]:
    raw = sys.stdin.readline(MAX_PRIVATE_INPUT + 1)
    if len(raw) > MAX_PRIVATE_INPUT:
        refuse('private_input_too_large')
    values: object = json.loads(raw)
    if not isinstance(values, dict):
        refuse('invalid_private_fields')
    source = cast('dict[str, object]', values)
    if set(source) != required or not all(isinstance(value, str) for value in source.values()):
        refuse('invalid_private_fields')
    return cast('dict[str, str]', source)


def _private_input(options: argparse.Namespace) -> dict[str, str]:
    field = PRIVATE_FIELDS.get(options.kind)
    required: set[str] = {'password'}
    if field:
        required.add(field)
    if options.credentials_stdin:
        private = _stdin_private(required)
    else:
        private = {'password': _prompt('Account password')}
        if field:
            private[field] = _prompt(field.replace('_', ' ').capitalize())
    if not MIN_PASSWORD <= len(private['password']) <= MAX_PASSWORD:
        refuse('invalid_password_length')
    if field:
        limit = 2048 if field == 'endpoint_url' else 500
        if not 1 <= len(private[field]) <= limit:
            refuse('invalid_provider_value')
        if field == 'endpoint_url':
            url = urlsplit(private[field])
            if url.scheme != 'https' or not url.hostname or url.username or url.password:
                refuse('output_endpoint_requires_https')
    return private


def _receipt(
    body: dict[str, object], origin: str, project_id: str, *, setup_kind: str
) -> dict[str, object]:
    value = body.get('connection')
    if not isinstance(value, dict):
        refuse('create_response_invalid', 201)
    connection = cast('dict[str, object]', value)
    identifier = connection.get('connection_id')
    if (
        not isinstance(identifier, str)
        or re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9._:-]{0,199}', identifier) is None
        or connection.get('project_id') != project_id
        or connection.get('status') != 'active'
    ):
        refuse('create_response_invalid', 201)
    path = body.get('callback_path')
    callback: str | None = None
    if path is not None:
        if not isinstance(path, str) or path != f'/v1/connections/{identifier}/events':
            refuse('create_response_invalid', 201)
        inbound = body.get('inbound_url')
        if inbound is not None:
            if not isinstance(inbound, str):
                refuse('create_response_invalid', 201)
            parsed = urlsplit(inbound)
            if parsed.path != path or parsed.query or parsed.fragment:
                refuse('create_response_invalid', 201)
            callback = safe_origin(f'{parsed.scheme}://{parsed.netloc}') + path
        else:
            callback = origin + path
    receipt: dict[str, object] = {
        'connection_id': identifier,
        'callback_url': callback,
        'status': 'created',
    }
    if setup_kind == 'email_inbox':
        receipt.update(_email_receipt(connection))
    return receipt


def _email_receipt(connection: dict[str, object]) -> dict[str, object]:
    local_part = connection.get('dropzone_local_part')
    address = connection.get('email_address')
    if local_part is not None and (
        not isinstance(local_part, str) or re.fullmatch(r'p-[0-9a-f]{32}', local_part) is None
    ):
        refuse('create_response_invalid', HTTPStatus.CREATED)
    if address is not None:
        if not isinstance(address, str) or not isinstance(local_part, str):
            refuse('create_response_invalid', HTTPStatus.CREATED)
        returned_local, separator, domain = address.partition('@')
        label = r'[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?'
        if (
            returned_local != local_part
            or not separator
            or len(domain) > MAX_DNS_NAME_LENGTH
            or re.fullmatch(rf'{label}(?:\.{label})+', domain) is None
        ):
            refuse('create_response_invalid', HTTPStatus.CREATED)
    return {'dropzone_local_part': local_part, 'email_address': address}


def _safe_output(receipt: dict[str, object], secrets: list[str]) -> None:
    safe = {
        key: '[redacted]'
        if isinstance(value, str) and any(secret and secret in value for secret in secrets)
        else value
        for key, value in receipt.items()
    }
    sys.stdout.write(json.dumps(safe, sort_keys=True) + '\n')


def _save_secret(
    issued: dict[str, object],
    receipt: dict[str, object],
    options: argparse.Namespace,
    destination: PrivateSetupFile | None,
    secrets: list[str],
) -> None:
    minted = issued.get('one_time_secret')
    if isinstance(minted, str):
        secrets.append(minted)
    if destination is not None:
        if not isinstance(minted, str) or not 1 <= len(minted) <= MAX_MINTED_SECRET:
            refuse('minted_secret_missing', HTTPStatus.CREATED)
        destination.write(
            {
                **receipt,
                'project_id': options.project_id,
                'setup_kind': options.kind,
                'one_time_secret': minted,
            }
        )


def _report_failure(
    error: BaseException,
    receipt: dict[str, object] | None,
    secrets: list[str],
    *,
    created: bool,
    attempted: bool,
) -> None:
    if receipt is not None:
        receipt['status'] = 'created_private_save_unconfirmed'
        _safe_output(receipt, secrets)
    stage = error.stage if isinstance(error, SetupRefusalError) else 'setup_failed'
    status = error.status if isinstance(error, SetupRefusalError) else None
    sys.stderr.write(json.dumps({'status': stage, 'http_status': status}) + '\n')
    _failure_advice(stage, status)
    if created or status == HTTPStatus.CREATED:
        sys.stderr.write(
            'Connection creation succeeded, but private setup output is unconfirmed. '
            'Do not recreate; inspect this connection in Studio or Portal.\n'
        )
    elif attempted and status is None:
        sys.stderr.write(
            'The create outcome is unknown. No request was retried; '
            'check existing connections in Studio or Portal before trying again.\n'
        )


def _failure_advice(stage: str, status: int | None) -> None:
    advice_by_status: dict[int | None, str] = {
        HTTPStatus.UNAUTHORIZED: 'Sign-in was refused or expired. Check the account in Portal.',
        HTTPStatus.FORBIDDEN: 'The platform refused this action. Connection creation requires '
        'the existing project-admin permission and a valid session.',
        HTTPStatus.UNPROCESSABLE_ENTITY: 'Check the setup kind and its required fields.',
        HTTPStatus.TOO_MANY_REQUESTS: 'The platform rate limit was reached. Try again later.',
        HTTPStatus.SERVICE_UNAVAILABLE: 'The platform is unavailable. '
        'Check its status before retrying.',
    }
    advice = advice_by_status.get(status)
    if advice is None and stage == 'setup_failed':
        advice = 'Check the selected private directory and input. Run '
        advice += 'maivn connections create --help for the required fields.'
    if stage == 'local_curl_required':
        advice = 'Local HTTP requires native curl on PATH. '
        advice += 'Install curl or use an HTTPS platform URL.'
    if advice is not None:
        sys.stderr.write(advice + '\n')


def _cleanup(session: HumanSetupSession | None, destination: PrivateSetupFile | None) -> None:
    if session is not None and not session.logout():
        sys.stderr.write(
            'Logout or temporary cookie cleanup could not be confirmed. '
            'Remove any maivn-connection-session-* folder in your temporary directory '
            'after this command stops; '
            'no create request was retried.\n'
        )
    if destination is not None:
        try:
            destination.close(remove_empty=True)
        except OSError:
            sys.stderr.write('Private file cleanup could not be confirmed.\n')


def run_connections(args: Sequence[str]) -> None:
    """Create once, save a minted secret privately, and always attempt logout."""
    parser = _parser()
    if not args or args[0] in {'-h', '--help'}:
        parser.print_help()
        return
    if args[0] != 'create':
        parser.error('unsupported operation')
    options = parser.parse_args(args[1:])
    destination: PrivateSetupFile | None = None
    session: HumanSetupSession | None = None
    created = False
    attempted = False
    receipt: dict[str, object] | None = None
    private: dict[str, str] = {}
    secrets: list[str] = []
    try:
        body = _public_body(options)
        if options.secret_file:
            destination = PrivateSetupFile(options.secret_file)
        session = HumanSetupSession(_base_url(options))
        private = _private_input(options)
        secrets = list(private.values())
        session.login(options.email, private['password'])
        body.update({key: value for key, value in private.items() if key != 'password'})
        csrf = session.csrf()
        attempted = True
        issued = session.request(
            'POST',
            f'/v1/projects/{options.project_id}/connections',
            stage='create',
            expected=201,
            body=body,
            csrf=csrf,
        )
        created = True
        receipt = _receipt(issued, session.origin, options.project_id, setup_kind=options.kind)
        _save_secret(issued, receipt, options, destination, secrets)
    except (
        OSError,
        ValueError,
        RuntimeError,
        http.client.HTTPException,
        EOFError,
        getpass.GetPassWarning,
    ) as error:
        _report_failure(error, receipt, secrets, created=created, attempted=attempted)
        raise SystemExit(1) from None
    finally:
        _cleanup(session, destination)
        private.clear()
    _safe_output(receipt, secrets)
