"""Ticket-driven private-artifact SDK journeys."""

from __future__ import annotations

import asyncio
import base64
import json
import threading
from http import HTTPStatus
from typing import TYPE_CHECKING, Any, Literal, cast

import httpx
import pytest
from maivn_contracts.artifacts import (
    GRANT_CREDENTIAL_HEADER,
    SENDER_ASSERTION_HEADER,
    SENDER_SIGNATURE_HEADER,
    PrivateWorkIntentStatus,
    VaultActionTicket,
    VaultEffectAccepted,
    VaultGrantCredentialDelivery,
    VaultRetrievalSessionCredentialDelivery,
    VaultSenderAssertion,
    VaultWorkRequest,
    vault_actual_request_digest_from_prehashed,
    vault_assertion_json_bytes,
    vault_client_proof_header_digest,
    vault_grant_header_digest,
    vault_retrieval_session_credential_digest,
    vault_work_request_json_bytes,
)

from maivn import (
    Client,
    PrivateArtifactAuthorizationDeniedError,
    PrivateArtifactEffectFailedError,
    PrivateArtifactError,
    PrivateArtifactGrantExpiredError,
    PrivateArtifactRef,
    PrivateArtifactTimeoutError,
)
from maivn._internal.private_artifacts import (
    _validate_range_response,  # pyright: ignore[reportPrivateUsage] - range transport boundary regression.
    trusted_origin,
)

if TYPE_CHECKING:
    from collections.abc import AsyncGenerator, AsyncIterator, Generator, Mapping
    from pathlib import Path

_VAULT_ORIGIN = 'https://vault.example.test'
_SERVER_INTENT_ID = '00000000-0000-4000-8000-000000000010'
_OPERATION_ID = '00000000-0000-4000-8000-000000000020'
_GRANT = 'Z2dnZ2dnZ2dnZ2dnZ2dnZ2dnZ2dnZ2dnZ2dnZ2dnZ2c'
_SESSION_CREDENTIAL = 'c3Nzc3Nzc3Nzc3Nzc3Nzc3Nzc3Nzc3Nzc3Nzc3Nzc3M'
_SOURCE_GENERATION = 'source_generation_00000000000000000001'
_DELETE_VAULT_CALLS = 4
_MAX_BOUNDED_STREAM_READS = 65
_VAULT_RESPONSE_HEADERS = {
    'cache-control': 'no-store',
    'x-content-type-options': 'nosniff',
}


def _delete_work() -> VaultWorkRequest:
    return VaultWorkRequest.model_validate(
        {
            'schema_version': 'vault-work-v1',
            'intent_id': 'client_intent_000000000000000000000001',
            'idempotency_key': 'idempotency_00000000000000000000001',
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
                    'record_handle': 'source_record_000000000000000000001',
                    'generation_handle': _SOURCE_GENERATION,
                    'role': 'source_artifact',
                },
            ],
            'bounds': {'max_output_bytes': 1_048_576},
            'retention_policy_id': 'retention_standard',
            'deletion_scope': {'mode': 'include_redacted_exports'},
            'expires_at': '2026-08-14T13:00:00Z',
        },
    )


def _retrieve_work(proof_digest: str) -> VaultWorkRequest:
    payload = _delete_work().model_dump(mode='json')
    payload['operation'] = 'retrieve_session'
    payload['deletion_scope'] = None
    payload['client_proof_digest'] = proof_digest
    return VaultWorkRequest.model_validate(payload)


def _private_ref() -> PrivateArtifactRef:
    return PrivateArtifactRef.model_validate(
        {
            'artifact_id': _SOURCE_GENERATION,
            'logical_output_id': 'source_record_000000000000000000001',
            'revision': 2,
            'supersedes_artifact_id': 'source_generation_previous',
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


@pytest.mark.parametrize('access_session', ['root_session', 'followup_session'])
@pytest.mark.parametrize('asynchronous', [False, True])
@pytest.mark.parametrize('part', ['private_original', 'editable_source'])
@pytest.mark.parametrize(
    'mismatch', [None, 'operation', 'generation', 'record', 'session', 'proof', 'part']
)
def test_private_resolver_validates_exact_reference_and_context(  # noqa: C901 - exact authority mismatch matrix.
    mismatch: str | None,
    access_session: str,
    part: Literal['private_original', 'editable_source'],
    *,
    asynchronous: bool,
) -> None:
    """A resolver response for a different generation, session, operation or proof is refused."""
    calls: list[httpx.Request] = []
    client = Client(api_key='sdk-project-key', base_url='https://data.example.test')
    proof = client.private_artifacts.new_retrieval_proof()
    resolved = _retrieve_work(proof.digest).model_dump(mode='json')
    resolved['retrieval_part'] = 'editable_source' if part == 'editable_source' else None
    if mismatch == 'operation':
        resolved = _delete_work().model_dump(mode='json')
    elif mismatch == 'generation':
        resolved['input_handles'][0]['generation_handle'] = 'other_generation'
    elif mismatch == 'record':
        resolved['input_handles'][0]['record_handle'] = 'other_record'
    elif mismatch == 'session':
        resolved['surface']['session_id'] = 'other_session'
    elif mismatch == 'proof':
        resolved['client_proof_digest'] = 'a' * 64
    elif mismatch == 'part':
        resolved['retrieval_part'] = None if part == 'editable_source' else 'editable_source'

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        assert request.url.path == '/v1/private-artifacts/resolve'
        assert json.loads(request.content) == {
            'artifact_ref': _private_ref().model_dump(mode='json'),
            'session_id': access_session,
            'client_proof_digest': proof.digest,
            **({'retrieval_part': part} if part == 'editable_source' else {}),
        }
        return httpx.Response(200, json=resolved)

    client = Client(
        api_key='sdk-project-key',
        base_url='https://data.example.test',
        transport=httpx.MockTransport(handler),
    )

    def exercise() -> VaultWorkRequest:
        if asynchronous:
            return asyncio.run(
                client.private_artifacts.aresolve(
                    _private_ref(),
                    session_id=access_session,
                    proof=proof,
                    retrieval_part=part,
                )
            )
        return client.private_artifacts.resolve(
            _private_ref(),
            session_id=access_session,
            proof=proof,
            retrieval_part=part,
        )

    if mismatch is None:
        assert exercise() == _retrieve_work(proof.digest).model_copy(
            update={
                'retrieval_part': 'editable_source' if part == 'editable_source' else None,
            }
        )
    else:
        with pytest.raises(PrivateArtifactError):
            exercise()
    assert len(calls) == 1


@pytest.mark.parametrize('part', ['private_original', 'editable_source'])
def test_private_resolver_uses_retained_origin_when_reference_omits_session(
    part: Literal['private_original', 'editable_source'],
) -> None:
    """Real Vault refs omit session; the authenticated resolver supplies original authority."""
    ref = _private_ref().model_copy(
        update={'producer': _private_ref().producer.model_copy(update={'session_id': None})}
    )

    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        assert body['session_id'] == 'followup_session'
        assert body['artifact_ref'] == ref.model_dump(mode='json')
        work = _retrieve_work(body['client_proof_digest']).model_copy(
            update={'retrieval_part': 'editable_source' if part == 'editable_source' else None}
        )
        return httpx.Response(200, json=work.model_dump(mode='json'))

    client = Client(
        api_key='sdk-project-key',
        base_url='https://data.example.test',
        transport=httpx.MockTransport(handler),
    )
    work = client.private_artifacts.resolve(
        ref,
        session_id='followup_session',
        proof=client.private_artifacts.new_retrieval_proof(),
        retrieval_part=part,
    )
    assert work.surface.session_id == 'root_session'


@pytest.mark.parametrize('asynchronous', [False, True])
@pytest.mark.parametrize(
    'conflict',
    [
        'missing_session',
        'extra_proof',
        'work_session',
        'missing_proof',
        'wrong_proof',
    ],
)
def test_private_download_rejects_ambiguous_authority_before_network(
    conflict: str,
    *,
    asynchronous: bool,
) -> None:
    """References cannot borrow a caller proof or redirect existing work to another session."""
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(500)

    client = Client(
        api_key='sdk-project-key',
        base_url='https://data.example.test',
        transport=httpx.MockTransport(handler),
    )
    proof = client.private_artifacts.new_retrieval_proof()
    work = _retrieve_work(proof.digest)
    use_ref = conflict in {'missing_session', 'extra_proof'}
    target = _private_ref() if use_ref else work
    selected_proof = None if conflict in {'missing_session', 'missing_proof'} else proof
    if conflict == 'wrong_proof':
        selected_proof = client.private_artifacts.new_retrieval_proof()
    session = 'root_session' if conflict in {'extra_proof', 'work_session'} else None

    def exercise() -> bytes:
        if asynchronous:
            return asyncio.run(
                client.private_artifacts.adownload(
                    target,
                    proof=selected_proof,
                    session_id=session,
                )
            )
        return client.private_artifacts.download(target, proof=selected_proof, session_id=session)

    with pytest.raises(PrivateArtifactError):
        exercise()
    assert requests == []


@pytest.mark.parametrize('asynchronous', [False, True])
def test_private_save_filesystem_failure_does_not_disclose_the_local_path(
    tmp_path: Path,
    *,
    asynchronous: bool,
) -> None:
    """Local filesystem errors obey the same value-free boundary as Vault errors."""
    client = Client(api_key='sdk-project-key', base_url='https://data.example.test')
    proof = client.private_artifacts.new_retrieval_proof()
    work = _retrieve_work(proof.digest)
    destination = tmp_path / 'private-customer-sentinel' / 'private-report.pdf'

    def exercise() -> Path:
        if asynchronous:
            return asyncio.run(
                client.private_artifacts.adownload_to(work, destination, proof=proof)
            )
        return client.private_artifacts.download_to(work, destination, proof=proof)

    with pytest.raises(PrivateArtifactError) as caught:
        exercise()
    assert 'sentinel' not in str(caught.value)
    assert caught.value.__context__ is None
    assert caught.value.__cause__ is None
    assert list(tmp_path.iterdir()) == []


def _export_work() -> VaultWorkRequest:
    payload = _delete_work().model_dump(mode='json')
    payload['operation'] = 'redacted_export'
    payload['deletion_scope'] = None
    payload['export_filename'] = 'Approved report.pdf'
    payload['expires_at'] = '2099-08-14T13:00:00Z'
    return VaultWorkRequest.model_validate(payload)


def _b64(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).rstrip(b'=').decode('ascii')


def _ticket(  # noqa: PLR0913 - every exact signed-call input remains explicit.
    *,
    method: str,
    path: str,
    body: bytes = b'',
    public_headers: Mapping[str, str] | None = None,
    client_header_digests: Mapping[str, str] | None = None,
    origin: str = _VAULT_ORIGIN,
    issued_at: str = '2099-08-14T12:00:00Z',
    expires_at: str = '2099-08-14T12:01:00Z',
) -> dict[str, Any]:
    public = dict(public_headers or {})
    digests = cast('dict[Any, Any]', dict(client_header_digests or {}))
    actual = vault_actual_request_digest_from_prehashed(
        method,
        path,
        body,
        public,
        digests,
        expected_client_headers=frozenset(digests),
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
        issued_at=issued_at,
        nonce='nonce_0000000000000000000000000001',
        expires_at=expires_at,
    )
    ticket = VaultActionTicket.model_validate(
        {
            'method': method,
            'path': path,
            'url': f'{origin}{path}',
            'public_headers': public,
            'client_header_digests': digests,
            'sender_assertion_base64url': _b64(vault_assertion_json_bytes(assertion)),
            'sender_signature_base64url': _b64(b's' * 64),
            'body_base64url': None if not body else _b64(body),
            'expires_at': assertion.expires_at,
        },
    )
    return ticket.model_dump(mode='json')


def _committed_delete_status(work: VaultWorkRequest) -> dict[str, Any]:
    return PrivateWorkIntentStatus.model_validate(
        {
            'intent_id': _SERVER_INTENT_ID,
            'operation': 'delete',
            'correlation_id': work.intent_id,
            'idempotency_key': work.idempotency_key,
            'expires_at': work.expires_at,
            'state': 'committed',
            'lifecycle_receipt': {
                'receipt_id': 'deletion_receipt_0000000000000000001',
                'artifact_id': 'source_record_000000000000000000001',
                'operation': 'delete',
                'state': 'deletion_pending_backup_expiry',
                'correlation_id': work.intent_id,
                'occurred_at': '2026-08-14T12:00:30Z',
            },
        },
    ).model_dump(mode='json')


def _committed_export_status(
    work: VaultWorkRequest,
    *,
    filename: str = 'Approved report.pdf',
) -> dict[str, Any]:
    return PrivateWorkIntentStatus.model_validate(
        {
            'intent_id': _SERVER_INTENT_ID,
            'operation': 'redacted_export',
            'correlation_id': work.intent_id,
            'idempotency_key': work.idempotency_key,
            'expires_at': work.expires_at,
            'state': 'committed',
            'redacted_export_receipt': {
                'receipt_id': 'receipt_export_0000000000000000001',
                'ordinary_artifact': {
                    'artifact_id': 'ordinary_artifact_000000000000001',
                    'logical_output_id': 'ordinary_output_0000000000000001',
                    'revision': 1,
                    'kind': 'document',
                    'mime_type': 'application/pdf',
                    'custody': 'ordinary',
                    'state': 'available',
                    'created_at': '2099-08-14T12:00:30Z',
                    'effective_retention': {
                        'policy_snapshot_id': 'policy_snapshot_000000000000000001',
                        'retention_class': 'invocation_artifact_30d',
                        'expires_at': '2099-09-14T12:00:30Z',
                    },
                    'producer': {
                        'producer_class': 'vault',
                        'producer_id': 'verified-redacted-export',
                        'root_invocation_id': 'root_invocation',
                    },
                    'display_filename': filename,
                    'size_bytes': 1024,
                    'sha256': 'c' * 64,
                    'validation_receipt': {
                        'receipt_id': 'validation_receipt_000000000000001',
                        'status': 'validated',
                        'validator_profile': 'document-default',
                        'validator_version': '1.0.0',
                    },
                    'safe_preview': {'kind': 'document', 'page_count': 1},
                    'retrieval_action': {'relation': 'artifact.download_authorization'},
                },
                'correlation_id': work.intent_id,
                'occurred_at': '2099-08-14T12:00:30Z',
            },
        },
    ).model_dump(mode='json')


def test_delete_uses_capability_free_data_tickets_and_direct_vault_calls() -> None:
    """Raw grants go only from the approval body into the exact Vault redemption."""
    work = _delete_work()
    data_calls: list[tuple[str, dict[str, Any], str | None]] = []
    vault_calls: list[httpx.Request] = []

    def data_handler(request: httpx.Request) -> httpx.Response:
        payload = cast('dict[str, Any]', json.loads(request.content))
        data_calls.append((request.url.path, payload, request.headers.get('authorization')))
        assert _GRANT.encode() not in request.content
        if request.url.path == '/v1/private-artifacts/intents/bind-status':
            assert payload == VaultEffectAccepted(
                server_intent_id=_SERVER_INTENT_ID,
                operation_id=_OPERATION_ID,
                operation='delete',
                state='queued',
            ).model_dump(mode='json')
            return httpx.Response(HTTPStatus.OK, json=_committed_delete_status(work))
        assert request.url.path == '/v1/private-artifacts/tickets'
        assert payload['work'] == work.model_dump(mode='json')
        operation = payload['operation']
        if operation == 'admit':
            return httpx.Response(
                HTTPStatus.OK,
                json=_ticket(
                    method='POST',
                    path='/v1/vault/work-intents',
                    body=vault_work_request_json_bytes(work),
                    public_headers={'content-type': 'application/json'},
                ),
            )
        if operation == 'approve':
            assert payload['server_intent_id'] == _SERVER_INTENT_ID
            return httpx.Response(
                HTTPStatus.OK,
                json=_ticket(
                    method='POST',
                    path=f'/v1/vault/work-intents/{_SERVER_INTENT_ID}/approval',
                ),
            )
        if operation == 'redeem':
            assert payload['client_header_digests'] == {
                GRANT_CREDENTIAL_HEADER: vault_grant_header_digest(_GRANT),
            }
            return httpx.Response(
                HTTPStatus.OK,
                json=_ticket(
                    method='POST',
                    path=f'/v1/vault/work-intents/{_SERVER_INTENT_ID}/redemption',
                    client_header_digests={
                        GRANT_CREDENTIAL_HEADER: vault_grant_header_digest(_GRANT),
                    },
                ),
            )
        assert operation == 'delete'
        assert payload['operation_id'] == _OPERATION_ID
        return httpx.Response(
            HTTPStatus.OK,
            json=_ticket(
                method='POST',
                path=f'/v1/vault/artifacts/{_SOURCE_GENERATION}/deletion',
                body=vault_work_request_json_bytes(work),
                public_headers={
                    'content-type': 'application/json',
                    'x-maivn-operation-id': _OPERATION_ID,
                },
            ),
        )

    def vault_handler(request: httpx.Request) -> httpx.Response:
        vault_calls.append(request)
        assert request.url.scheme == 'https'
        assert request.url.host == 'vault.example.test'
        assert 'authorization' not in request.headers
        assert request.headers['accept-encoding'] == 'identity'
        assert SENDER_ASSERTION_HEADER in request.headers
        assert SENDER_SIGNATURE_HEADER in request.headers
        if request.url.path == '/v1/vault/work-intents':
            assert request.content == vault_work_request_json_bytes(work)
            return httpx.Response(
                HTTPStatus.CREATED,
                headers=_VAULT_RESPONSE_HEADERS,
                json={
                    'intent_id': _SERVER_INTENT_ID,
                    'state': 'authorization_pending',
                    'replayed': False,
                },
            )
        if request.url.path.endswith('/approval'):
            delivery = VaultGrantCredentialDelivery(
                intent_id=_SERVER_INTENT_ID,
                state='executing_in_vault',
                grant_credential_digest=vault_grant_header_digest(_GRANT),
                credential_delivery='delivered',
                grant_credential=_GRANT,
                expires_at='2099-08-14T12:05:00Z',
            )
            return httpx.Response(
                HTTPStatus.OK,
                headers=_VAULT_RESPONSE_HEADERS,
                json=delivery.model_dump(mode='json'),
            )
        if request.url.path.endswith('/redemption'):
            assert request.headers[GRANT_CREDENTIAL_HEADER] == _GRANT
            return httpx.Response(
                HTTPStatus.OK,
                headers=_VAULT_RESPONSE_HEADERS,
                json={'operation_id': _OPERATION_ID, 'replayed': False},
            )
        assert request.url.path.endswith('/deletion')
        assert GRANT_CREDENTIAL_HEADER not in request.headers
        return httpx.Response(
            HTTPStatus.ACCEPTED,
            headers=_VAULT_RESPONSE_HEADERS,
            json={
                'server_intent_id': _SERVER_INTENT_ID,
                'operation_id': _OPERATION_ID,
                'operation': 'delete',
                'state': 'queued',
            },
        )

    client = Client(
        api_key='sdk-project-key',
        base_url=_VAULT_ORIGIN,
        transport=httpx.MockTransport(data_handler),
        vault_transport=httpx.MockTransport(vault_handler),
    )

    result = client.private_artifacts.delete(work)

    assert result.state == 'committed'
    assert result.lifecycle_receipt is not None
    assert result.lifecycle_receipt.state == 'deletion_pending_backup_expiry'
    assert [item[0] for item in data_calls] == [
        '/v1/private-artifacts/tickets',
        '/v1/private-artifacts/tickets',
        '/v1/private-artifacts/tickets',
        '/v1/private-artifacts/tickets',
        '/v1/private-artifacts/intents/bind-status',
    ]
    assert all(auth == 'Bearer sdk-project-key' for _path, _payload, auth in data_calls)
    assert len(vault_calls) == _DELETE_VAULT_CALLS


def test_delete_refuses_wrong_operation_before_network() -> None:
    """A convenience method cannot reinterpret the admitted closed operation."""
    calls = 0

    def handler(_request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(HTTPStatus.INTERNAL_SERVER_ERROR)

    work = _delete_work().model_copy(update={'operation': 'retrieve_session'})
    client = Client(
        api_key='sdk-project-key',
        base_url=_VAULT_ORIGIN,
        transport=httpx.MockTransport(handler),
        vault_transport=httpx.MockTransport(handler),
    )

    with pytest.raises(PrivateArtifactError, match='private_artifact_request_refused'):
        client.private_artifacts.delete(work)

    assert calls == 0


# One test covering the full bounded-range retrieval path; the branches are the
# range cases it exists to cover.
@pytest.mark.parametrize(
    'method',
    [
        'aretrieve_stream',
        'download',
        'adownload',
        'download_to',
        'adownload_to',
        'ref_download',
        'ref_adownload',
        'ref_download_to',
        'ref_adownload_to',
    ],
)
@pytest.mark.parametrize('fault', [None, 'oversized', 'interrupted', 'cancelled'])
def test_retrieval_streams_bounded_ranges_with_locally_attached_secrets(  # noqa: C901, PLR0915
    tmp_path: Path,
    method: str,
    fault: str | None,
) -> None:
    """Data sees digests only while each exact Vault range receives both raw secrets."""
    selected_ref = _private_ref().model_copy(
        update={'producer': _private_ref().producer.model_copy(update={'session_id': None})}
    )
    data_bodies: list[bytes] = []
    raw_proof: str | None = None
    client = Client(
        api_key='sdk-project-key',
        base_url=_VAULT_ORIGIN,
    )
    proof = client.private_artifacts.new_retrieval_proof()
    work = _retrieve_work(proof.digest)
    if fault == 'oversized':
        work = VaultWorkRequest.model_validate(
            {
                **work.model_dump(mode='json'),
                'bounds': {'max_output_bytes': 4},
            }
        )

    def data_handler(request: httpx.Request) -> httpx.Response:
        nonlocal work
        data_bodies.append(request.content)
        payload = cast('dict[str, Any]', json.loads(request.content))
        if request.url.path == '/v1/private-artifacts/resolve':
            assert payload['artifact_ref'] == selected_ref.model_dump(mode='json')
            assert payload['session_id'] == 'followup_session'
            assert set(payload) == {'artifact_ref', 'session_id', 'client_proof_digest'}
            work = VaultWorkRequest.model_validate(
                {
                    **work.model_dump(mode='json'),
                    'client_proof_digest': payload['client_proof_digest'],
                }
            )
            return httpx.Response(HTTPStatus.OK, json=work.model_dump(mode='json'))
        assert request.url.path == '/v1/private-artifacts/tickets'
        operation = payload['operation']
        if operation == 'admit':
            return httpx.Response(
                HTTPStatus.OK,
                json=_ticket(
                    method='POST',
                    path='/v1/vault/work-intents',
                    body=vault_work_request_json_bytes(work),
                    public_headers={'content-type': 'application/json'},
                ),
            )
        if operation == 'approve':
            return httpx.Response(
                HTTPStatus.OK,
                json=_ticket(
                    method='POST',
                    path=f'/v1/vault/work-intents/{_SERVER_INTENT_ID}/approval',
                ),
            )
        if operation == 'redeem':
            return httpx.Response(
                HTTPStatus.OK,
                json=_ticket(
                    method='POST',
                    path=f'/v1/vault/work-intents/{_SERVER_INTENT_ID}/redemption',
                    client_header_digests={
                        GRANT_CREDENTIAL_HEADER: vault_grant_header_digest(_GRANT),
                    },
                ),
            )
        if operation == 'retrieve_session':
            assert payload['client_header_digests'] == {
                'x-maivn-client-proof': work.client_proof_digest,
            }
            return httpx.Response(
                HTTPStatus.OK,
                json=_ticket(
                    method='POST',
                    path=f'/v1/vault/artifacts/{_SOURCE_GENERATION}/retrieval-sessions',
                    body=vault_work_request_json_bytes(work),
                    public_headers={
                        'content-type': 'application/json',
                        'x-maivn-operation-id': _OPERATION_ID,
                    },
                    client_header_digests={
                        'x-maivn-client-proof': cast('str', work.client_proof_digest)
                    },
                ),
            )
        assert operation == 'read_bytes'
        first = cast('int', payload['first'])
        last = cast('int', payload['last'])
        assert payload['session_id'] == 'retrieval_session_public_0000000001'
        return httpx.Response(
            HTTPStatus.OK,
            json=_ticket(
                method='GET',
                path=f'/v1/vault/artifacts/{_SOURCE_GENERATION}/bytes',
                public_headers={
                    'range': f'bytes={first}-{last}',
                    'x-maivn-session-id': 'retrieval_session_public_0000000001',
                },
                client_header_digests={
                    'x-maivn-client-proof': cast('str', work.client_proof_digest),
                    'x-maivn-session-credential': vault_retrieval_session_credential_digest(
                        _SESSION_CREDENTIAL,
                    ),
                },
            ),
        )

    def vault_handler(request: httpx.Request) -> httpx.Response:  # noqa: PLR0911
        nonlocal raw_proof
        if request.url.path == '/v1/vault/work-intents':
            return httpx.Response(
                HTTPStatus.CREATED,
                headers=_VAULT_RESPONSE_HEADERS,
                json={
                    'intent_id': _SERVER_INTENT_ID,
                    'state': 'authorization_pending',
                    'replayed': False,
                },
            )
        if request.url.path.endswith('/approval'):
            delivery = VaultGrantCredentialDelivery(
                intent_id=_SERVER_INTENT_ID,
                state='executing_in_vault',
                grant_credential_digest=vault_grant_header_digest(_GRANT),
                credential_delivery='delivered',
                grant_credential=_GRANT,
                expires_at='2099-08-14T12:05:00Z',
            )
            return httpx.Response(
                HTTPStatus.OK,
                headers=_VAULT_RESPONSE_HEADERS,
                json=delivery.model_dump(mode='json'),
            )
        if request.url.path.endswith('/redemption'):
            return httpx.Response(
                HTTPStatus.OK,
                headers=_VAULT_RESPONSE_HEADERS,
                json={'operation_id': _OPERATION_ID, 'replayed': False},
            )
        if request.url.path.endswith('/retrieval-sessions'):
            raw_proof = request.headers['x-maivn-client-proof']
            assert vault_client_proof_header_digest(raw_proof) == work.client_proof_digest
            delivery = VaultRetrievalSessionCredentialDelivery(
                schema_version='vault-retrieval-session-credential-delivery-v1',
                session_id='retrieval_session_public_0000000001',
                session_credential_digest=vault_retrieval_session_credential_digest(
                    _SESSION_CREDENTIAL,
                ),
                credential_delivery='delivered',
                session_credential=_SESSION_CREDENTIAL,
                expires_at='2099-08-14T12:05:00Z',
            )
            return httpx.Response(
                HTTPStatus.CREATED,
                headers=_VAULT_RESPONSE_HEADERS,
                json=delivery.model_dump(mode='json'),
            )
        assert request.url.path.endswith('/bytes')
        assert request.headers['x-maivn-client-proof'] == raw_proof
        assert request.headers['x-maivn-session-credential'] == _SESSION_CREDENTIAL
        assert 'authorization' not in request.headers
        if request.headers['range'] == 'bytes=0-2':
            return httpx.Response(
                HTTPStatus.PARTIAL_CONTENT,
                headers={
                    'accept-ranges': 'bytes',
                    'cache-control': 'no-store',
                    'content-disposition': 'attachment',
                    'content-length': '3',
                    'content-range': 'bytes 0-2/5',
                    'content-type': 'application/pdf',
                    'x-content-type-options': 'nosniff',
                },
                content=b'abc',
            )
        assert request.headers['range'] == 'bytes=3-5'
        if fault == 'cancelled':
            raise asyncio.CancelledError
        if fault == 'interrupted':
            return httpx.Response(HTTPStatus.SERVICE_UNAVAILABLE)
        return httpx.Response(
            HTTPStatus.PARTIAL_CONTENT,
            headers={
                'accept-ranges': 'bytes',
                'cache-control': 'no-store',
                'content-disposition': 'attachment',
                'content-length': '2',
                'content-range': 'bytes 3-4/5',
                'content-type': 'application/pdf',
                'x-content-type-options': 'nosniff',
            },
            content=b'de',
        )

    client = Client(
        api_key='sdk-project-key',
        base_url=_VAULT_ORIGIN,
        transport=httpx.MockTransport(data_handler),
        vault_transport=httpx.MockTransport(vault_handler),
    )

    async def collect() -> list[bytes]:
        return [
            chunk
            async for chunk in client.private_artifacts.aretrieve_stream(
                work,
                proof=proof,
                chunk_bytes=3,
            )
        ]

    destination = tmp_path / 'private-document.pdf'
    if fault is not None:
        destination.write_bytes(b'previous')

    def exercise() -> bytes:
        use_ref = method.startswith('ref_')
        target = selected_ref if use_ref else work
        selected_proof = None if use_ref else proof
        session_id = 'followup_session' if use_ref else None
        operation = method.removeprefix('ref_')
        if operation == 'download':
            return client.private_artifacts.download(
                target,
                proof=selected_proof,
                session_id=session_id,
                chunk_bytes=3,
            )
        if operation == 'adownload':
            return asyncio.run(
                client.private_artifacts.adownload(
                    target,
                    proof=selected_proof,
                    session_id=session_id,
                    chunk_bytes=3,
                )
            )
        if operation == 'download_to':
            result = client.private_artifacts.download_to(
                target,
                destination,
                proof=selected_proof,
                session_id=session_id,
                chunk_bytes=3,
            )
            assert result == destination
            return destination.read_bytes()
        if operation == 'adownload_to':
            result = asyncio.run(
                client.private_artifacts.adownload_to(
                    target,
                    destination,
                    proof=selected_proof,
                    session_id=session_id,
                    chunk_bytes=3,
                )
            )
            assert result == destination
            return destination.read_bytes()
        return b''.join(asyncio.run(collect()))

    if fault is not None:
        error_type = asyncio.CancelledError if fault == 'cancelled' else PrivateArtifactError
        with pytest.raises(error_type):
            exercise()
        assert destination.read_bytes() == b'previous'
    else:
        assert exercise() == b'abcde'

    has_file = method.endswith('_to') or fault is not None
    assert list(tmp_path.iterdir()) == ([destination] if has_file else [])
    assert raw_proof is not None
    assert all(raw_proof.encode() not in body for body in data_bodies)
    assert all(_SESSION_CREDENTIAL.encode() not in body for body in data_bodies)


@pytest.mark.parametrize('asynchronous', [False, True])
def test_private_download_refuses_wrong_operation_without_replacing_file(
    tmp_path: Path,
    *,
    asynchronous: bool,
) -> None:
    """A delete grant cannot be used to download or overwrite a selected local file."""
    destination = tmp_path / 'private.pdf'
    destination.write_bytes(b'previous')
    client = Client(api_key='sdk-project-key', base_url=_VAULT_ORIGIN)
    proof = client.private_artifacts.new_retrieval_proof()
    if asynchronous:
        with pytest.raises(PrivateArtifactError):
            asyncio.run(
                client.private_artifacts.adownload_to(
                    _delete_work(),
                    destination,
                    proof=proof,
                )
            )
    else:
        with pytest.raises(PrivateArtifactError):
            client.private_artifacts.download_to(_delete_work(), destination, proof=proof)
    assert destination.read_bytes() == b'previous'
    assert list(tmp_path.iterdir()) == [destination]


def test_expired_session_delivery_refuses_before_requesting_a_range() -> None:
    """An expired transient session credential cannot authorize a byte-range ticket."""
    client = Client(
        api_key='sdk-project-key',
        base_url=_VAULT_ORIGIN,
    )
    proof = client.private_artifacts.new_retrieval_proof()
    work = _retrieve_work(proof.digest)
    calls: list[str] = []

    async def authorize(_work: VaultWorkRequest) -> str:
        return _OPERATION_ID

    async def ticket_call(*, operation: str, **_kwargs: object) -> httpx.Response:
        calls.append(operation)
        delivery = VaultRetrievalSessionCredentialDelivery(
            schema_version='vault-retrieval-session-credential-delivery-v1',
            session_id='retrieval_session_public_0000000001',
            session_credential_digest=vault_retrieval_session_credential_digest(
                _SESSION_CREDENTIAL,
            ),
            credential_delivery='delivered',
            session_credential=_SESSION_CREDENTIAL,
            expires_at='2000-01-01T00:00:00Z',
        )
        return httpx.Response(
            HTTPStatus.CREATED,
            headers={'cache-control': 'no-store', 'x-content-type-options': 'nosniff'},
            json=delivery.model_dump(mode='json'),
        )

    # Rebinding a bound method is how this suite swaps the authorize call for a double.
    client.private_artifacts._authorize = authorize  # noqa: SLF001  # type: ignore[method-assign]
    # Rebinding a bound method is how this suite swaps the ticket call for a double.
    client.private_artifacts._ticket_call = ticket_call  # noqa: SLF001  # type: ignore[method-assign]

    async def collect() -> list[bytes]:
        return [
            chunk async for chunk in client.private_artifacts.aretrieve_stream(work, proof=proof)
        ]

    with pytest.raises(PrivateArtifactError):
        asyncio.run(collect())

    assert calls == ['retrieve_session']


def test_sensitive_response_refusal_has_no_value_or_exception_chain() -> None:
    """Malformed secret delivery cannot survive in any SDK error projection."""
    sentinel = 'cHJpdmF0ZV9zZW50aW5lbF80Mg'
    client = Client(
        api_key='sdk-project-key',
        base_url=_VAULT_ORIGIN,
    )
    call_count = 0

    async def ticket_call(**_kwargs: object) -> httpx.Response:
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            return httpx.Response(
                HTTPStatus.CREATED,
                content=json.dumps(
                    {
                        'intent_id': _SERVER_INTENT_ID,
                        'state': 'authorization_pending',
                        'replayed': False,
                    },
                ).encode(),
            )
        return httpx.Response(
            HTTPStatus.OK,
            headers={'cache-control': 'no-store', 'x-content-type-options': 'nosniff'},
            content=json.dumps(
                {
                    'intent_id': _SERVER_INTENT_ID,
                    'state': 'executing_in_vault',
                    'grant_credential_digest': '0' * 64,
                    'credential_delivery': 'delivered',
                    'grant_credential': sentinel,
                    'expires_at': '2026-08-14T12:05:00Z',
                },
            ).encode(),
        )

    # Rebinding a bound method is how this suite swaps the ticket call for a double.
    client.private_artifacts._ticket_call = ticket_call  # noqa: SLF001  # type: ignore[method-assign]

    with pytest.raises(PrivateArtifactError) as caught:
        client.private_artifacts.delete(_delete_work())

    projections = (
        str(caught.value),
        repr(caught.value),
        repr(vars(caught.value)),
    )
    assert all(sentinel not in projection for projection in projections)
    assert caught.value.__cause__ is None
    assert caught.value.__context__ is None


def test_approval_delivery_for_another_intent_refuses_before_redemption() -> None:
    """A valid raw grant cannot be moved between distinct admitted intent identities."""
    client = Client(
        api_key='sdk-project-key',
        base_url=_VAULT_ORIGIN,
    )
    calls: list[str] = []

    async def ticket_call(*, operation: str, **_kwargs: object) -> httpx.Response:
        calls.append(operation)
        if operation == 'admit':
            return httpx.Response(
                HTTPStatus.CREATED,
                json={
                    'intent_id': _SERVER_INTENT_ID,
                    'state': 'authorization_pending',
                    'replayed': False,
                },
            )
        if operation == 'approve':
            delivery = VaultGrantCredentialDelivery(
                intent_id='00000000-0000-4000-8000-000000000099',
                state='executing_in_vault',
                grant_credential_digest=vault_grant_header_digest(_GRANT),
                credential_delivery='delivered',
                grant_credential=_GRANT,
                expires_at='2099-08-14T12:05:00Z',
            )
            return httpx.Response(
                HTTPStatus.OK,
                headers={'cache-control': 'no-store', 'x-content-type-options': 'nosniff'},
                json=delivery.model_dump(mode='json'),
            )
        return httpx.Response(
            HTTPStatus.OK,
            json={'operation_id': _OPERATION_ID, 'replayed': False},
        )

    # Rebinding a bound method is how this suite swaps the ticket call for a double.
    client.private_artifacts._ticket_call = ticket_call  # noqa: SLF001  # type: ignore[method-assign]

    with pytest.raises(PrivateArtifactError):
        asyncio.run(
            # The authorize seam is what this test drives.
            client.private_artifacts._authorize(  # noqa: SLF001  # pyright: ignore[reportPrivateUsage]
                _delete_work(),
            ),
        )

    assert calls == ['admit', 'approve']


def test_expired_approval_delivery_refuses_before_redemption() -> None:
    """An expired transient grant is never attached to a new redemption request."""
    client = Client(
        api_key='sdk-project-key',
        base_url=_VAULT_ORIGIN,
    )
    calls: list[str] = []

    async def ticket_call(*, operation: str, **_kwargs: object) -> httpx.Response:
        calls.append(operation)
        if operation == 'admit':
            return httpx.Response(
                HTTPStatus.CREATED,
                json={
                    'intent_id': _SERVER_INTENT_ID,
                    'state': 'authorization_pending',
                    'replayed': False,
                },
            )
        if operation == 'approve':
            delivery = VaultGrantCredentialDelivery(
                intent_id=_SERVER_INTENT_ID,
                state='executing_in_vault',
                grant_credential_digest=vault_grant_header_digest(_GRANT),
                credential_delivery='delivered',
                grant_credential=_GRANT,
                expires_at='2000-01-01T00:00:00Z',
            )
            return httpx.Response(
                HTTPStatus.OK,
                headers={'cache-control': 'no-store', 'x-content-type-options': 'nosniff'},
                json=delivery.model_dump(mode='json'),
            )
        return httpx.Response(
            HTTPStatus.OK,
            json={'operation_id': _OPERATION_ID, 'replayed': False},
        )

    # Rebinding a bound method is how this suite swaps the ticket call for a double.
    client.private_artifacts._ticket_call = ticket_call  # noqa: SLF001  # type: ignore[method-assign]

    with pytest.raises(PrivateArtifactError):
        asyncio.run(
            # The authorize seam is what this test drives.
            client.private_artifacts._authorize(  # noqa: SLF001  # pyright: ignore[reportPrivateUsage]
                _delete_work(),
            ),
        )

    assert calls == ['admit', 'approve']


def test_expired_or_wrong_origin_ticket_refuses_before_vault_network() -> None:
    """A Data response cannot redirect the SDK or revive an expired assertion."""
    work = _delete_work()

    for ticket in (
        _ticket(
            method='POST',
            path='/v1/vault/work-intents',
            body=vault_work_request_json_bytes(work),
            public_headers={'content-type': 'application/json'},
            issued_at='1999-12-31T23:59:00Z',
            expires_at='2000-01-01T00:00:00Z',
        ),
        _ticket(
            method='POST',
            path='/v1/vault/work-intents',
            body=vault_work_request_json_bytes(work),
            public_headers={'content-type': 'application/json'},
            origin='https://foreign-vault.example.test',
        ),
    ):
        vault_calls = 0

        def data_handler(
            _request: httpx.Request,
            ticket_payload: dict[str, Any] = ticket,
        ) -> httpx.Response:
            return httpx.Response(HTTPStatus.OK, json=ticket_payload)

        def vault_handler(_request: httpx.Request) -> httpx.Response:
            nonlocal vault_calls
            vault_calls += 1
            return httpx.Response(HTTPStatus.INTERNAL_SERVER_ERROR)

        client = Client(
            api_key='sdk-project-key',
            base_url=_VAULT_ORIGIN,
            transport=httpx.MockTransport(data_handler),
            vault_transport=httpx.MockTransport(vault_handler),
        )

        with pytest.raises(PrivateArtifactError):
            client.private_artifacts.delete(work)

        assert vault_calls == 0


def test_direct_vault_response_stops_before_buffering_over_the_range_bound() -> None:
    """A malicious Vault response cannot make the SDK buffer an unbounded body."""

    class CountingStream(httpx.AsyncByteStream):
        def __init__(self) -> None:
            self.reads = 0

        async def __aiter__(self) -> AsyncIterator[bytes]:
            for _ in range(100):
                self.reads += 1
                yield b'x' * 1024

    stream = CountingStream()
    ticket = VaultActionTicket.model_validate(
        _ticket(
            method='GET',
            path=f'/v1/vault/artifacts/{_SOURCE_GENERATION}/bytes',
            public_headers={
                'range': 'bytes=0-65535',
                'x-maivn-session-id': 'retrieval_session_public_0000000001',
            },
        ),
    )

    def vault_handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            HTTPStatus.PARTIAL_CONTENT,
            headers={'content-length': '102400'},
            stream=stream,
        )

    client = Client(
        api_key='sdk-project-key',
        base_url=_VAULT_ORIGIN,
        vault_transport=httpx.MockTransport(vault_handler),
    )

    with pytest.raises(PrivateArtifactError):
        asyncio.run(
            # The direct-fetch seam is what this test drives.
            client.private_artifacts._direct(  # noqa: SLF001  # pyright: ignore[reportPrivateUsage]
                ticket,
                capabilities={},
            ),
        )

    assert stream.reads <= _MAX_BOUNDED_STREAM_READS


def test_direct_vault_response_refuses_raw_capability_headers() -> None:
    """A Vault response cannot reflect a transient capability into public transport state."""
    sentinel = 'cHJpdmF0ZV9yZXNwb25zZV9jcmVkZW50aWFs'
    ticket = VaultActionTicket.model_validate(
        _ticket(method='POST', path='/v1/vault/work-intents/example/approval'),
    )

    def vault_handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            HTTPStatus.OK,
            headers={GRANT_CREDENTIAL_HEADER: sentinel},
            json={'safe': True},
        )

    client = Client(
        api_key='sdk-project-key',
        base_url=_VAULT_ORIGIN,
        vault_transport=httpx.MockTransport(vault_handler),
    )

    with pytest.raises(PrivateArtifactError) as caught:
        asyncio.run(
            # The direct-fetch seam is what this test drives.
            client.private_artifacts._direct(  # noqa: SLF001  # pyright: ignore[reportPrivateUsage]
                ticket,
                capabilities={},
            ),
        )

    assert sentinel not in repr(caught.value)
    assert caught.value.__cause__ is None
    assert caught.value.__context__ is None


@pytest.mark.parametrize(
    'headers',
    [
        {},
        {'cache-control': 'no-store'},
        {'x-content-type-options': 'nosniff'},
        {'cache-control': 'public', 'x-content-type-options': 'nosniff'},
    ],
)
def test_direct_vault_response_requires_private_no_store_headers(
    headers: dict[str, str],
) -> None:
    """Every direct Vault response must prevent storage and MIME sniffing."""
    ticket = VaultActionTicket.model_validate(
        _ticket(method='POST', path='/v1/vault/work-intents/example/redemption'),
    )

    def vault_handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(HTTPStatus.OK, headers=headers, json={'safe': True})

    client = Client(
        api_key='sdk-project-key',
        base_url=_VAULT_ORIGIN,
        vault_transport=httpx.MockTransport(vault_handler),
    )

    with pytest.raises(PrivateArtifactError):
        asyncio.run(
            # The direct-fetch seam is what this test drives.
            client.private_artifacts._direct(  # noqa: SLF001  # pyright: ignore[reportPrivateUsage]
                ticket,
                capabilities={},
            ),
        )


@pytest.mark.parametrize(
    ('state', 'error_type'),
    [
        ('authorization_denied', PrivateArtifactAuthorizationDeniedError),
        ('grant_expired', PrivateArtifactGrantExpiredError),
        ('failed', PrivateArtifactEffectFailedError),
    ],
)
def test_effect_status_maps_terminal_states_to_typed_value_free_errors(
    state: str,
    error_type: type[PrivateArtifactError],
) -> None:
    """Terminal lifecycle states remain distinguishable without exposing details."""
    work = _delete_work()
    accepted = VaultEffectAccepted(
        server_intent_id=_SERVER_INTENT_ID,
        operation_id=_OPERATION_ID,
        operation='delete',
        state='queued',
    )
    status = PrivateWorkIntentStatus(
        intent_id=_SERVER_INTENT_ID,
        operation='delete',
        correlation_id=work.intent_id,
        idempotency_key=work.idempotency_key,
        expires_at=work.expires_at,
        state=cast('Any', state),
    )
    client = Client(
        api_key='sdk-project-key',
        base_url=_VAULT_ORIGIN,
    )

    async def data_post(_path: str, _payload: dict[str, Any]) -> dict[str, Any]:
        return status.model_dump(mode='json')

    # Rebinding a bound method is how this suite swaps the data-plane POST for a double.
    client.private_artifacts._data_post = data_post  # noqa: SLF001  # type: ignore[method-assign]

    with pytest.raises(error_type) as caught:
        asyncio.run(
            # The bind-and-poll seam is what this test drives.
            client.private_artifacts._bind_and_poll(  # noqa: SLF001  # pyright: ignore[reportPrivateUsage]
                work,
                accepted,
            ),
        )

    assert caught.value.__cause__ is None
    assert caught.value.__context__ is None


def test_effect_status_timeout_is_typed_and_value_free() -> None:
    """A bounded poll timeout is distinguishable from transport refusal."""
    work = _delete_work()
    accepted = VaultEffectAccepted(
        server_intent_id=_SERVER_INTENT_ID,
        operation_id=_OPERATION_ID,
        operation='delete',
        state='queued',
    )
    status = PrivateWorkIntentStatus(
        intent_id=_SERVER_INTENT_ID,
        operation='delete',
        correlation_id=work.intent_id,
        idempotency_key=work.idempotency_key,
        expires_at=work.expires_at,
        state='executing_in_vault',
    )
    client = Client(
        api_key='sdk-project-key',
        base_url=_VAULT_ORIGIN,
        timeout_seconds=0.001,
    )

    async def data_post(_path: str, _payload: dict[str, Any]) -> dict[str, Any]:
        return status.model_dump(mode='json')

    # Rebinding a bound method is how this suite swaps the data-plane POST for a double.
    client.private_artifacts._data_post = data_post  # noqa: SLF001  # type: ignore[method-assign]

    with pytest.raises(PrivateArtifactTimeoutError) as caught:
        asyncio.run(
            # The bind-and-poll seam is what this test drives.
            client.private_artifacts._bind_and_poll(  # noqa: SLF001  # pyright: ignore[reportPrivateUsage]
                work,
                accepted,
            ),
        )

    assert caught.value.__cause__ is None
    assert caught.value.__context__ is None


def test_status_refuses_a_cross_paired_server_intent_without_calling_vault() -> None:
    """The authenticated status result must repeat the exact requested server handle."""
    requested = _SERVER_INTENT_ID
    returned = '00000000-0000-4000-8000-000000000099'
    vault_calls = 0

    def data_handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == '/v1/private-artifacts/intents/status'
        assert json.loads(request.content) == {
            'schema_version': 'vault-intent-status-request-v1',
            'server_intent_id': requested,
        }
        return httpx.Response(
            HTTPStatus.OK,
            json={
                'intent_id': returned,
                'operation': 'delete',
                'correlation_id': 'client_intent_000000000000000000000001',
                'idempotency_key': 'idempotency_00000000000000000000001',
                'expires_at': '2099-08-14T13:00:00Z',
                'state': 'executing_in_vault',
            },
        )

    def vault_handler(_request: httpx.Request) -> httpx.Response:
        nonlocal vault_calls
        vault_calls += 1
        return httpx.Response(HTTPStatus.INTERNAL_SERVER_ERROR)

    client = Client(
        api_key='sdk-project-key',
        base_url=_VAULT_ORIGIN,
        transport=httpx.MockTransport(data_handler),
        vault_transport=httpx.MockTransport(vault_handler),
    )

    with pytest.raises(PrivateArtifactError):
        client.private_artifacts.status(requested)

    assert vault_calls == 0


def test_status_reads_deleted_manifest_progress_only_from_authenticated_data() -> None:
    """Delete callers can observe the same manifest reaching deleted through Data Plane."""
    work = _delete_work()
    deleted = _committed_delete_status(work)
    lifecycle_receipt = cast('dict[str, Any]', deleted['lifecycle_receipt'])
    lifecycle_receipt['state'] = 'deleted'
    data_calls = 0
    vault_calls = 0

    def data_handler(request: httpx.Request) -> httpx.Response:
        nonlocal data_calls
        data_calls += 1
        assert request.url.path == '/v1/private-artifacts/intents/status'
        assert request.headers['authorization'] == 'Bearer sdk-project-key'
        assert json.loads(request.content) == {
            'schema_version': 'vault-intent-status-request-v1',
            'server_intent_id': _SERVER_INTENT_ID,
        }
        return httpx.Response(HTTPStatus.OK, json=deleted)

    def vault_handler(_request: httpx.Request) -> httpx.Response:
        nonlocal vault_calls
        vault_calls += 1
        return httpx.Response(HTTPStatus.INTERNAL_SERVER_ERROR)

    client = Client(
        api_key='sdk-project-key',
        base_url=_VAULT_ORIGIN,
        transport=httpx.MockTransport(data_handler),
        vault_transport=httpx.MockTransport(vault_handler),
    )

    status = client.private_artifacts.status(_SERVER_INTENT_ID)

    assert status.lifecycle_receipt is not None
    assert status.lifecycle_receipt.state == 'deleted'
    assert data_calls == 1
    assert vault_calls == 0


def test_delete_refuses_a_committed_receipt_for_another_artifact() -> None:
    """A delete outcome must identify the exact admitted source record."""
    work = _delete_work()
    wrong_status = PrivateWorkIntentStatus.model_validate(
        {
            'intent_id': _SERVER_INTENT_ID,
            'operation': 'delete',
            'correlation_id': work.intent_id,
            'idempotency_key': work.idempotency_key,
            'expires_at': work.expires_at,
            'state': 'committed',
            'lifecycle_receipt': {
                'receipt_id': 'deletion_receipt_wrong_artifact_00001',
                'artifact_id': 'different_artifact_record_0000000001',
                'operation': 'delete',
                'state': 'deletion_pending_backup_expiry',
                'correlation_id': work.intent_id,
                'occurred_at': '2099-08-14T12:00:30Z',
            },
        },
    )
    client = Client(
        api_key='sdk-project-key',
        base_url=_VAULT_ORIGIN,
    )

    async def effect_status(
        _work: VaultWorkRequest,
        *,
        expected_operation: str,
    ) -> PrivateWorkIntentStatus:
        assert expected_operation == 'delete'
        return wrong_status

    # Rebinding a bound method is how this suite swaps the effect-status poll for a double.
    client.private_artifacts._effect_status = effect_status  # noqa: SLF001  # type: ignore[method-assign]

    with pytest.raises(PrivateArtifactError):
        asyncio.run(client.private_artifacts.adelete(work))


def test_export_refuses_a_committed_receipt_for_another_correlation() -> None:
    """An ordinary export receipt cannot be moved between client work intents."""
    work = _export_work()
    wrong_status = PrivateWorkIntentStatus.model_validate(
        {
            **_committed_export_status(work),
            'redacted_export_receipt': {
                **cast(
                    'dict[str, Any]',
                    _committed_export_status(work)['redacted_export_receipt'],
                ),
                'correlation_id': 'another_client_intent_00000000000001',
            },
        },
    )
    client = Client(
        api_key='sdk-project-key',
        base_url=_VAULT_ORIGIN,
    )

    async def effect_status(
        _work: VaultWorkRequest,
        *,
        expected_operation: str,
    ) -> PrivateWorkIntentStatus:
        assert expected_operation == 'redacted_export'
        return wrong_status

    # Rebinding a bound method is how this suite swaps the effect-status poll for a double.
    client.private_artifacts._effect_status = effect_status  # noqa: SLF001  # type: ignore[method-assign]

    with pytest.raises(PrivateArtifactError):
        asyncio.run(client.private_artifacts.aexport_redacted(work))


def test_export_refuses_a_committed_receipt_with_a_substituted_filename() -> None:
    """The ordinary filename must equal the exact export filename admitted in the work."""
    work = _export_work()
    wrong_status = PrivateWorkIntentStatus.model_validate(
        _committed_export_status(work, filename='Substituted report.pdf'),
    )
    client = Client(
        api_key='sdk-project-key',
        base_url=_VAULT_ORIGIN,
    )

    async def effect_status(
        _work: VaultWorkRequest,
        *,
        expected_operation: str,
    ) -> PrivateWorkIntentStatus:
        assert expected_operation == 'redacted_export'
        return wrong_status

    # Rebinding a bound method is how this suite swaps the effect-status poll for a double.
    client.private_artifacts._effect_status = effect_status  # noqa: SLF001  # type: ignore[method-assign]

    with pytest.raises(PrivateArtifactError):
        asyncio.run(client.private_artifacts.aexport_redacted(work))


def test_export_returns_the_exact_committed_ordinary_receipt() -> None:
    """A correctly bound export returns its ordinary reference without private authority."""
    work = _export_work()
    status = PrivateWorkIntentStatus.model_validate(_committed_export_status(work))
    client = Client(
        api_key='sdk-project-key',
        base_url=_VAULT_ORIGIN,
    )

    async def effect_status(
        _work: VaultWorkRequest,
        *,
        expected_operation: str,
    ) -> PrivateWorkIntentStatus:
        assert expected_operation == 'redacted_export'
        return status

    # Rebinding a bound method is how this suite swaps the effect-status poll for a double.
    client.private_artifacts._effect_status = effect_status  # noqa: SLF001  # type: ignore[method-assign]

    receipt = asyncio.run(client.private_artifacts.aexport_redacted(work))

    assert receipt.correlation_id == work.intent_id
    assert receipt.ordinary_artifact.display_filename == work.export_filename


@pytest.mark.parametrize('operation', ['delete', 'redacted_export'])
@pytest.mark.parametrize('substitute', [False, True])
def test_message_reference_actions_resolve_exact_scope_before_any_effect(
    operation: str,
    *,
    substitute: bool,
) -> None:
    """SDK-only callers can act on a message ref without constructing server work."""
    work = _delete_work() if operation == 'delete' else _export_work()
    payload = work.model_dump(mode='json')
    ref = _private_ref()
    payload['input_handles'][0]['record_handle'] = ref.logical_output_id
    payload['input_handles'][0]['generation_handle'] = ref.artifact_id
    work = VaultWorkRequest.model_validate(payload)
    status = PrivateWorkIntentStatus.model_validate(
        _committed_delete_status(work) if operation == 'delete' else _committed_export_status(work)
    )
    effects: list[VaultWorkRequest] = []

    def resolve(request: httpx.Request) -> httpx.Response:
        assert request.url.path == '/v1/private-artifacts/resolve'
        selection = json.loads(request.content)
        assert selection['artifact_ref'] == ref.model_dump(mode='json')
        assert selection['operation'] == operation
        if substitute:
            payload['input_handles'][0]['generation_handle'] = 'foreign'
        return httpx.Response(200, json=payload)

    client = Client(
        api_key='sdk-project-key',
        base_url='https://data.example.test',
        transport=httpx.MockTransport(resolve),
    )

    async def effect(
        selected: VaultWorkRequest, *, expected_operation: str
    ) -> PrivateWorkIntentStatus:
        assert expected_operation == operation
        effects.append(selected)
        return status

    client.private_artifacts._effect_status = effect  # noqa: SLF001 # type: ignore[method-assign]

    def execute() -> object:
        if operation == 'delete':
            return client.private_artifacts.delete(
                ref,
                session_id=work.surface.session_id,
                include_redacted_exports=True,
            )
        return client.private_artifacts.export_redacted(
            ref,
            session_id=work.surface.session_id,
            filename=work.export_filename,
        )

    if substitute:
        with pytest.raises(PrivateArtifactError):
            execute()
        assert effects == []
    else:
        assert execute() is not None
        assert effects == [work]


@pytest.mark.parametrize('override', ['filename', 'session_id', 'include_redacted_exports'])
def test_work_actions_refuse_convenience_overrides_before_transport(override: str) -> None:
    """An already canonical work request cannot acquire new filename/session/delete scope."""
    work = _delete_work() if override == 'include_redacted_exports' else _export_work()
    original = work.model_dump_json()
    requests: list[httpx.Request] = []

    def transport(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(500)

    client = Client(
        api_key='sdk-project-key',
        base_url='https://data.example.test',
        transport=httpx.MockTransport(transport),
    )

    def execute() -> object:
        if override == 'include_redacted_exports':
            return client.private_artifacts.delete(work, include_redacted_exports=True)
        if override == 'session_id':
            return client.private_artifacts.export_redacted(work, session_id='changed-session')
        return client.private_artifacts.export_redacted(work, filename='changed.pdf')

    with pytest.raises(PrivateArtifactError):
        execute()
    assert requests == []
    assert work.model_dump_json() == original


def test_delete_refuses_a_committed_receipt_for_another_correlation() -> None:
    """A deletion receipt cannot be moved between client work intents."""
    work = _delete_work()
    wrong_status = PrivateWorkIntentStatus.model_validate(
        {
            **_committed_delete_status(work),
            'lifecycle_receipt': {
                **cast('dict[str, Any]', _committed_delete_status(work)['lifecycle_receipt']),
                'correlation_id': 'another_client_intent_00000000000001',
            },
        },
    )
    client = Client(
        api_key='sdk-project-key',
        base_url=_VAULT_ORIGIN,
    )

    async def effect_status(
        _work: VaultWorkRequest,
        *,
        expected_operation: str,
    ) -> PrivateWorkIntentStatus:
        assert expected_operation == 'delete'
        return wrong_status

    # Rebinding a bound method is how this suite swaps the effect-status poll for a double.
    client.private_artifacts._effect_status = effect_status  # noqa: SLF001  # type: ignore[method-assign]

    with pytest.raises(PrivateArtifactError):
        asyncio.run(client.private_artifacts.adelete(work))


def test_sync_retrieval_close_releases_the_async_source_without_a_worker_leak() -> None:
    """A sync caller stopping early cannot strand the bounded bridge producer."""
    client = Client(
        api_key='sdk-project-key',
        base_url=_VAULT_ORIGIN,
    )
    proof = client.private_artifacts.new_retrieval_proof()
    closed = threading.Event()
    existing_workers = {
        worker.ident
        for worker in threading.enumerate()
        if worker.name == 'maivn-private-artifact-stream'
    }

    async def source(
        _work: VaultWorkRequest,
        *,
        proof: object,
        chunk_bytes: int,
    ) -> AsyncGenerator[bytes, None]:
        del proof, chunk_bytes
        try:
            for _ in range(1_000):
                yield b'x'
        finally:
            closed.set()

    # Rebinding a bound method is how this suite swaps the retrieval stream for a double.
    client.private_artifacts.aretrieve_stream = source  # type: ignore[method-assign]
    iterator = cast(
        'Generator[bytes, None, None]',
        client.private_artifacts.retrieve_stream(_retrieve_work(proof.digest), proof=proof),
    )

    assert next(iterator) == b'x'
    iterator.close()

    assert closed.wait(timeout=1)
    assert (
        not {
            worker.ident
            for worker in threading.enumerate()
            if worker.name == 'maivn-private-artifact-stream'
        }
        - existing_workers
    )


@pytest.mark.parametrize(
    ('part', 'mime', 'accepted'),
    [
        ('editable_source', 'application/octet-stream', True),
        ('editable_source', 'application/pdf', False),
        ('private_original', 'application/octet-stream', True),
        ('private_original', 'application/pdf', True),
    ],
)
def test_range_mime_is_bound_to_exact_retrieval_part(
    part: str, mime: str, *, accepted: bool
) -> None:
    """Opaque source bytes are accepted only for source-bound authorized work."""
    response = httpx.Response(
        206,
        content=b'abc',
        headers={
            'content-range': 'bytes 0-2/3',
            'content-length': '3',
            'accept-ranges': 'bytes',
            'cache-control': 'no-store',
            'content-disposition': 'attachment',
            'x-content-type-options': 'nosniff',
            'content-type': mime,
        },
    )
    if accepted:
        assert _validate_range_response(
            response, expected_first=0, requested_last=2, trusted_total=None, retrieval_part=part
        ) == (2, 3)
    else:
        with pytest.raises(PrivateArtifactError):
            _validate_range_response(
                response,
                expected_first=0,
                requested_last=2,
                trusted_total=None,
                retrieval_part=part,
            )


@pytest.mark.parametrize(
    ('origin', 'trusted'),
    [
        ('https://api.example.test', 'https://api.example.test'),
        ('https://api.example.test/', 'https://api.example.test'),
        ('http://127.0.0.1:8000', 'http://127.0.0.1:8000'),
        ('http://localhost:8000', 'http://localhost:8000'),
        ('http://[::1]:8000', 'http://[::1]:8000'),
        ('http://api.example.test', None),
        ('http://10.0.0.5:8000', None),
        ('ftp://127.0.0.1', None),
        ('https://api.example.test/prefix', None),
        (None, None),
    ],
)
def test_private_bytes_use_tls_except_on_loopback(origin: str | None, trusted: str | None) -> None:
    """The one API origin carries private bytes; plain HTTP only on this machine."""
    if trusted is None:
        with pytest.raises(PrivateArtifactError):
            trusted_origin(origin)
    else:
        assert trusted_origin(origin) == trusted
