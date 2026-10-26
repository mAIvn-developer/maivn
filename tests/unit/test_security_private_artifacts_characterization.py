"""Characterize the private-artifact client against a wire-level Data and Vault double.

Every test drives the public ``Client.private_artifacts`` API over ``httpx.MockTransport``
doubles: the Data plane issues capability-free tickets, the Vault double answers the
ticketed calls. Secrets (client proof, grant, session credential) must reach only the Vault.
"""

from __future__ import annotations

import asyncio
import base64
import copy
import json
import pickle
import re
import time
from typing import TYPE_CHECKING, Any, cast

import httpx
import pytest
from maivn_contracts.artifacts import (
    PrivateArtifactRef,
    PrivateWorkIntentStatus,
    VaultActionTicket,
    VaultGrantCredentialDelivery,
    VaultRetrievalSessionCredentialDelivery,
    VaultSenderAssertion,
    VaultWorkRequest,
    vault_actual_request_digest_from_prehashed,
    vault_assertion_json_bytes,
    vault_client_proof_header_digest,
    vault_grant_header_digest,
    vault_retrieval_session_credential_digest,
)

from maivn import (
    Client,
    PrivateArtifactError,
    PrivateArtifactTimeoutError,
    TransientVaultClientProof,
)
from maivn._internal.private_artifacts import trusted_origin

if TYPE_CHECKING:
    from collections.abc import AsyncIterator, Callable, Iterator, Mapping

_ORIGIN = 'https://vault.example.test'
_INTENT = 'server_intent_0001'
_OPERATION = 'operation_0001'
_SESSION = 'retrieval_session_0001'
_REFUSED = 'private_artifact_request_refused'
_SECURE = {'cache-control': 'no-store', 'x-content-type-options': 'nosniff'}
_PROOF_HEADER = 'x-maivn-client-proof'
_GRANT_HEADER = 'x-maivn-grant-credential'
_SESSION_HEADER = 'x-maivn-session-credential'
_DATA_PREFIX = '/v1/private-artifacts'
_LATER = '2099-08-14T13:00:00Z'


def _b64(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).rstrip(b'=').decode('ascii')


_GRANT = _b64(b'g' * 32)
_SESSION_CREDENTIAL = _b64(b's' * 32)


def _base_work() -> dict[str, Any]:
    return {
        'schema_version': 'vault-work-v1',
        'intent_id': 'client_intent_0001',
        'idempotency_key': 'idempotency_0001',
        'operation': 'delete',
        'scope': {
            'organization_id': '00000000-0000-4000-8000-000000000002',
            'project_id': '00000000-0000-4000-8000-000000000003',
        },
        'surface': {
            'root_invocation_id': 'root_invocation',
            'session_id': 'root_session',
            'checkpoint_id': 'checkpoint_current',
            'caller_id': '00000000-0000-4000-8000-000000000001',
        },
        'input_handles': [
            {
                'local_id': 'source_artifact',
                'record_handle': 'source_record_0001',
                'generation_handle': 'source_generation_0001',
                'role': 'source_artifact',
            },
        ],
        'bounds': {'max_output_bytes': 1_048_576},
        'retention_policy_id': 'retention_standard',
        'deletion_scope': {'mode': 'private_only'},
        'expires_at': _LATER,
    }


def _delete_work() -> VaultWorkRequest:
    return VaultWorkRequest.model_validate(_base_work())


def _retrieve_work(
    digest: str, *, part: str | None = None, max_bytes: int = 1_048_576
) -> VaultWorkRequest:
    payload = _base_work()
    payload.update(
        {
            'operation': 'retrieve_session',
            'deletion_scope': None,
            'client_proof_digest': digest,
            'retrieval_part': part,
            'bounds': {'max_output_bytes': max_bytes},
        }
    )
    return VaultWorkRequest.model_validate(payload)


def _ref() -> PrivateArtifactRef:
    return PrivateArtifactRef.model_validate(
        {
            'artifact_id': 'source_generation_0001',
            'logical_output_id': 'source_record_0001',
            'revision': 1,
            'kind': 'document',
            'mime_type': 'application/pdf',
            'created_at': '2026-09-05T12:00:00Z',
            'effective_retention': {
                'policy_snapshot_id': 'policy_standard',
                'retention_class': 'standard',
                'expires_at': '2099-09-05T12:00:00Z',
            },
            'producer': {
                'producer_class': 'vault',
                'producer_id': 'vault_renderer',
                'root_invocation_id': 'root_invocation',
                'session_id': 'root_session',
            },
            'custody': 'vault_private',
            'state': 'available_in_vault',
            'display_label': 'Private document',
            'creation_receipt_id': 'creation_receipt',
            'retrieval_action': {'relation': 'artifact.vault_download_authorization'},
        }
    )


def _ticket(  # noqa: PLR0913 - every signed-ticket input stays explicit.
    *,
    method: str,
    path: str,
    digests: Mapping[str, str],
    public_headers: Mapping[str, str] | None = None,
    origin: str = _ORIGIN,
    expires_at: str = '2099-08-14T12:01:00Z',
) -> dict[str, Any]:
    public = dict(public_headers or {})
    client_digests = cast('dict[Any, Any]', dict(digests))
    actual = vault_actual_request_digest_from_prehashed(
        method, path, b'', public, client_digests, expected_client_headers=frozenset(client_digests)
    )
    assertion = VaultSenderAssertion(
        issuer='data_plane',
        key_id='data_key_1',
        audience='maivn-vault-plane',
        actor='00000000-0000-4000-8000-000000000001',
        tenant_id='tenant_private',
        surface='data-plane-sdk',
        surface_digest='a' * 64,
        actual_request_digest=actual,
        issued_at='1999-12-31T23:59:00Z' if expires_at < '2099' else '2099-08-14T12:00:00Z',
        nonce='nonce_0000000000000000000000000001',
        expires_at=expires_at,
    )
    return VaultActionTicket.model_validate(
        {
            'method': method,
            'path': path,
            'url': f'{origin}{path}',
            'public_headers': public,
            'client_header_digests': client_digests,
            'sender_assertion_base64url': _b64(vault_assertion_json_bytes(assertion)),
            'sender_signature_base64url': _b64(b's' * 64),
            'body_base64url': None,
            'expires_at': expires_at,
        }
    ).model_dump(mode='json')


def _raw(status: int, body: bytes, headers: list[tuple[str, str]] | None = None) -> httpx.Response:
    """Build a response whose headers are exactly the ones given."""
    return httpx.Response(status, headers=headers or [], stream=httpx.ByteStream(body))


def _json_reply(status: int, payload: object) -> httpx.Response:
    body = json.dumps(payload).encode()
    return _raw(
        status,
        body,
        [
            ('content-type', 'application/json'),
            ('content-length', str(len(body))),
            ('cache-control', 'no-store'),
            ('x-content-type-options', 'nosniff'),
        ],
    )


def _range_headers(first: int, last: int, total: int, *, mime: str) -> list[tuple[str, str]]:
    return [
        ('accept-ranges', 'bytes'),
        ('cache-control', 'no-store'),
        ('content-disposition', 'attachment'),
        ('content-length', str(last - first + 1)),
        ('content-range', f'bytes {first}-{last}/{total}'),
        ('content-type', mime),
        ('x-content-type-options', 'nosniff'),
    ]


class Platform:
    """Wire-level Data plane and Vault doubles for one client."""

    def __init__(self, *, content: bytes = b'abcde', mime: str = 'application/pdf') -> None:
        """Start with default happy-path replies."""
        self.content = content
        self.mime = mime
        self.data: list[tuple[str, dict[str, Any]]] = []
        self.data_bodies: list[bytes] = []
        self.vault: list[httpx.Request] = []
        self.replies: dict[str, Callable[[httpx.Request], httpx.Response]] = {}
        self.ticket_digests: dict[str, dict[str, str]] = {}
        self.ticket_origins: dict[str, str] = {}
        self.ticket_expiry: dict[str, str] = {}
        self.resolved: Callable[[dict[str, Any]], dict[str, Any]] | None = None
        self.statuses: list[object] = []
        self.data_failure: BaseException | None = None
        self.path_failures: dict[str, BaseException] = {}

    def client(self, *, timeout_seconds: float = 5.0, base_url: str = _ORIGIN) -> Client:
        """Return a client whose Data and Vault traffic reach these doubles."""
        return Client(
            api_key='sdk-project-key',
            base_url=base_url,
            timeout_seconds=timeout_seconds,
            transport=httpx.MockTransport(self._data),
            vault_transport=httpx.MockTransport(self._vault),
        )

    @property
    def data_operations(self) -> list[str]:
        """Return the Data path tail, or the ticket operation, for every Data request."""
        return [
            payload['operation'] if path.endswith('/tickets') else path.removeprefix(_DATA_PREFIX)
            for path, payload in self.data
        ]

    @property
    def vault_operations(self) -> list[str]:
        """Return the last path segment of every Vault request."""
        return [request.url.path.rsplit('/', 1)[-1] for request in self.vault]

    def _data(self, request: httpx.Request) -> httpx.Response:
        if self.data_failure is not None:
            raise self.data_failure
        if request.url.path in self.path_failures:
            raise self.path_failures[request.url.path]
        payload = cast('dict[str, Any]', json.loads(request.content))
        self.data.append((request.url.path, payload))
        self.data_bodies.append(request.content)
        path = request.url.path.removeprefix(_DATA_PREFIX)
        if path == '/tickets':
            operation = payload['operation']
            method = 'GET' if operation == 'read_bytes' else 'POST'
            public = (
                {'range': f'bytes={payload["first"]}-{payload["last"]}'}
                if operation == 'read_bytes'
                else {}
            )
            ticket = _ticket(
                method=method,
                path=f'/v1/vault/ops/{operation}',
                digests=self.ticket_digests.get(
                    operation, cast('dict[str, str]', payload['client_header_digests'])
                ),
                public_headers=public,
                origin=self.ticket_origins.get(operation, _ORIGIN),
                expires_at=self.ticket_expiry.get(operation, '2099-08-14T12:01:00Z'),
            )
            return httpx.Response(200, json=ticket)
        if path == '/resolve':
            assert self.resolved is not None
            return httpx.Response(200, json=self.resolved(payload))
        assert path in {'/intents/bind-status', '/intents/status'}
        status = self.statuses.pop(0)
        return httpx.Response(200, json=status)

    def _vault(self, request: httpx.Request) -> httpx.Response:
        self.vault.append(request)
        operation = request.url.path.rsplit('/', 1)[-1]
        reply = self.replies.get(operation, self.default)
        return reply(request)

    def default(self, request: httpx.Request) -> httpx.Response:
        """Answer one ticketed Vault call with its happy-path reply."""
        operation = request.url.path.rsplit('/', 1)[-1]
        if operation == 'admit':
            return _json_reply(
                201, {'intent_id': _INTENT, 'state': 'authorization_pending', 'replayed': False}
            )
        if operation == 'approve':
            return _json_reply(
                200,
                VaultGrantCredentialDelivery(
                    intent_id=_INTENT,
                    state='executing_in_vault',
                    grant_credential_digest=vault_grant_header_digest(_GRANT),
                    credential_delivery='delivered',
                    grant_credential=_GRANT,
                    expires_at=_LATER,
                ).model_dump(mode='json'),
            )
        if operation == 'redeem':
            return _json_reply(200, {'operation_id': _OPERATION, 'replayed': False})
        if operation in {'delete', 'redacted_export'}:
            return _json_reply(
                202,
                {
                    'server_intent_id': _INTENT,
                    'operation_id': _OPERATION,
                    'operation': operation,
                    'state': 'queued',
                },
            )
        if operation == 'retrieve_session':
            return _json_reply(
                201,
                VaultRetrievalSessionCredentialDelivery(
                    schema_version='vault-retrieval-session-credential-delivery-v1',
                    session_id=_SESSION,
                    session_credential_digest=vault_retrieval_session_credential_digest(
                        _SESSION_CREDENTIAL
                    ),
                    credential_delivery='delivered',
                    session_credential=_SESSION_CREDENTIAL,
                    expires_at=_LATER,
                ).model_dump(mode='json'),
            )
        assert operation == 'read_bytes'
        match = re.fullmatch(r'bytes=(\d+)-(\d+)', request.headers['range'])
        assert match is not None
        first = int(match.group(1))
        last = min(int(match.group(2)), len(self.content) - 1)
        return _raw(
            206,
            self.content[first : last + 1],
            _range_headers(first, last, len(self.content), mime=self.mime),
        )


def _status(work: VaultWorkRequest, state: str = 'committed') -> dict[str, Any]:
    payload: dict[str, Any] = {
        'intent_id': _INTENT,
        'operation': work.operation,
        'correlation_id': work.intent_id,
        'idempotency_key': work.idempotency_key,
        'expires_at': work.expires_at,
        'state': state,
    }
    if state == 'committed' and work.operation == 'delete':
        payload['lifecycle_receipt'] = {
            'receipt_id': 'deletion_receipt_0001',
            'artifact_id': 'source_record_0001',
            'operation': 'delete',
            'state': 'deletion_pending_backup_expiry',
            'correlation_id': work.intent_id,
            'occurred_at': '2026-08-14T12:00:30Z',
        }
    return PrivateWorkIntentStatus.model_validate(payload).model_dump(mode='json')


def _resolve_to(
    work_for: Callable[[dict[str, Any]], VaultWorkRequest],
) -> Callable[[dict[str, Any]], dict[str, Any]]:
    return lambda payload: work_for(payload).model_dump(mode='json')


def _refused(caught: pytest.ExceptionInfo[PrivateArtifactError]) -> None:
    assert type(caught.value) is PrivateArtifactError
    assert str(caught.value) == _REFUSED
    assert caught.value.code == _REFUSED
    assert caught.value.__cause__ is None
    assert caught.value.__context__ is None


def _vault_header_values(platform: Platform, name: str) -> list[str | None]:
    return [request.headers.get(name) for request in platform.vault]


def test_proof_cannot_be_forged_copied_or_serialized() -> None:
    """Proofs come only from the client and refuse every copy or serialization path."""
    with pytest.raises(TypeError, match=f'^{_REFUSED}$'):
        TransientVaultClientProof('raw', _factory=object())
    proof = Client(api_key='k', base_url=_ORIGIN).private_artifacts.new_retrieval_proof()
    assert repr(proof) == str(proof) == 'TransientVaultClientProof(<redacted>)'
    assert re.fullmatch(r'[0-9a-f]{64}', proof.digest) is not None
    for attempt in (copy.copy, copy.deepcopy, pickle.dumps):
        with pytest.raises(TypeError, match=f'^{_REFUSED}$'):
            attempt(proof)
    with pytest.raises(TypeError, match=f'^{_REFUSED}$'):
        proof.__reduce__()
    with pytest.raises(AttributeError):
        _ = proof.__dict__


def test_delete_sends_each_secret_only_to_its_vault_call() -> None:
    """Data sees digests only; the grant goes only to redemption; Vault never sees the API key."""
    platform = Platform()
    platform.statuses = [_status(_delete_work())]
    status = platform.client().private_artifacts.delete(_delete_work())
    assert status.state == 'committed'
    assert platform.data_operations == [
        'admit',
        'approve',
        'redeem',
        'delete',
        '/intents/bind-status',
    ]
    redeem = platform.data[2][1]
    assert redeem['server_intent_id'] == _INTENT
    assert redeem['client_header_digests'] == {_GRANT_HEADER: vault_grant_header_digest(_GRANT)}
    assert platform.data[3][1]['operation_id'] == _OPERATION
    assert platform.data[4][1] == {
        'server_intent_id': _INTENT,
        'operation_id': _OPERATION,
        'operation': 'delete',
        'state': 'queued',
    }
    assert all(_GRANT.encode() not in body for body in platform.data_bodies)
    assert platform.vault_operations == ['admit', 'approve', 'redeem', 'delete']
    assert _vault_header_values(platform, _GRANT_HEADER) == [None, None, _GRANT, None]
    assert _vault_header_values(platform, 'authorization') == [None] * 4
    assert _vault_header_values(platform, 'accept-encoding') == ['identity'] * 4
    assert all(
        request.headers['x-maivn-sender-signature'] == _b64(b's' * 64) for request in platform.vault
    )


def test_plain_json_work_is_normalized_like_a_model() -> None:
    """A JSON-shaped work mapping is validated and used like the model it encodes."""
    platform = Platform()
    platform.statuses = [_status(_delete_work())]
    work = cast('VaultWorkRequest', _delete_work().model_dump(mode='json'))
    assert platform.client().private_artifacts.delete(work).state == 'committed'


def test_effect_status_polls_until_committed() -> None:
    """A pending bound status is re-read by its server handle until committed."""
    platform = Platform()
    work = _delete_work()
    platform.statuses = [_status(work, 'authorization_pending'), _status(work)]
    assert platform.client().private_artifacts.delete(work).state == 'committed'
    assert platform.data_operations[-2:] == ['/intents/bind-status', '/intents/status']
    assert platform.data[-1][1] == {
        'schema_version': 'vault-intent-status-request-v1',
        'server_intent_id': _INTENT,
    }


def test_effect_status_times_out_when_never_committed() -> None:
    """A status that never commits ends in the typed timeout after the bounded wait."""
    platform = Platform()
    work = _delete_work()
    platform.statuses = [_status(work, 'executing_in_vault')] * 50
    started = time.monotonic()
    with pytest.raises(PrivateArtifactTimeoutError):
        platform.client(timeout_seconds=0.2).private_artifacts.delete(work)
    assert time.monotonic() - started < 3  # noqa: PLR2004 - bounded by the client timeout.


@pytest.mark.parametrize('fault', ['malformed', 'foreign_correlation'])
def test_unbound_effect_status_is_refused(fault: str) -> None:
    """A status that does not parse or binds another intent is refused."""
    platform = Platform()
    work = _delete_work()
    status = _status(work)
    if fault == 'malformed':
        platform.statuses = [{'state': 'committed'}]
    else:
        platform.statuses = [{**status, 'correlation_id': 'other_intent_0001'}]
    with pytest.raises(PrivateArtifactError) as caught:
        platform.client().private_artifacts.delete(work)
    _refused(caught)


def test_status_lookup_requires_the_requested_intent() -> None:
    """A status for another server intent, or a Data failure, is refused."""
    platform = Platform()
    work = _delete_work()
    platform.statuses = [_status(work), {**_status(work), 'intent_id': 'server_intent_0002'}]
    client = platform.client()
    assert client.private_artifacts.status(_INTENT).intent_id == _INTENT
    with pytest.raises(PrivateArtifactError) as caught:
        client.private_artifacts.status(_INTENT)
    _refused(caught)
    platform.data_failure = httpx.ConnectError('sentinel-network-detail')
    with pytest.raises(PrivateArtifactError) as failed:
        client.private_artifacts.status(_INTENT)
    _refused(failed)


def test_missing_grant_stops_before_redemption() -> None:
    """A non-replayable approval without a grant cannot be redeemed for other operations."""
    platform = Platform()
    platform.replies['approve'] = lambda _request: _json_reply(
        200,
        VaultGrantCredentialDelivery(
            intent_id=_INTENT,
            state='executing_in_vault',
            grant_credential_digest=vault_grant_header_digest(_GRANT),
            credential_delivery='not_replayable',
            grant_credential=None,
            expires_at=_LATER,
        ).model_dump(mode='json'),
    )
    with pytest.raises(PrivateArtifactError) as caught:
        platform.client().private_artifacts.delete(_delete_work())
    _refused(caught)
    assert platform.data_operations == ['admit', 'approve']


@pytest.mark.parametrize(
    'accepted',
    [
        {'operation_id': 'operation_0002', 'operation': 'delete'},
        {'operation_id': _OPERATION, 'operation': 'redacted_export'},
    ],
)
def test_effect_acceptance_must_match_the_redeemed_operation(accepted: dict[str, str]) -> None:
    """Acceptance for another operation id or operation never reaches status binding."""
    platform = Platform()
    platform.replies['delete'] = lambda _request: _json_reply(
        202, {'server_intent_id': _INTENT, 'state': 'queued', **accepted}
    )
    with pytest.raises(PrivateArtifactError) as caught:
        platform.client().private_artifacts.delete(_delete_work())
    _refused(caught)
    assert '/intents/bind-status' not in platform.data_operations


@pytest.mark.parametrize('variant', ['wrong', 'missing', 'extra', 'proof_only'])
def test_ticket_digests_must_name_exactly_the_attached_secrets(variant: str) -> None:
    """A ticket whose digests differ from the attached grant never reaches Vault."""
    grant = vault_grant_header_digest(_GRANT)
    variants: dict[str, dict[str, str]] = {
        'wrong': {_GRANT_HEADER: 'a' * 64},
        'missing': {},
        'extra': {_GRANT_HEADER: grant, _PROOF_HEADER: 'b' * 64},
        'proof_only': {_PROOF_HEADER: grant},
    }
    digests = variants[variant]
    platform = Platform()
    platform.ticket_digests['redeem'] = digests
    with pytest.raises(PrivateArtifactError) as caught:
        platform.client().private_artifacts.delete(_delete_work())
    _refused(caught)
    assert platform.vault_operations == ['admit', 'approve']


@pytest.mark.parametrize(
    'origin',
    ['https://vault.example.test:8443', 'http://vault.example.test', 'https://other.example.test'],
)
def test_ticket_for_another_origin_is_refused(origin: str) -> None:
    """The ticket origin must match the configured origin exactly, scheme and port included."""
    platform = Platform()
    platform.ticket_origins['admit'] = origin
    with pytest.raises(PrivateArtifactError) as caught:
        platform.client().private_artifacts.delete(_delete_work())
    _refused(caught)
    assert platform.vault == []


def test_expired_ticket_is_refused() -> None:
    """A ticket whose expiry has passed never reaches Vault."""
    platform = Platform()
    platform.ticket_expiry['admit'] = '2000-01-01T00:00:00Z'
    with pytest.raises(PrivateArtifactError) as caught:
        platform.client().private_artifacts.delete(_delete_work())
    _refused(caught)
    assert platform.vault == []


def _admit_body() -> bytes:
    return json.dumps(
        {'intent_id': _INTENT, 'state': 'authorization_pending', 'replayed': False}
    ).encode()


def _padded_admit(size: int) -> bytes:
    body = _admit_body()
    return body[:-1] + b' ' * (size - len(body)) + b'}'


def _admit_headers(**changes: str | None) -> list[tuple[str, str]]:
    headers = {
        'content-type': 'application/json',
        'content-length': str(len(_admit_body())),
        **_SECURE,
    }
    for name, value in changes.items():
        key = name.replace('_', '-')
        if value is None:
            headers.pop(key, None)
        else:
            headers[key] = value
    return list(headers.items())


@pytest.mark.parametrize(
    ('status', 'headers', 'body'),
    [
        (302, [*_admit_headers(), ('location', 'https://evil.example.test/steal')], _admit_body()),
        (500, _admit_headers(), _admit_body()),
        (199, _admit_headers(), _admit_body()),
        (201, _admit_headers(cache_control=None), _admit_body()),
        (201, _admit_headers(cache_control='private'), _admit_body()),
        (201, _admit_headers(x_content_type_options=None), _admit_body()),
        (201, _admit_headers(content_encoding='identity'), _admit_body()),
        (201, _admit_headers(transfer_encoding='chunked'), _admit_body()),
        (201, _admit_headers(content_length=None), _admit_body()),
        (201, [*_admit_headers(), ('content-length', str(len(_admit_body())))], _admit_body()),
        (201, _admit_headers(content_length='0' + str(len(_admit_body()))), _admit_body()),
        (201, _admit_headers(content_length='+' + str(len(_admit_body()))), _admit_body()),
        (201, _admit_headers(content_length=str(len(_admit_body()) + 1)), _admit_body()),
        (201, _admit_headers(content_length=str(len(_admit_body()) - 1)), _admit_body()),
        (201, _admit_headers(content_length='16385'), _padded_admit(16385)),
        (201, [*_admit_headers(), (_GRANT_HEADER, _GRANT)], _admit_body()),
        (201, [*_admit_headers(), (_PROOF_HEADER, 'x')], _admit_body()),
        (201, [*_admit_headers(), (_SESSION_HEADER, 'x')], _admit_body()),
    ],
)
def test_control_responses_are_strictly_bounded(
    status: int, headers: list[tuple[str, str]], body: bytes
) -> None:
    """Redirects, failures, unsafe caching, encodings, bad lengths or echoed secrets refuse."""
    platform = Platform()
    platform.replies['admit'] = lambda _request: _raw(status, body, headers)
    with pytest.raises(PrivateArtifactError) as caught:
        platform.client().private_artifacts.delete(_delete_work())
    _refused(caught)
    assert platform.vault_operations == ['admit']


def test_control_response_at_the_size_limit_is_read() -> None:
    """A control reply of exactly 16384 declared and sent bytes is accepted."""
    platform = Platform()
    padded = _padded_admit(16384)
    platform.replies['admit'] = lambda _request: _raw(
        201, padded, _admit_headers(content_length='16384')
    )
    platform.statuses = [_status(_delete_work())]
    assert platform.client().private_artifacts.delete(_delete_work()).state == 'committed'


def _retrieval(
    platform: Platform, *, part: str | None = None, max_bytes: int = 1_048_576
) -> tuple[Client, TransientVaultClientProof, VaultWorkRequest]:
    client = platform.client()
    proof = client.private_artifacts.new_retrieval_proof()
    return client, proof, _retrieve_work(proof.digest, part=part, max_bytes=max_bytes)


def test_retrieval_attaches_proof_and_session_secrets_only_to_vault() -> None:
    """Ranges are read with both raw secrets attached locally and digests sent to Data."""
    platform = Platform(content=b'abcde')
    client, proof, work = _retrieval(platform)
    assert client.private_artifacts.download(work, proof=proof, chunk_bytes=2) == b'abcde'
    assert platform.data_operations == [
        'admit',
        'approve',
        'redeem',
        'retrieve_session',
        'read_bytes',
        'read_bytes',
        'read_bytes',
    ]
    reads = [
        payload for _path, payload in platform.data if payload.get('operation') == 'read_bytes'
    ]
    assert [(read['first'], read['last'], read['session_id']) for read in reads] == [
        (0, 1, _SESSION),
        (2, 3, _SESSION),
        (4, 5, _SESSION),
    ]
    raw_proof = platform.vault[3].headers[_PROOF_HEADER]
    assert re.fullmatch(r'[A-Za-z0-9_-]{43}', raw_proof) is not None
    assert vault_client_proof_header_digest(raw_proof) == proof.digest
    assert reads[0]['client_header_digests'] == {
        _PROOF_HEADER: proof.digest,
        _SESSION_HEADER: vault_retrieval_session_credential_digest(_SESSION_CREDENTIAL),
    }
    assert _vault_header_values(platform, _PROOF_HEADER) == [None] * 3 + [raw_proof] * 4
    assert _vault_header_values(platform, _SESSION_HEADER) == [None] * 4 + [_SESSION_CREDENTIAL] * 3
    for secret in (raw_proof, _SESSION_CREDENTIAL, _GRANT):
        assert all(secret.encode() not in body for body in platform.data_bodies)


@pytest.mark.parametrize('chunk_bytes', [0, 65_537, True, 2.0, -1])
def test_chunk_size_outside_one_to_65536_is_refused_before_network(chunk_bytes: object) -> None:
    """Only an integer chunk size between 1 and 65536 bytes is accepted."""
    platform = Platform()
    client, proof, work = _retrieval(platform)
    with pytest.raises(PrivateArtifactError) as caught:
        client.private_artifacts.download(work, proof=proof, chunk_bytes=cast('int', chunk_bytes))
    _refused(caught)
    assert platform.data == []


def test_largest_chunk_reads_the_whole_artifact_in_one_range() -> None:
    """A 65536-byte chunk requests one range covering up to 65536 bytes."""
    platform = Platform(content=b'xyz')
    client, proof, work = _retrieval(platform)
    assert client.private_artifacts.download(work, proof=proof, chunk_bytes=65_536) == b'xyz'
    reads = [
        payload for _path, payload in platform.data if payload.get('operation') == 'read_bytes'
    ]
    assert [(read['first'], read['last']) for read in reads] == [(0, 65_535)]


@pytest.mark.parametrize('proof_value', ['raw-proof-string', None, object()])
def test_retrieval_requires_a_client_issued_proof(proof_value: object) -> None:
    """Anything other than a client-issued proof object is refused before network."""
    platform = Platform()
    client, proof, _work = _retrieval(platform)
    work = _retrieve_work(proof.digest)

    async def collect() -> list[bytes]:
        stream = client.private_artifacts.aretrieve_stream(
            work, proof=cast('TransientVaultClientProof', proof_value)
        )
        return [chunk async for chunk in stream]

    with pytest.raises(PrivateArtifactError):
        asyncio.run(collect())
    with pytest.raises(PrivateArtifactError):
        client.private_artifacts.resolve(
            _ref(), session_id='root_session', proof=cast('TransientVaultClientProof', proof_value)
        )
    assert platform.data == []


def test_undelivered_session_credential_stops_before_any_range() -> None:
    """A session without a delivered credential cannot request bytes."""
    platform = Platform()
    platform.replies['retrieve_session'] = lambda _request: _json_reply(
        201,
        VaultRetrievalSessionCredentialDelivery(
            schema_version='vault-retrieval-session-credential-delivery-v1',
            session_id=_SESSION,
            session_credential_digest=vault_retrieval_session_credential_digest(
                _SESSION_CREDENTIAL
            ),
            credential_delivery='not_replayable',
            session_credential=None,
            expires_at=_LATER,
        ).model_dump(mode='json'),
    )
    client, proof, work = _retrieval(platform)
    with pytest.raises(PrivateArtifactError):
        client.private_artifacts.download(work, proof=proof)
    assert 'read_bytes' not in platform.data_operations


def _range_reply(  # noqa: PLR0913 - one keyword per header override.
    first: int,
    last: int,
    total: int,
    body: bytes,
    *,
    status: int = 206,
    mime: str = 'application/pdf',
    **changes: str | None,
) -> Callable[[httpx.Request], httpx.Response]:
    headers = dict(_range_headers(first, last, total, mime=mime))
    for name, value in changes.items():
        key = name.replace('_', '-')
        if value is None:
            headers.pop(key, None)
        else:
            headers[key] = value
    return lambda _request: _raw(status, body, list(headers.items()))


@pytest.mark.parametrize(
    ('reply', 'chunk_bytes'),
    [
        (_range_reply(0, 2, 3, b'abc', status=200), 3),
        (_range_reply(0, 2, 3, b'abc', accept_ranges='none'), 3),
        (_range_reply(0, 2, 3, b'abc', content_disposition='inline'), 3),
        (_range_reply(0, 2, 3, b'abc', mime='text/html'), 3),
        (_range_reply(0, 2, 3, b'abc', content_type=None), 3),
        (_range_reply(0, 2, 3, b'abc', content_range='bytes 0-2/x'), 3),
        (_range_reply(0, 2, 3, b'abc', content_range='bytes 00-2/3'), 3),
        (_range_reply(1, 2, 3, b'bc'), 3),
        (_range_reply(0, 2, 3, b'abc'), 2),
        (_range_reply(0, 3, 3, b'abcd'), 4),
        (_range_reply(0, 2, 3, b'abc', content_range='bytes 0-2/0'), 3),
        (_range_reply(0, 1, 3, b'abc', content_length='3'), 3),
    ],
    ids=[
        'status-200',
        'accept-ranges',
        'inline',
        'html',
        'no-type',
        'total-not-number',
        'leading-zero',
        'wrong-start',
        'beyond-request',
        'beyond-total',
        'zero-total',
        'length-not-range',
    ],
)
def test_range_responses_must_match_the_requested_window(
    reply: Callable[[httpx.Request], httpx.Response], chunk_bytes: int
) -> None:
    """Status, headers, offsets, totals and lengths must all describe the requested range."""
    platform = Platform(content=b'abc')
    reads: list[httpx.Request] = []

    def first_reply_only(request: httpx.Request) -> httpx.Response:
        reads.append(request)
        return reply(request) if len(reads) == 1 else platform.default(request)

    platform.replies['read_bytes'] = first_reply_only
    client, proof, work = _retrieval(platform)
    with pytest.raises(PrivateArtifactError):
        client.private_artifacts.download(work, proof=proof, chunk_bytes=chunk_bytes)
    assert len(reads) == 1


def _next_reply(
    replies: Iterator[Callable[[httpx.Request], httpx.Response]], request: httpx.Request
) -> httpx.Response:
    return next(replies)(request)


def test_range_total_cannot_change_between_ranges() -> None:
    """The first range fixes the total size for every later range."""
    platform = Platform(content=b'abcd')
    replies = iter(
        [
            _range_reply(0, 1, 4, b'ab'),
            _range_reply(2, 3, 5, b'cd'),
        ]
    )
    platform.replies['read_bytes'] = lambda request: _next_reply(replies, request)
    client, proof, work = _retrieval(platform)
    with pytest.raises(PrivateArtifactError):
        client.private_artifacts.download(work, proof=proof, chunk_bytes=2)


def test_artifact_larger_than_the_admitted_bound_is_refused() -> None:
    """A total above the work's byte bound is refused before its bytes are yielded."""
    platform = Platform(content=b'abcde')
    client, proof, work = _retrieval(platform, max_bytes=4)
    received: list[bytes] = []

    async def collect() -> None:
        async for chunk in client.private_artifacts.aretrieve_stream(work, proof=proof):
            received.append(chunk)  # noqa: PERF401 - observe chunks before the refusal.

    with pytest.raises(PrivateArtifactError):
        asyncio.run(collect())
    assert received == []


def test_exact_bound_is_accepted() -> None:
    """A total equal to the work's byte bound is accepted."""
    platform = Platform(content=b'abcd')
    client, proof, work = _retrieval(platform, max_bytes=4)
    assert client.private_artifacts.download(work, proof=proof) == b'abcd'


def test_range_body_above_the_range_limit_is_refused() -> None:
    """A range reply declaring more than 65536 bytes is refused before buffering."""
    platform = Platform()
    platform.replies['read_bytes'] = _range_reply(
        0, 65_536, 65_537, b'x' * 65_537, content_length='65537'
    )
    client, proof, work = _retrieval(platform)
    with pytest.raises(PrivateArtifactError):
        client.private_artifacts.download(work, proof=proof)


def test_editable_source_download_uses_fresh_source_authority() -> None:
    """Source downloads resolve source-specific work and accept only octet-stream ranges."""
    platform = Platform(content=b'source-bytes', mime='application/octet-stream')
    platform.resolved = _resolve_to(
        lambda payload: _retrieve_work(payload['client_proof_digest'], part='editable_source')
    )
    client = platform.client()
    assert client.private_artifacts.download_source(_ref(), session_id='followup') == (
        b'source-bytes'
    )
    resolve = platform.data[0][1]
    assert resolve['retrieval_part'] == 'editable_source'
    assert resolve['session_id'] == 'followup'
    assert resolve['artifact_ref'] == _ref().model_dump(mode='json')
    assert set(resolve) == {'artifact_ref', 'session_id', 'client_proof_digest', 'retrieval_part'}


def test_editable_source_refuses_document_mime_ranges() -> None:
    """A source range labelled with a document type is refused."""
    platform = Platform(content=b'source', mime='application/pdf')
    platform.resolved = _resolve_to(
        lambda payload: _retrieve_work(payload['client_proof_digest'], part='editable_source')
    )
    with pytest.raises(PrivateArtifactError):
        platform.client().private_artifacts.download_source(_ref(), session_id='followup')


def test_sync_stream_survives_a_slow_consumer() -> None:
    """The synchronous bridge delivers every range even when the caller is slow."""
    platform = Platform(content=b'abc')
    client, proof, work = _retrieval(platform)
    chunks: list[bytes] = []
    for chunk in client.private_artifacts.retrieve_stream(work, proof=proof, chunk_bytes=1):
        time.sleep(0.15)
        chunks.append(chunk)
    assert chunks == [b'a', b'b', b'c']


def test_sync_stream_raises_the_failure_in_the_caller() -> None:
    """A failure inside the worker is raised to the iterating caller."""
    platform = Platform(content=b'abc')
    replies = iter([_range_reply(0, 0, 3, b'a'), _range_reply(1, 1, 9, b'b')])
    platform.replies['read_bytes'] = lambda request: _next_reply(replies, request)
    client, proof, work = _retrieval(platform)
    stream: Iterator[bytes] = client.private_artifacts.retrieve_stream(
        work, proof=proof, chunk_bytes=1
    )
    assert next(stream) == b'a'
    with pytest.raises(PrivateArtifactError):
        next(stream)


def test_non_boolean_export_scope_is_refused_before_network() -> None:
    """Only a real boolean may widen a deletion to redacted exports."""
    platform = Platform()
    with pytest.raises(PrivateArtifactError) as caught:
        platform.client().private_artifacts.delete(
            _ref(), session_id='root_session', include_redacted_exports=cast('bool', 1)
        )
    _refused(caught)
    assert platform.data == []


@pytest.mark.parametrize(
    ('include', 'mode'), [(False, 'private_only'), (True, 'include_redacted_exports')]
)
def test_reference_delete_resolves_the_exact_deletion_scope(*, include: bool, mode: str) -> None:
    """A reference deletion asks Data for exactly the chosen scope and checks the answer."""
    platform = Platform()

    def resolved(payload: dict[str, Any]) -> VaultWorkRequest:
        work = _base_work()
        work['deletion_scope'] = payload['deletion_scope']
        return VaultWorkRequest.model_validate(work)

    platform.resolved = _resolve_to(resolved)
    work = resolved({'deletion_scope': {'mode': mode}})
    platform.statuses = [_status(work)]
    status = platform.client().private_artifacts.delete(
        _ref(), session_id='followup', include_redacted_exports=include
    )
    assert status.state == 'committed'
    assert platform.data[0][1] == {
        'artifact_ref': _ref().model_dump(mode='json'),
        'session_id': 'followup',
        'operation': 'delete',
        'deletion_scope': {'mode': mode},
    }


def test_reference_delete_refuses_a_widened_scope() -> None:
    """Data cannot widen a private-only deletion to include exports."""
    platform = Platform()

    def widened(_payload: dict[str, Any]) -> VaultWorkRequest:
        work = _base_work()
        work['deletion_scope'] = {'mode': 'include_redacted_exports'}
        return VaultWorkRequest.model_validate(work)

    platform.resolved = _resolve_to(widened)
    with pytest.raises(PrivateArtifactError) as caught:
        platform.client().private_artifacts.delete(_ref(), session_id='followup')
    _refused(caught)
    assert platform.vault == []


def test_ingest_requires_the_proof_named_by_the_work() -> None:
    """Ingestion with a proof other than the one the work admitted is refused offline."""
    platform = Platform()
    client = platform.client()
    proof = client.private_artifacts.new_retrieval_proof()
    work = VaultWorkRequest.model_validate(
        {
            **_base_work(),
            'operation': 'ingest_file',
            'input_handles': [],
            'deletion_scope': None,
            'client_proof_digest': 'c' * 64,
            'file_intake': {
                'kind': 'document',
                'mime_type': 'application/pdf',
                'max_source_bytes': 1024,
                'output_commitment': 'a' * 64,
                'source_commitment': 'b' * 64,
            },
        }
    )
    with pytest.raises(PrivateArtifactError):
        client.private_artifacts.ingest_file(work, proof=proof, output='unused', source='unused')
    assert platform.data == []


def test_cancellation_is_never_converted_into_a_refusal() -> None:
    """Cancellation inside Data traffic propagates as cancellation."""
    platform = Platform()
    platform.data_failure = asyncio.CancelledError()
    client = platform.client()
    proof = client.private_artifacts.new_retrieval_proof()
    with pytest.raises(asyncio.CancelledError):
        asyncio.run(client.private_artifacts.aresolve(_ref(), session_id='s', proof=proof))
    with pytest.raises(asyncio.CancelledError):
        asyncio.run(client.private_artifacts.astatus(_INTENT))
    with pytest.raises(asyncio.CancelledError):
        asyncio.run(client.private_artifacts.adelete(_ref(), session_id='s'))


class _CancelledBody(httpx.AsyncByteStream):
    """A response body whose read is cancelled."""

    async def __aiter__(self) -> AsyncIterator[bytes]:
        raise asyncio.CancelledError
        yield b''  # pragma: no cover - makes this an async generator.


def test_cancellation_during_status_binding_or_body_reads_propagates() -> None:
    """Cancelled status binding and cancelled Vault body reads stay cancellations."""
    platform = Platform()
    platform.path_failures['/v1/private-artifacts/intents/bind-status'] = asyncio.CancelledError()
    with pytest.raises(asyncio.CancelledError):
        asyncio.run(platform.client().private_artifacts.adelete(_delete_work()))
    body = Platform()
    body.replies['admit'] = lambda _request: httpx.Response(
        201,
        headers=[('content-length', '10'), *_SECURE.items()],
        stream=_CancelledBody(),
    )
    with pytest.raises(asyncio.CancelledError):
        asyncio.run(body.client().private_artifacts.adelete(_delete_work()))


def test_resolution_for_another_root_invocation_is_refused() -> None:
    """Resolved work bound to another root invocation never reaches Vault."""
    platform = Platform()

    def other_root(payload: dict[str, Any]) -> VaultWorkRequest:
        work = _retrieve_work(payload['client_proof_digest']).model_dump(mode='json')
        work['surface']['root_invocation_id'] = 'other_root'
        return VaultWorkRequest.model_validate(work)

    platform.resolved = _resolve_to(other_root)
    with pytest.raises(PrivateArtifactError) as caught:
        platform.client().private_artifacts.download(_ref(), session_id='root_session')
    _refused(caught)
    assert platform.vault == []


def test_export_resolution_must_keep_the_requested_filename() -> None:
    """Data cannot substitute the export filename the caller asked for."""
    platform = Platform()
    requested: list[dict[str, Any]] = []

    def renamed(payload: dict[str, Any]) -> VaultWorkRequest:
        requested.append(payload)
        work = _base_work()
        work.update(
            {'operation': 'redacted_export', 'deletion_scope': None, 'export_filename': 'Other.pdf'}
        )
        return VaultWorkRequest.model_validate(work)

    platform.resolved = _resolve_to(renamed)
    with pytest.raises(PrivateArtifactError) as caught:
        platform.client().private_artifacts.export_redacted(
            _ref(), session_id='root_session', filename='Report.pdf'
        )
    _refused(caught)
    assert requested[0]['export_filename'] == 'Report.pdf'
    assert requested[0]['operation'] == 'redacted_export'
    assert platform.vault == []


@pytest.mark.parametrize(
    'base_url',
    [
        f'{_ORIGIN}/?tenant=other',
        f'{_ORIGIN}/#fragment',
        'https://user@vault.example.test',
        f'{_ORIGIN}/base',
        'http://vault.example.test',
    ],
)
def test_private_bytes_need_a_bare_tls_origin(base_url: str) -> None:
    """Queries, fragments, credentials, paths or plain HTTP never carry private calls."""
    platform = Platform()
    with pytest.raises(PrivateArtifactError) as caught:
        platform.client(base_url=base_url).private_artifacts.delete(_delete_work())
    _refused(caught)
    assert platform.vault == []


@pytest.mark.parametrize(
    'origin',
    [
        None,
        f'{_ORIGIN}?tenant=other',
        f'{_ORIGIN}#fragment',
        'https://user:pw@vault.example.test',
        f'{_ORIGIN}/base',
        'http://vault.example.test',
        'ftp://vault.example.test',
        'https://',
    ],
)
def test_trusted_origin_refuses_anything_but_a_bare_origin(origin: str | None) -> None:
    """Only a bare HTTPS origin, or plain HTTP on loopback, may carry private bytes."""
    with pytest.raises(PrivateArtifactError):
        trusted_origin(origin)


@pytest.mark.parametrize(
    ('origin', 'expected'),
    [
        (f'{_ORIGIN}/', _ORIGIN),
        ('https://vault.example.test:8443', 'https://vault.example.test:8443'),
        ('http://127.0.0.1:8000', 'http://127.0.0.1:8000'),
        ('http://localhost:8000/', 'http://localhost:8000'),
        ('http://[::1]:8000', 'http://[::1]:8000'),
    ],
)
def test_trusted_origin_accepts_bare_origins(origin: str, expected: str) -> None:
    """A bare origin is returned without its trailing slash."""
    assert trusted_origin(origin) == expected
