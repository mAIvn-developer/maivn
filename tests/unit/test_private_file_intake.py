"""Private output and editable source travel only to the configured Vault."""

from __future__ import annotations

import asyncio
import hashlib
from typing import TYPE_CHECKING, Any

import httpx
import pytest
from maivn_contracts.artifacts import (
    GeneratedFile,
    PrivateArtifactRef,
    PrivateWorkIntentStatus,
    VaultWorkRequest,
    vault_file_intake_commitment,
    vault_grant_header_digest,
)

from maivn import Client, PrivateArtifactError
from maivn._internal.private_artifacts import (
    PrivateArtifactsClient,
    TransientVaultClientProof,
    _proof_raw,  # pyright: ignore[reportPrivateUsage]
)

if TYPE_CHECKING:
    from pathlib import Path

_SOURCE_BOUND = 50 * 1024 * 1024


@pytest.mark.parametrize(
    'fault',
    [
        None,
        'replay',
        'lost_approval',
        'lost_redemption',
        'revision_gap',
        'commitment',
        'upload_redirect',
        'wrong_operation',
    ],
)
@pytest.mark.parametrize('mode', ['work', 'publish'])
def test_private_file_upload_checks_commitments_and_scopes_every_binary_request(  # noqa: C901, PLR0915 - fault matrix asserts the complete custody boundary.
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    fault: str | None,
    mode: str,
) -> None:
    """Exact upload streams bypass Data Plane and reject substitutions without disclosing input."""
    output = tmp_path / 'private.pdf'
    source = tmp_path / 'editable.json'
    output.write_bytes(b'PRIVATE-OUTPUT-SENTINEL')
    source.write_bytes(b'PRIVATE-SOURCE-SENTINEL')
    requests: list[httpx.Request] = []
    authorized: list[str] = []

    def transport(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        assert request.url.host == 'vault.example.test'
        assert 'authorization' not in request.headers
        assert request.headers['x-maivn-client-proof'] == raw_proof
        if fault == 'upload_redirect':
            return httpx.Response(307, headers={'location': 'https://elsewhere.example.test'})
        if request.method == 'PUT':
            assert request.content in (output.read_bytes(), source.read_bytes())
            result = {'state': 'sealed'}
        else:
            assert request.url.path.endswith('/complete')
            assert request.content == b''
            result = {
                'server_intent_id': 'server-intent',
                'operation_id': 'operation',
                'operation': 'ingest_file',
                'state': 'queued',
            }
        return httpx.Response(
            200,
            json=result,
            headers={
                'cache-control': 'no-store',
                'x-content-type-options': 'nosniff',
            },
        )

    client = Client(
        api_key='project-key',
        base_url='https://vault.example.test',
        vault_transport=httpx.MockTransport(transport),
    )
    proof = client.private_artifacts.new_retrieval_proof()
    raw_proof = _proof_raw(proof)
    generated = GeneratedFile(
        kind='generated_file',
        artifact_kind='document',
        custody='ordinary',
        filename=output.name,
        path=str(output),
        mime_type='application/pdf',
        size_bytes=output.stat().st_size,
        sha256=hashlib.sha256(output.read_bytes()).hexdigest(),
    )
    work = VaultWorkRequest.model_validate(
        {
            'schema_version': 'vault-work-v1',
            'intent_id': 'client-intent',
            'idempotency_key': 'idempotency',
            'operation': 'ingest_file',
            'scope': {
                'organization_id': '00000000-0000-4000-8000-000000000001',
                'project_id': '00000000-0000-4000-8000-000000000002',
            },
            'surface': {
                'root_invocation_id': 'root',
                'session_id': 'session',
                'checkpoint_id': 'checkpoint',
                'caller_id': 'caller',
            },
            'input_handles': [],
            'bounds': {'max_output_bytes': 1024},
            'retention_policy_id': 'retention',
            'expires_at': '2099-09-05T12:00:00Z',
            'client_proof_digest': proof.digest,
            'file_intake': {
                'kind': 'document',
                'mime_type': 'application/pdf',
                'max_source_bytes': 1024,
                'output_commitment': vault_file_intake_commitment(
                    raw_proof, 'private_original', output.read_bytes()
                ),
                'source_commitment': vault_file_intake_commitment(
                    raw_proof, 'editable_source', source.read_bytes()
                ),
            },
        }
    )
    ref = PrivateArtifactRef.model_validate(
        {
            'artifact_id': 'private-artifact',
            'logical_output_id': 'private-output',
            'revision': 1,
            'kind': 'document',
            'mime_type': 'application/pdf',
            'created_at': '2026-09-05T12:00:00Z',
            'effective_retention': {
                'policy_snapshot_id': 'policy',
                'retention_class': 'private',
                'expires_at': work.expires_at,
            },
            'custody': 'vault_private',
            'state': 'available_in_vault',
            'display_label': 'Private document',
            'producer': {
                'producer_class': 'vault',
                'producer_id': 'vault',
                'root_invocation_id': 'root',
                'dispatch_id': 'operation',
            },
            'creation_receipt_id': 'receipt',
            'retrieval_action': {'relation': 'artifact.vault_download_authorization'},
        }
    )
    if fault == 'revision_gap':
        base = ref
        ref = PrivateArtifactRef.model_validate(
            {
                **ref.model_dump(mode='json'),
                'artifact_id': 'new-artifact',
                'revision': 3,
                'supersedes_artifact_id': base.artifact_id,
            }
        )
        raw_work = work.model_dump(mode='json')
        raw_work['input_handles'] = [
            {
                'local_id': 'source',
                'role': 'source_artifact',
                'record_handle': base.logical_output_id,
                'generation_handle': base.artifact_id,
            }
        ]
        raw_work['file_intake']['expected_base_ref'] = base.model_dump(mode='json')
        work = VaultWorkRequest.model_validate(raw_work)
        generated = generated.model_copy(update={'private_expected_base': base})

    async def start(_self: object, **arguments: Any) -> httpx.Response:
        action = arguments['operation']
        if action == 'admit':
            authorized.append('admit')
            return httpx.Response(
                200,
                json={
                    'intent_id': 'server-intent',
                    'state': 'admitted',
                    'replayed': len(authorized) > 1,
                },
            )
        if action == 'approve':
            if fault == 'lost_approval' and len(authorized) == 1:
                raise PrivateArtifactError
            replay = fault in {'replay', 'lost_approval'} or (
                fault == 'lost_redemption' and len(authorized) > 1
            )
            return httpx.Response(
                200,
                json={
                    'intent_id': 'server-intent',
                    'state': 'executing_in_vault',
                    'expires_at': work.expires_at,
                    'grant_credential_digest': vault_grant_header_digest(
                        'Z2dnZ2dnZ2dnZ2dnZ2dnZ2dnZ2dnZ2dnZ2dnZ2dnZ2c'
                    ),
                    'credential_delivery': 'not_replayable' if replay else 'delivered',
                    'grant_credential': None
                    if replay
                    else 'Z2dnZ2dnZ2dnZ2dnZ2dnZ2dnZ2dnZ2dnZ2dnZ2dnZ2c',
                },
                headers={'cache-control': 'no-store', 'x-content-type-options': 'nosniff'},
            )
        if action == 'redeem':
            if fault == 'lost_redemption' and len(authorized) == 1:
                raise PrivateArtifactError
            return httpx.Response(200, json={'operation_id': 'operation', 'replayed': False})
        if action == 'recover_ingestion':
            assert arguments['capabilities'] == {'x-maivn-client-proof': raw_proof}
            assert arguments['capability_digests'] == {'x-maivn-client-proof': proof.digest}
            assert arguments['work'] == work or mode == 'publish'
            return httpx.Response(200, json={'operation_id': 'operation', 'replayed': True})
        assert arguments['operation'] == 'ingest_file'
        assert arguments['capabilities']['x-maivn-client-proof'] == raw_proof
        if fault == 'replay':
            return httpx.Response(
                200,
                json={
                    'server_intent_id': 'server-intent',
                    'operation_id': 'operation',
                    'operation': 'ingest_file',
                    'state': 'queued',
                },
            )
        return httpx.Response(
            200,
            json={
                'server_intent_id': 'server-intent',
                'operation_id': 'foreign' if fault == 'wrong_operation' else 'operation',
                'upload_id': 'upload',
                'expires_at': work.expires_at,
                'state': 'accepting',
            },
        )

    async def status(_self: object, _work: object, _accepted: object) -> PrivateWorkIntentStatus:
        return PrivateWorkIntentStatus(
            intent_id='server-intent',
            correlation_id=work.intent_id,
            operation='ingest_file',
            idempotency_key=work.idempotency_key,
            expires_at=work.expires_at,
            state='committed',
            artifact=ref,
        )

    monkeypatch.setattr(PrivateArtifactsClient, '_ticket_call', start)
    monkeypatch.setattr(PrivateArtifactsClient, '_bind_and_poll', status)

    async def prepare(_self: object, path: str, payload: dict[str, Any]) -> dict[str, Any]:
        assert path == '/v1/private-artifacts/ingestions'
        assert payload['client_proof_digest'] == proof.digest
        assert str(output) not in str(payload)
        assert generated.filename not in str(payload)
        assert generated.sha256 not in str(payload)
        assert 'SENTINEL' not in str(payload)
        assert payload['file_intake']['max_source_bytes'] == _SOURCE_BOUND
        candidate = work.model_dump(mode='json')
        candidate['file_intake'] = payload['file_intake']
        return candidate

    monkeypatch.setattr(PrivateArtifactsClient, '_data_post', prepare)

    def fixed_proof(_self: object) -> TransientVaultClientProof:
        return proof

    async def upload() -> PrivateArtifactRef:
        if mode == 'publish':
            return await client.private_artifacts.apublish_file(
                generated,
                source=source.read_bytes(),
                session_id='session',
                call_id='call',
            )
        return await client.private_artifacts.aingest_file(
            work,
            proof=proof,
            output=output,
            source=source,
        )

    monkeypatch.setattr(PrivateArtifactsClient, 'new_retrieval_proof', fixed_proof)
    if fault == 'commitment':
        output.write_bytes(b'CHANGED-PRIVATE-SENTINEL')
    if fault in {'lost_approval', 'lost_redemption'}:
        with pytest.raises(PrivateArtifactError):
            asyncio.run(upload())
        assert requests == []
    if fault not in (None, 'replay', 'lost_approval', 'lost_redemption', 'revision_gap'):
        with pytest.raises(PrivateArtifactError) as error:
            asyncio.run(upload())
        assert 'SENTINEL' not in str(error.value)
        assert error.value.__context__ is None
        if fault in {'commitment', 'wrong_operation'}:
            assert requests == []
        if fault == 'commitment':
            assert authorized == []
    else:
        if mode == 'publish':
            assert (
                client.private_artifacts.publish_file(
                    generated,
                    source=source.read_bytes(),
                    session_id='session',
                    call_id='call',
                )
                == ref
            )
        else:
            assert (
                client.private_artifacts.ingest_file(
                    work,
                    proof=proof,
                    output=output,
                    source=source,
                )
                == ref
            )
        expected_methods = [] if fault == 'replay' else ['PUT', 'PUT', 'POST']
        assert [request.method for request in requests] == expected_methods
