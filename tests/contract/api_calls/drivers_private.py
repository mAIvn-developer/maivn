"""Drive the private-artifact facade: Data Plane tickets and the direct Vault calls they sign.

Every Data Plane answer is a ticket, a work request or a status; every ticket names a
direct Vault request that reaches the same fake API. The SDK refuses plain HTTP to a
non-loopback Vault, so these drivers configure an HTTPS base URL on the same transport.
The SDK parses every direct Vault reply with `model_validate_json`, so the record shows
those calls but none of their response reads.
"""

from __future__ import annotations

import base64
import hashlib
import tempfile
from http import HTTPStatus
from pathlib import Path
from typing import TYPE_CHECKING, Any

from maivn_contracts.artifacts import (
    CLIENT_PROOF_HEADER,
    GRANT_CREDENTIAL_HEADER,
    RETRIEVAL_SESSION_CREDENTIAL_HEADER,
    GeneratedFile,
    PrivateArtifactRef,
    VaultActionTicket,
    VaultSenderAssertion,
    VaultWorkRequest,
    vault_actual_request_digest_from_prehashed,
    vault_assertion_json_bytes,
    vault_file_intake_commitment,
    vault_grant_header_digest,
    vault_retrieval_session_credential_digest,
    vault_work_request_json_bytes,
)

from maivn._internal.private_artifacts import (
    TransientVaultClientProof,
    _proof_raw,  # pyright: ignore[reportPrivateUsage] - commitments bind the raw proof.
)
from tests.contract.api_calls.recording import BASE_URL, FakeApi, Reply

if TYPE_CHECKING:
    from maivn import Client

ORIGIN = BASE_URL
SERVER_INTENT_ID = '00000000-0000-4000-8000-000000000010'
OPERATION_ID = '00000000-0000-4000-8000-000000000020'
UPLOAD_ID = '00000000-0000-4000-8000-000000000030'
GENERATION = 'gen_1'
RECORD = 'rec_1'
GRANT = 'Z2dnZ2dnZ2dnZ2dnZ2dnZ2dnZ2dnZ2dnZ2dnZ2dnZ2c'
SESSION_CREDENTIAL = 'c3Nzc3Nzc3Nzc3Nzc3Nzc3Nzc3Nzc3Nzc3Nzc3Nzc3M'
RETRIEVAL_SESSION = 'rses_1'
EXPIRES = '2099-10-09T13:00:00Z'
EXPORT_FILENAME = 'Approved report.pdf'
MAX_SOURCE_BYTES = 52_428_800
NO_STORE = {'cache-control': 'no-store', 'x-content-type-options': 'nosniff'}
PRIVATE_BYTES = b'%PDF-'
CHUNK_BYTES = 4096

INTENTS = '/v1/vault/work-intents'
APPROVAL = f'{INTENTS}/{SERVER_INTENT_ID}/approval'
REDEMPTION = f'{INTENTS}/{SERVER_INTENT_ID}/redemption'
ARTIFACT = f'/v1/vault/artifacts/{GENERATION}'
INGESTIONS = '/v1/vault/artifacts/ingestions'
TICKETS = '/v1/private-artifacts/tickets'
RESOLVE = '/v1/private-artifacts/resolve'
BIND_STATUS = '/v1/private-artifacts/intents/bind-status'


def _pin_proof(client: Client) -> TransientVaultClientProof:
    """Make every internally generated proof the one the scripted replies are bound to."""
    proof = client.private_artifacts.new_retrieval_proof()
    client.private_artifacts.new_retrieval_proof = lambda: proof
    return proof


def _b64(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).rstrip(b'=').decode('ascii')


def _ticket(
    method: str,
    path: str,
    *,
    body: bytes = b'',
    public_headers: dict[str, str] | None = None,
    digests: dict[str, str] | None = None,
) -> Reply:
    """Answer a ticket request with a signed-shape ticket for one direct Vault call."""
    public = dict(public_headers or {})
    header_digests: dict[Any, Any] = dict(digests or {})
    assertion = VaultSenderAssertion(
        issuer='data_plane',
        key_id='data_key_1',
        audience='maivn-vault-plane',
        actor='00000000-0000-4000-8000-000000000001',
        tenant_id='tenant_1',
        surface='data-plane-sdk',
        surface_digest='a' * 64,
        actual_request_digest=vault_actual_request_digest_from_prehashed(
            method,
            path,
            body,
            public,
            header_digests,
            expected_client_headers=frozenset(header_digests),
        ),
        issued_at='2099-10-09T12:00:00Z',
        nonce='nonce_0000000000000000000000000001',
        expires_at='2099-10-09T12:01:00Z',
    )
    ticket = VaultActionTicket.model_validate(
        {
            'method': method,
            'path': path,
            'url': f'{ORIGIN}{path}',
            'public_headers': public,
            'client_header_digests': header_digests,
            'sender_assertion_base64url': _b64(vault_assertion_json_bytes(assertion)),
            'sender_signature_base64url': _b64(b's' * 64),
            'body_base64url': _b64(body) if body else None,
            'expires_at': assertion.expires_at,
        }
    )
    return Reply(json=ticket.model_dump(mode='json'))


def _work(operation: str, **fields: Any) -> VaultWorkRequest:
    payload: dict[str, Any] = {
        'schema_version': 'vault-work-v1',
        'intent_id': 'cint_1',
        'idempotency_key': 'idem_1',
        'operation': operation,
        'scope': {
            'organization_id': '00000000-0000-4000-8000-000000000002',
            'project_id': '00000000-0000-4000-8000-000000000003',
        },
        'surface': {
            'root_invocation_id': 'inv_1',
            'session_id': 'ses_1',
            'checkpoint_id': 'chk_1',
            'caller_id': '00000000-0000-4000-8000-000000000001',
        },
        'input_handles': [
            {
                'local_id': 'source_artifact',
                'record_handle': RECORD,
                'generation_handle': GENERATION,
                'role': 'source_artifact',
            }
        ],
        'bounds': {'max_output_bytes': 1_048_576},
        'retention_policy_id': 'ret_1',
        'expires_at': EXPIRES,
        **fields,
    }
    return VaultWorkRequest.model_validate(payload)


def _ref(**fields: Any) -> PrivateArtifactRef:
    payload: dict[str, Any] = {
        'artifact_id': GENERATION,
        'logical_output_id': RECORD,
        'revision': 1,
        'kind': 'document',
        'mime_type': 'application/pdf',
        'created_at': '2026-10-09T00:00:00Z',
        'effective_retention': {
            'policy_snapshot_id': 'pol_1',
            'retention_class': 'standard',
            'expires_at': EXPIRES,
        },
        'producer': {
            'producer_class': 'vault',
            'producer_id': 'vault_renderer',
            'root_invocation_id': 'inv_1',
            'session_id': 'ses_1',
        },
        'custody': 'vault_private',
        'state': 'available_in_vault',
        'display_label': 'Private document',
        'creation_receipt_id': 'rcpt_1',
        'retrieval_action': {'relation': 'artifact.vault_download_authorization'},
        **fields,
    }
    return PrivateArtifactRef.model_validate(payload)


def _status(work: VaultWorkRequest, **outcome: Any) -> dict[str, Any]:
    return {
        'intent_id': SERVER_INTENT_ID,
        'operation': work.operation,
        'correlation_id': work.intent_id,
        'idempotency_key': work.idempotency_key,
        'expires_at': work.expires_at,
        'state': 'committed',
        **outcome,
    }


def _accepted(operation: str) -> dict[str, Any]:
    return {
        'server_intent_id': SERVER_INTENT_ID,
        'operation_id': OPERATION_ID,
        'operation': operation,
        'state': 'queued',
    }


def _authorize_tickets(work: VaultWorkRequest) -> list[Reply]:
    """Tickets for the admit, approve and redeem calls every effect starts with."""
    return [
        _ticket(
            'POST',
            INTENTS,
            body=vault_work_request_json_bytes(work),
            public_headers={'content-type': 'application/json'},
        ),
        _ticket('POST', APPROVAL),
        _ticket('POST', REDEMPTION, digests={GRANT_CREDENTIAL_HEADER: _grant_digest()}),
    ]


def _effect_ticket(work: VaultWorkRequest, path: str, **headers: str) -> Reply:
    return _ticket(
        'POST',
        path,
        body=vault_work_request_json_bytes(work),
        public_headers={
            'content-type': 'application/json',
            'x-maivn-operation-id': OPERATION_ID,
            **headers,
        },
    )


def _grant_digest() -> str:
    return vault_grant_header_digest(GRANT)


def _approval(*, delivered: bool = True) -> Reply:
    return Reply(
        json={
            'intent_id': SERVER_INTENT_ID,
            'state': 'executing_in_vault',
            'grant_credential_digest': _grant_digest(),
            'credential_delivery': 'delivered' if delivered else 'not_replayable',
            **({'grant_credential': GRANT} if delivered else {}),
            'expires_at': EXPIRES,
        },
        headers=NO_STORE,
    )


def _answer_authorization(api: FakeApi, *, delivered: bool = True) -> None:
    """Answer the direct Vault admit, approval and redemption calls."""
    admitted = {'intent_id': SERVER_INTENT_ID, 'state': 'authorization_pending', 'replayed': False}
    redeemed = {'operation_id': OPERATION_ID, 'replayed': False}
    api.answer('POST', INTENTS, Reply(status=HTTPStatus.CREATED, json=admitted, headers=NO_STORE))
    api.answer('POST', APPROVAL, _approval(delivered=delivered))
    api.answer('POST', REDEMPTION, Reply(json=redeemed, headers=NO_STORE))


def _retrieval_tickets(work: VaultWorkRequest, proof: TransientVaultClientProof) -> list[Reply]:
    """Tickets for one authorized retrieval: authorize, open a session, read one range."""
    return [
        *_authorize_tickets(work),
        _ticket(
            'POST',
            f'{ARTIFACT}/retrieval-sessions',
            body=vault_work_request_json_bytes(work),
            public_headers={
                'content-type': 'application/json',
                'x-maivn-operation-id': OPERATION_ID,
            },
            digests={CLIENT_PROOF_HEADER: proof.digest},
        ),
        _ticket(
            'GET',
            f'{ARTIFACT}/bytes',
            public_headers={
                'range': f'bytes=0-{CHUNK_BYTES - 1}',
                'x-maivn-session-id': RETRIEVAL_SESSION,
            },
            digests={
                CLIENT_PROOF_HEADER: proof.digest,
                RETRIEVAL_SESSION_CREDENTIAL_HEADER: vault_retrieval_session_credential_digest(
                    SESSION_CREDENTIAL
                ),
            },
        ),
    ]


def _answer_retrieval(api: FakeApi) -> None:
    """Answer the direct Vault authorization, session and range calls of a retrieval."""
    _answer_authorization(api)
    delivery = {
        'schema_version': 'vault-retrieval-session-credential-delivery-v1',
        'session_id': RETRIEVAL_SESSION,
        'session_credential_digest': vault_retrieval_session_credential_digest(SESSION_CREDENTIAL),
        'credential_delivery': 'delivered',
        'session_credential': SESSION_CREDENTIAL,
        'expires_at': EXPIRES,
    }
    api.answer(
        'POST',
        f'{ARTIFACT}/retrieval-sessions',
        Reply(status=HTTPStatus.CREATED, json=delivery, headers=NO_STORE),
    )
    size = len(PRIVATE_BYTES)
    api.answer(
        'GET',
        f'{ARTIFACT}/bytes',
        Reply(
            status=HTTPStatus.PARTIAL_CONTENT,
            content=PRIVATE_BYTES,
            headers={
                **NO_STORE,
                'accept-ranges': 'bytes',
                'content-disposition': 'attachment',
                'content-length': str(size),
                'content-range': f'bytes 0-{size - 1}/{size}',
                'content-type': 'application/octet-stream',
            },
        ),
    )


async def private_retrieval_work(api: FakeApi) -> None:
    """Stream, download and save already-authorized retrieval work."""
    client = api.client()
    private = client.private_artifacts
    proof = private.new_retrieval_proof()
    work = _work('retrieve_session', client_proof_digest=proof.digest)
    api.answer('POST', TICKETS, *(_retrieval_tickets(work, proof) * 3))
    _answer_retrieval(api)

    async for _chunk in private.aretrieve_stream(work, proof=proof, chunk_bytes=CHUNK_BYTES):
        pass
    await private.adownload(work, proof=proof, chunk_bytes=CHUNK_BYTES)
    with tempfile.TemporaryDirectory() as folder:
        await private.adownload_to(
            work, Path(folder) / 'private.pdf', proof=proof, chunk_bytes=CHUNK_BYTES
        )


async def private_retrieval_ref(api: FakeApi) -> None:
    """Resolve a message attachment, then download its original and its editable source."""
    client = api.client()
    private = client.private_artifacts
    proof = _pin_proof(client)
    original = _work('retrieve_session', client_proof_digest=proof.digest)
    source = _work(
        'retrieve_session', client_proof_digest=proof.digest, retrieval_part='editable_source'
    )
    resolved = [Reply(json=work.model_dump(mode='json')) for work in (source, original, source)]
    api.answer('POST', RESOLVE, *resolved)
    api.answer(
        'POST',
        TICKETS,
        *_retrieval_tickets(original, proof),
        *_retrieval_tickets(source, proof),
    )
    _answer_retrieval(api)

    await private.aresolve(
        _ref(), session_id='ses_1', proof=proof, retrieval_part='editable_source'
    )
    await private.adownload(_ref(), session_id='ses_1', chunk_bytes=CHUNK_BYTES)
    await private.adownload_source(_ref(), session_id='ses_1')


async def private_export(api: FakeApi) -> None:
    """Resolve, authorize and run one redacted export of a message attachment."""
    work = _work('redacted_export', export_filename=EXPORT_FILENAME)
    receipt = {
        'receipt_id': 'rcpt_export_1',
        'ordinary_artifact': {
            'artifact_id': 'art_1',
            'logical_output_id': 'out_1',
            'revision': 1,
            'kind': 'document',
            'mime_type': 'application/pdf',
            'custody': 'ordinary',
            'state': 'available',
            'created_at': '2099-10-09T12:00:30Z',
            'effective_retention': {
                'policy_snapshot_id': 'pol_1',
                'retention_class': 'invocation_artifact_30d',
                'expires_at': EXPIRES,
            },
            'producer': {
                'producer_class': 'vault',
                'producer_id': 'verified-redacted-export',
                'root_invocation_id': 'inv_1',
            },
            'display_filename': EXPORT_FILENAME,
            'size_bytes': 1024,
            'sha256': 'c' * 64,
            'validation_receipt': {
                'receipt_id': 'rcpt_validation_1',
                'status': 'validated',
                'validator_profile': 'document-default',
                'validator_version': '1.0.0',
            },
            'safe_preview': {'kind': 'document', 'page_count': 1},
            'retrieval_action': {'relation': 'artifact.download_authorization'},
        },
        'correlation_id': work.intent_id,
        'occurred_at': '2099-10-09T12:00:30Z',
    }
    api.answer('POST', RESOLVE, Reply(json=work.model_dump(mode='json')))
    api.answer(
        'POST',
        TICKETS,
        *_authorize_tickets(work),
        _effect_ticket(
            work, f'{ARTIFACT}/redacted-export', **{'x-maivn-export-filename': EXPORT_FILENAME}
        ),
    )
    _answer_authorization(api)
    api.answer(
        'POST',
        f'{ARTIFACT}/redacted-export',
        Reply(status=HTTPStatus.ACCEPTED, json=_accepted('redacted_export'), headers=NO_STORE),
    )
    api.answer('POST', BIND_STATUS, Reply(json=_status(work, redacted_export_receipt=receipt)))

    await api.client().private_artifacts.aexport_redacted(
        _ref(), session_id='ses_1', filename=EXPORT_FILENAME
    )


def _delete_status(work: VaultWorkRequest) -> dict[str, Any]:
    receipt = {
        'receipt_id': 'rcpt_delete_1',
        'artifact_id': RECORD,
        'operation': 'delete',
        'state': 'deletion_pending_backup_expiry',
        'correlation_id': work.intent_id,
        'occurred_at': '2099-10-09T12:00:30Z',
    }
    return _status(work, lifecycle_receipt=receipt)


async def private_delete(api: FakeApi) -> None:
    """Resolve, authorize and run one deletion that includes redacted exports."""
    work = _work('delete', deletion_scope={'mode': 'include_redacted_exports'})
    api.answer('POST', RESOLVE, Reply(json=work.model_dump(mode='json')))
    api.answer(
        'POST', TICKETS, *_authorize_tickets(work), _effect_ticket(work, f'{ARTIFACT}/deletion')
    )
    _answer_authorization(api)
    api.answer(
        'POST',
        f'{ARTIFACT}/deletion',
        Reply(status=HTTPStatus.ACCEPTED, json=_accepted('delete'), headers=NO_STORE),
    )
    api.answer('POST', BIND_STATUS, Reply(json=_delete_status(work)))

    await api.client().private_artifacts.adelete(
        _ref(), session_id='ses_1', include_redacted_exports=True
    )


async def private_status(api: FakeApi) -> None:
    """Read one effect's authenticated status by its server poll handle."""
    work = _work('delete', deletion_scope={'mode': 'private_only'})
    api.answer('POST', '/v1/private-artifacts/intents/status', Reply(json=_delete_status(work)))

    await api.client().private_artifacts.astatus(SERVER_INTENT_ID)


def _ingest_work(
    proof: TransientVaultClientProof, output: bytes, source: bytes
) -> VaultWorkRequest:
    raw = _proof_raw(proof)
    return _work(
        'ingest_file',
        client_proof_digest=proof.digest,
        file_intake={
            'kind': 'document',
            'mime_type': 'application/pdf',
            'output_commitment': vault_file_intake_commitment(raw, 'private_original', output),
            'source_commitment': vault_file_intake_commitment(raw, 'editable_source', source),
            'max_source_bytes': MAX_SOURCE_BYTES,
            'expected_base_ref': _ref().model_dump(mode='json'),
        },
    )


def _ingested(work: VaultWorkRequest) -> Reply:
    """Answer bind-status with the committed new revision of the base artifact."""
    artifact = _ref(
        artifact_id='gen_2',
        revision=2,
        supersedes_artifact_id=GENERATION,
        producer={
            'producer_class': 'vault',
            'producer_id': 'vault_intake',
            'root_invocation_id': 'inv_1',
            'session_id': 'ses_1',
            'dispatch_id': OPERATION_ID,
        },
    )
    return Reply(json=_status(work, artifact=artifact.model_dump(mode='json')))


def _ingest_ticket(work: VaultWorkRequest, proof: TransientVaultClientProof) -> Reply:
    return _ticket(
        'POST',
        f'{INGESTIONS}/start/{OPERATION_ID}',
        body=vault_work_request_json_bytes(work),
        public_headers={
            'content-type': 'application/json',
            'x-maivn-operation-id': OPERATION_ID,
        },
        digests={CLIENT_PROOF_HEADER: proof.digest},
    )


async def private_publish_file(api: FakeApi) -> None:
    """Prepare, authorize and upload a generated file and its editable source to Vault."""
    client = api.client()
    proof = _pin_proof(client)
    output_bytes = b'%PDF-private-output'
    source_bytes = b'{"source": true}'
    work = _ingest_work(proof, output_bytes, source_bytes)
    session = {
        'server_intent_id': SERVER_INTENT_ID,
        'operation_id': OPERATION_ID,
        'upload_id': UPLOAD_ID,
        'expires_at': '2099-10-09T12:30:00Z',
        'state': 'accepting',
    }
    sealed = Reply(json={'state': 'sealed'}, headers=NO_STORE)
    upload = f'{INGESTIONS}/{UPLOAD_ID}'
    api.answer('POST', '/v1/private-artifacts/ingestions', Reply(json=work.model_dump(mode='json')))
    api.answer('POST', TICKETS, *_authorize_tickets(work), _ingest_ticket(work, proof))
    _answer_authorization(api)
    api.answer('POST', f'{INGESTIONS}/start/{OPERATION_ID}', Reply(json=session, headers=NO_STORE))
    api.answer('PUT', f'{upload}/parts/private_original', sealed)
    api.answer('PUT', f'{upload}/parts/editable_source', sealed)
    api.answer(
        'POST',
        f'{upload}/complete',
        Reply(status=HTTPStatus.ACCEPTED, json=_accepted('ingest_file'), headers=NO_STORE),
    )
    api.answer('POST', BIND_STATUS, _ingested(work))

    with tempfile.TemporaryDirectory() as folder:
        path = Path(folder) / 'report.pdf'
        path.write_bytes(output_bytes)
        generated = GeneratedFile(
            kind='generated_file',
            artifact_kind='document',
            custody='vault_private',
            filename=path.name,
            path=str(path),
            mime_type='application/pdf',
            size_bytes=len(output_bytes),
            sha256=hashlib.sha256(output_bytes).hexdigest(),
            private_expected_base=_ref(),
        )
        await client.private_artifacts.apublish_file(
            generated, source=source_bytes, session_id='ses_1', call_id='call_1', output_index=1
        )


async def private_ingest_recovered(api: FakeApi) -> None:
    """Ingest prepared work whose grant was already delivered: recover, then find it queued."""
    client = api.client()
    proof = client.private_artifacts.new_retrieval_proof()
    output_bytes = b'%PDF-private-output'
    source_bytes = b'{"source": true}'
    work = _ingest_work(proof, output_bytes, source_bytes)
    recovered = {'operation_id': OPERATION_ID, 'replayed': True}
    api.answer(
        'POST',
        TICKETS,
        *_authorize_tickets(work)[:2],
        _ticket(
            'POST',
            f'{INGESTIONS}/recover',
            body=vault_work_request_json_bytes(work),
            public_headers={'content-type': 'application/json'},
            digests={CLIENT_PROOF_HEADER: proof.digest},
        ),
        _ingest_ticket(work, proof),
    )
    _answer_authorization(api, delivered=False)
    api.answer('POST', f'{INGESTIONS}/recover', Reply(json=recovered, headers=NO_STORE))
    api.answer(
        'POST',
        f'{INGESTIONS}/start/{OPERATION_ID}',
        Reply(json=_accepted('ingest_file'), headers=NO_STORE),
    )
    api.answer('POST', BIND_STATUS, _ingested(work))

    with tempfile.TemporaryDirectory() as folder:
        output = Path(folder) / 'report.pdf'
        source = Path(folder) / 'report.source'
        output.write_bytes(output_bytes)
        source.write_bytes(source_bytes)
        await client.private_artifacts.aingest_file(work, proof=proof, output=output, source=source)


DRIVERS = (
    private_publish_file,
    private_ingest_recovered,
    private_retrieval_work,
    private_retrieval_ref,
    private_export,
    private_delete,
    private_status,
)
