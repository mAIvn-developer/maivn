"""Terminal email setup returns validated public address metadata without exposing secrets."""

from __future__ import annotations

import io
import json
from typing import TYPE_CHECKING

import pytest

from maivn import cli
from tests.unit.test_connection_setup_cli import (
    CONNECTION,
    PASSWORD,
    PROJECT,
    SECRET,
    arguments,
    platform,
)

if TYPE_CHECKING:
    from pathlib import Path

LOCAL_PART = 'p-0123456789abcdef0123456789abcdef'


def _issued(local_part: object, address: object, *, secret: str = SECRET) -> dict[str, object]:
    return {
        'connection': {
            'connection_id': CONNECTION,
            'status': 'active',
            'project_id': PROJECT,
            'setup_kind': 'email_inbox',
            'dropzone_local_part': local_part,
            'email_address': address,
        },
        'callback_path': f'/v1/connections/{CONNECTION}/events',
        'one_time_secret': secret,
    }


@pytest.mark.parametrize('address', [f'{LOCAL_PART}@mock.invalid', None])
def test_email_receipt_includes_only_configured_public_mailbox_metadata(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    address: str | None,
) -> None:
    """Keep an unprovisioned domain null rather than manufacturing a deliverable address."""
    target = tmp_path / 'private' / 'email.json'
    monkeypatch.setattr('sys.stdin', io.StringIO(json.dumps({'password': PASSWORD})))
    with platform(response_body=_issued(LOCAL_PART, address)) as (origin, requests):
        args = arguments(origin, target)
        args[args.index('form_webhook')] = 'email_inbox'
        monkeypatch.setattr('sys.argv', args)
        cli.main()
    captured = capsys.readouterr()
    receipt = json.loads(captured.out)
    assert receipt['dropzone_local_part'] == LOCAL_PART
    assert receipt['email_address'] == address
    assert json.loads(target.read_text())['email_address'] == address
    assert requests[-1]['path'].endswith('/logout')
    assert PASSWORD not in captured.out + captured.err
    assert SECRET not in captured.out + captured.err


@pytest.mark.parametrize(
    ('local_part', 'address'),
    [
        (True, None),
        ('not-a-platform-inbox', None),
        (LOCAL_PART, True),
        (LOCAL_PART, 'other@mock.invalid'),
        (LOCAL_PART, f'{LOCAL_PART}@invalid\r\n{SECRET}'),
        (LOCAL_PART, f'{LOCAL_PART}@' + 'x' * 254),
        (None, f'{LOCAL_PART}@mock.invalid'),
    ],
)
def test_malformed_email_metadata_refuses_without_echo_or_recreation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    local_part: object,
    address: object,
) -> None:
    """An invalid success receipt remains a created-but-unconfirmed outcome, never a retry."""
    monkeypatch.setattr('sys.stdin', io.StringIO(json.dumps({'password': PASSWORD})))
    with platform(response_body=_issued(local_part, address)) as (origin, requests):
        args = arguments(origin, tmp_path / 'private' / 'email.json')
        args[args.index('form_webhook')] = 'email_inbox'
        monkeypatch.setattr('sys.argv', args)
        with pytest.raises(SystemExit):
            cli.main()
    captured = capsys.readouterr()
    assert not captured.out
    assert 'Do not recreate' in captured.err
    assert SECRET not in captured.err
    assert sum(request['path'].endswith('/connections') for request in requests) == 1
    assert requests[-1]['path'].endswith('/logout')


def test_non_email_receipt_does_not_project_mailbox_fields(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """Connection setup kind, not arbitrary response fields, chooses the public receipt shape."""
    monkeypatch.setattr('sys.stdin', io.StringIO(json.dumps({'password': PASSWORD})))
    with platform(response_body=_issued(LOCAL_PART, f'{LOCAL_PART}@mock.invalid')) as (origin, _):
        monkeypatch.setattr('sys.argv', arguments(origin, tmp_path / 'private' / 'form.json'))
        cli.main()
    receipt = json.loads(capsys.readouterr().out)
    assert 'email_address' not in receipt
    assert 'dropzone_local_part' not in receipt


def test_secret_sentinel_is_redacted_even_when_it_coincides_with_valid_email_metadata(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """Adding public address fields must retain the final known-secret output protection."""
    monkeypatch.setattr('sys.stdin', io.StringIO(json.dumps({'password': PASSWORD})))
    target = tmp_path / 'private' / 'email.json'
    issued = _issued(LOCAL_PART, f'{LOCAL_PART}@mock.invalid', secret=LOCAL_PART)
    with platform(response_body=issued) as (origin, _):
        args = arguments(origin, target)
        args[args.index('form_webhook')] = 'email_inbox'
        monkeypatch.setattr('sys.argv', args)
        cli.main()
    captured = capsys.readouterr()
    assert LOCAL_PART not in captured.out + captured.err
    receipt = json.loads(captured.out)
    assert receipt['dropzone_local_part'] == '[redacted]'
    assert receipt['email_address'] == '[redacted]'
    assert json.loads(target.read_text())['one_time_secret'] == LOCAL_PART
