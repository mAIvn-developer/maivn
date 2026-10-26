"""Characterize private file intake: commitments, bounded uploads and verified references.

The public ``publish_file`` / ``ingest_file`` APIs run against wire-level Data and Vault
doubles. Private bytes, file metadata and the raw client proof must reach only the Vault.
"""

from __future__ import annotations

import asyncio
import base64
import hashlib
import json
import os
import re
from typing import TYPE_CHECKING, Any, cast

import httpx
import pytest
from maivn_contracts.artifacts import (
    GeneratedFile,
    PrivateArtifactRef,
    PrivateWorkIntentStatus,
    VaultActionTicket,
    VaultGrantCredentialDelivery,
    VaultSenderAssertion,
    VaultWorkRequest,
    vault_actual_request_digest_from_prehashed,
    vault_assertion_json_bytes,
    vault_client_proof_header_digest,
    vault_file_intake_commitment,
    vault_grant_header_digest,
)

from maivn import Client, PrivateArtifactError, TransientVaultClientProof

if TYPE_CHECKING:
    from collections.abc import Callable, Mapping
    from pathlib import Path

_ORIGIN = 'https://vault.example.test'
_INTENT = 'server_intent_0001'
_OPERATION = 'operation_0001'
_UPLOAD = 'upload_0001'
_LATER = '2099-08-14T13:00:00Z'
_REFUSED = 'private_artifact_request_refused'
_PROOF_HEADER = 'x-maivn-client-proof'
_OUTPUT = b'PRIVATE-OUTPUT-SENTINEL'
_SOURCE = b'PRIVATE-SOURCE-SENTINEL'
_MAX_FILE_BYTES = 52_428_800


def _b64(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).rstrip(b'=').decode('ascii')


_GRANT = _b64(b'g' * 32)


def _ticket(*, path: str, digests: Mapping[str, str]) -> dict[str, Any]:
    client_digests = cast('dict[Any, Any]', dict(digests))
    actual = vault_actual_request_digest_from_prehashed(
        'POST', path, b'', {}, client_digests, expected_client_headers=frozenset(client_digests)
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
        issued_at='2099-08-14T12:00:00Z',
        nonce='nonce_0000000000000000000000000001',
        expires_at='2099-08-14T12:01:00Z',
    )
    return VaultActionTicket.model_validate(
        {
            'method': 'POST',
            'path': path,
            'url': f'{_ORIGIN}{path}',
            'public_headers': {},
            'client_header_digests': client_digests,
            'sender_assertion_base64url': _b64(vault_assertion_json_bytes(assertion)),
            'sender_signature_base64url': _b64(b's' * 64),
            'body_base64url': None,
            'expires_at': '2099-08-14T12:01:00Z',
        }
    ).model_dump(mode='json')


def _reply(status: int, payload: object) -> httpx.Response:
    body = json.dumps(payload).encode()
    return httpx.Response(
        status,
        headers=[
            ('content-type', 'application/json'),
            ('content-length', str(len(body))),
            ('cache-control', 'no-store'),
            ('x-content-type-options', 'nosniff'),
        ],
        stream=httpx.ByteStream(body),
    )


def _accepted(
    *, operation_id: str = _OPERATION, intent: str = _INTENT, operation: str = 'ingest_file'
) -> dict[str, str]:
    return {
        'server_intent_id': intent,
        'operation_id': operation_id,
        'operation': operation,
        'state': 'queued',
    }


def _ref(**changes: Any) -> PrivateArtifactRef:
    payload: dict[str, Any] = {
        'artifact_id': 'private_artifact_0001',
        'logical_output_id': 'private_output_0001',
        'revision': 1,
        'kind': 'document',
        'mime_type': 'application/pdf',
        'created_at': '2026-09-05T12:00:00Z',
        'effective_retention': {
            'policy_snapshot_id': 'policy',
            'retention_class': 'private',
            'expires_at': _LATER,
        },
        'custody': 'vault_private',
        'state': 'available_in_vault',
        'display_label': 'Private document',
        'producer': {
            'producer_class': 'vault',
            'producer_id': 'vault',
            'root_invocation_id': 'root',
            'dispatch_id': _OPERATION,
        },
        'creation_receipt_id': 'receipt',
        'retrieval_action': {'relation': 'artifact.vault_download_authorization'},
    }
    producer = cast('dict[str, Any]', payload['producer'])
    producer.update(changes.pop('producer', {}))
    payload.update(changes)
    return PrivateArtifactRef.model_validate(payload)


def _work_payload(payload: Mapping[str, Any], *, max_output_bytes: int = 1024) -> dict[str, Any]:
    return {
        'schema_version': 'vault-work-v1',
        'intent_id': 'client_intent_0001',
        'idempotency_key': 'idempotency_0001',
        'operation': 'ingest_file',
        'scope': {
            'organization_id': '00000000-0000-4000-8000-000000000001',
            'project_id': '00000000-0000-4000-8000-000000000002',
        },
        'surface': {
            'root_invocation_id': 'root',
            'session_id': payload['session_id'],
            'checkpoint_id': 'checkpoint',
            'caller_id': 'caller',
        },
        'input_handles': [],
        'bounds': {'max_output_bytes': max_output_bytes},
        'retention_policy_id': 'retention',
        'expires_at': _LATER,
        'client_proof_digest': payload['client_proof_digest'],
        'file_intake': payload['file_intake'],
    }


class Platform:
    """Wire-level Data plane and Vault doubles for private file intake."""

    def __init__(self) -> None:
        """Start with happy-path replies."""
        self.data: list[tuple[str, dict[str, Any]]] = []
        self.data_bodies: list[bytes] = []
        self.vault: list[httpx.Request] = []
        self.vault_bodies: list[bytes] = []
        self.prepare: Callable[[dict[str, Any]], dict[str, Any]] = _work_payload
        self.replies: dict[str, Callable[[httpx.Request], httpx.Response]] = {}
        self.artifact: PrivateArtifactRef = _ref()
        self.data_failure: BaseException | None = None

    def client(self) -> Client:
        """Return a client whose traffic reaches these doubles."""
        return Client(
            api_key='sdk-project-key',
            base_url=_ORIGIN,
            transport=httpx.MockTransport(self._data),
            vault_transport=httpx.MockTransport(self._vault),
        )

    @property
    def vault_operations(self) -> list[str]:
        """Return the method and last path segment of every Vault request."""
        return [f'{request.method} {request.url.path.rsplit("/", 1)[-1]}' for request in self.vault]

    def _data(self, request: httpx.Request) -> httpx.Response:
        if self.data_failure is not None:
            raise self.data_failure
        payload = cast('dict[str, Any]', json.loads(request.content))
        self.data.append((request.url.path, payload))
        self.data_bodies.append(request.content)
        if request.url.path == '/v1/private-artifacts/ingestions':
            return httpx.Response(200, json=self.prepare(payload))
        if request.url.path == '/v1/private-artifacts/tickets':
            operation = payload['operation']
            return httpx.Response(
                200,
                json=_ticket(
                    path=f'/v1/vault/ops/{operation}', digests=payload['client_header_digests']
                ),
            )
        assert request.url.path == '/v1/private-artifacts/intents/bind-status'
        status = PrivateWorkIntentStatus.model_validate(
            {
                'intent_id': _INTENT,
                'operation': 'ingest_file',
                'correlation_id': 'client_intent_0001',
                'idempotency_key': 'idempotency_0001',
                'expires_at': _LATER,
                'state': 'committed',
                'artifact': self.artifact.model_dump(mode='json'),
            }
        )
        return httpx.Response(200, json=status.model_dump(mode='json'))

    async def _vault(self, request: httpx.Request) -> httpx.Response:
        self.vault.append(request)
        self.vault_bodies.append(await request.aread())
        operation = request.url.path.rsplit('/', 1)[-1]
        reply = self.replies.get(operation)
        if reply is not None:
            return reply(request)
        return self.default(operation)

    def default(self, operation: str) -> httpx.Response:
        """Answer one Vault call with its happy-path reply."""
        replies: dict[str, object] = {
            'admit': {'intent_id': _INTENT, 'state': 'authorization_pending', 'replayed': False},
            'approve': VaultGrantCredentialDelivery(
                intent_id=_INTENT,
                state='executing_in_vault',
                grant_credential_digest=vault_grant_header_digest(_GRANT),
                credential_delivery='delivered',
                grant_credential=_GRANT,
                expires_at=_LATER,
            ).model_dump(mode='json'),
            'redeem': {'operation_id': _OPERATION, 'replayed': False},
            'recover_ingestion': {'operation_id': _OPERATION, 'replayed': True},
            'ingest_file': {
                'server_intent_id': _INTENT,
                'operation_id': _OPERATION,
                'upload_id': _UPLOAD,
                'expires_at': _LATER,
                'state': 'accepting',
            },
            'private_original': {'state': 'sealed'},
            'editable_source': {'state': 'sealed'},
            'complete': _accepted(),
        }
        return _reply(200, replies[operation])


def _generated(tmp_path: Path, content: bytes = _OUTPUT, **changes: Any) -> GeneratedFile:
    output = tmp_path / 'private-report-sentinel.pdf'
    output.write_bytes(content)
    payload: dict[str, Any] = {
        'kind': 'generated_file',
        'artifact_kind': 'document',
        'custody': 'ordinary',
        'filename': output.name,
        'path': str(output),
        'mime_type': 'application/pdf',
        'size_bytes': len(content),
        'sha256': hashlib.sha256(content).hexdigest(),
    }
    payload.update(changes)
    return GeneratedFile.model_validate(payload)


def _publish(
    platform: Platform, generated: GeneratedFile, source: object = _SOURCE
) -> PrivateArtifactRef:
    return platform.client().private_artifacts.publish_file(
        generated, source=cast('bytes', source), session_id='session_0001', call_id='call_0001'
    )


def _refused(caught: pytest.ExceptionInfo[PrivateArtifactError]) -> None:
    assert type(caught.value) is PrivateArtifactError
    assert str(caught.value) == _REFUSED
    assert caught.value.__cause__ is None
    assert caught.value.__context__ is None


def _raw_proof(platform: Platform) -> str:
    proofs = {request.headers.get(_PROOF_HEADER) for request in platform.vault} - {None}
    assert len(proofs) == 1
    return cast('str', proofs.pop())


def test_publish_commits_both_parts_and_keeps_private_inputs_off_data(tmp_path: Path) -> None:
    """Data gets commitments and digests only; both parts stream directly to Vault."""
    platform = Platform()
    generated = _generated(tmp_path)
    assert _publish(platform, generated) == platform.artifact
    assert platform.vault_operations == [
        'POST admit',
        'POST approve',
        'POST redeem',
        'POST ingest_file',
        'PUT private_original',
        'PUT editable_source',
        'POST complete',
    ]
    raw_proof = _raw_proof(platform)
    assert re.fullmatch(r'[A-Za-z0-9_-]{43}', raw_proof) is not None
    prepare = platform.data[0][1]
    digest = vault_client_proof_header_digest(raw_proof)
    assert prepare == {
        'session_id': 'session_0001',
        'call_id': 'call_0001',
        'output_index': 0,
        'client_proof_digest': digest,
        'file_intake': {
            'kind': 'document',
            'mime_type': 'application/pdf',
            'output_commitment': vault_file_intake_commitment(
                raw_proof, 'private_original', _OUTPUT
            ),
            'source_commitment': vault_file_intake_commitment(
                raw_proof, 'editable_source', _SOURCE
            ),
            'max_source_bytes': _MAX_FILE_BYTES,
            'source_format': 'maivn-source-archive-v1',
        },
    }
    for secret in (raw_proof.encode(), _OUTPUT, _SOURCE, b'sentinel.pdf', str(tmp_path).encode()):
        assert all(secret not in body for body in platform.data_bodies)
    uploads = platform.vault[4:]
    assert [str(request.url) for request in uploads] == [
        f'{_ORIGIN}/v1/vault/artifacts/ingestions/{_UPLOAD}/parts/private_original',
        f'{_ORIGIN}/v1/vault/artifacts/ingestions/{_UPLOAD}/parts/editable_source',
        f'{_ORIGIN}/v1/vault/artifacts/ingestions/{_UPLOAD}/complete',
    ]
    assert platform.vault_bodies[4:] == [_OUTPUT, _SOURCE, b'']
    assert [request.headers.get('content-type') for request in uploads[:2]] == [
        'application/octet-stream'
    ] * 2
    assert all('authorization' not in request.headers for request in platform.vault)
    assert [request.headers.get(_PROOF_HEADER) for request in platform.vault] == [
        None,
        None,
        None,
        raw_proof,
        raw_proof,
        raw_proof,
        raw_proof,
    ]


def test_retrying_one_output_reuses_its_proof_and_others_get_new_ones(tmp_path: Path) -> None:
    """A retried publication keeps its proof; a different call gets a fresh proof."""
    platform = Platform()
    platform.prepare = lambda _payload: {}
    client = platform.client()
    generated = _generated(tmp_path)
    for call in ('call_a', 'call_a', 'call_b'):
        with pytest.raises(PrivateArtifactError):
            client.private_artifacts.publish_file(
                generated, source=_SOURCE, session_id='session_0001', call_id=call
            )
    digests = [payload['client_proof_digest'] for _path, payload in platform.data]
    assert digests[0] == digests[1]
    assert digests[2] != digests[0]


def test_retained_proofs_are_capped_at_1024_outputs(tmp_path: Path) -> None:
    """After 1024 distinct pending outputs a new one is refused before any request."""
    platform = Platform()
    platform.prepare = lambda _payload: {}
    client = platform.client()
    generated = _generated(tmp_path)

    async def fill() -> None:
        for index in range(1024):
            with pytest.raises(PrivateArtifactError):
                await client.private_artifacts.apublish_file(
                    generated, source=b'', session_id='session_0001', call_id=f'call_{index}'
                )

    asyncio.run(fill())
    assert platform.data == []
    with pytest.raises(PrivateArtifactError):
        client.private_artifacts.publish_file(
            generated, source=_SOURCE, session_id='session_0001', call_id='call_new'
        )
    assert platform.data == []
    with pytest.raises(PrivateArtifactError):
        client.private_artifacts.publish_file(
            generated, source=_SOURCE, session_id='session_0001', call_id='call_1023'
        )
    assert [path for path, _payload in platform.data] == ['/v1/private-artifacts/ingestions']


@pytest.mark.parametrize(
    'source',
    [b'', 'text-source', bytearray(_SOURCE), b'x' * (_MAX_FILE_BYTES + 1)],
    ids=['empty', 'text', 'bytearray', 'oversized'],
)
def test_invalid_sources_are_refused_before_any_request(tmp_path: Path, source: object) -> None:
    """Sources must be non-empty bytes no larger than 50 MiB."""
    platform = Platform()
    with pytest.raises(PrivateArtifactError) as caught:
        _publish(platform, _generated(tmp_path), source)
    _refused(caught)
    assert platform.data == []


@pytest.mark.parametrize('change', ['size', 'sha256', 'directory'])
def test_output_must_match_its_generated_description(tmp_path: Path, change: str) -> None:
    """A changed size, hash or a non-file output is refused before Data is asked."""
    generated = _generated(tmp_path)
    if change == 'size':
        generated = generated.model_copy(update={'size_bytes': generated.size_bytes + 1})
    elif change == 'sha256':
        generated = generated.model_copy(update={'sha256': 'f' * 64})
    else:
        generated = generated.model_copy(update={'path': str(tmp_path)})
    platform = Platform()
    with pytest.raises(PrivateArtifactError) as caught:
        _publish(platform, generated)
    _refused(caught)
    assert platform.data == []


def _tamper_operation(work: dict[str, Any]) -> None:
    work['operation'] = 'create'


def _tamper_manifest(work: dict[str, Any]) -> None:
    work['file_intake'] = {**work['file_intake'], 'output_commitment': '0' * 64}


def _tamper_source_bound(work: dict[str, Any]) -> None:
    work['file_intake'] = {**work['file_intake'], 'max_source_bytes': 4096}


def _tamper_proof(work: dict[str, Any]) -> None:
    work['client_proof_digest'] = '0' * 64


def _tamper_session(work: dict[str, Any]) -> None:
    work['surface'] = {**work['surface'], 'session_id': 'other_session'}


def _tamper_bound(work: dict[str, Any]) -> None:
    work['bounds'] = {'max_output_bytes': len(_OUTPUT) - 1}


@pytest.mark.parametrize(
    'tamper',
    [
        _tamper_operation,
        _tamper_manifest,
        _tamper_source_bound,
        _tamper_proof,
        _tamper_session,
        _tamper_bound,
    ],
)
def test_prepared_work_must_echo_the_request(
    tmp_path: Path, tamper: Callable[[dict[str, Any]], None]
) -> None:
    """Server work that differs from what was asked is refused before Vault is contacted."""
    platform = Platform()

    def prepare(payload: dict[str, Any]) -> dict[str, Any]:
        work = _work_payload(payload)
        tamper(work)
        return work

    platform.prepare = prepare
    with pytest.raises(PrivateArtifactError) as caught:
        _publish(platform, _generated(tmp_path))
    _refused(caught)
    assert platform.vault == []


def test_output_exactly_at_the_admitted_bound_is_accepted(tmp_path: Path) -> None:
    """An output equal to the admitted byte bound is published."""
    platform = Platform()
    platform.prepare = lambda payload: _work_payload(payload, max_output_bytes=len(_OUTPUT))
    assert _publish(platform, _generated(tmp_path)) == platform.artifact


def test_data_failure_is_a_value_free_refusal(tmp_path: Path) -> None:
    """A failing Data plane yields the fixed refusal without any chained detail."""
    platform = Platform()
    platform.data_failure = httpx.ConnectError('sentinel-network-detail')
    with pytest.raises(PrivateArtifactError) as caught:
        _publish(platform, _generated(tmp_path))
    _refused(caught)


def test_already_accepted_ingestion_sends_no_part_again(tmp_path: Path) -> None:
    """Durable acceptance at start means no private part is uploaded again."""
    platform = Platform()
    platform.replies['ingest_file'] = lambda _request: _reply(200, _accepted())
    assert _publish(platform, _generated(tmp_path)) == platform.artifact
    assert [operation for operation in platform.vault_operations if 'PUT' in operation] == []


@pytest.mark.parametrize(
    'start',
    [
        _accepted(operation='delete'),
        _accepted(operation_id='operation_0002'),
        _accepted(intent='client_intent_0001'),
        _accepted(intent=_OPERATION),
        {
            'server_intent_id': _INTENT,
            'operation_id': _OPERATION,
            'upload_id': _UPLOAD,
            'expires_at': '2000-01-01T00:00:00Z',
            'state': 'accepting',
        },
        {
            'server_intent_id': _INTENT,
            'operation_id': _OPERATION,
            'upload_id': _UPLOAD,
            'expires_at': '2099-08-14T13:00:01Z',
            'state': 'accepting',
        },
    ],
)
def test_upload_start_must_be_bound_and_unexpired(tmp_path: Path, start: dict[str, str]) -> None:
    """A start for another operation or intent, expired or outliving the work, uploads nothing."""
    platform = Platform()
    platform.replies['ingest_file'] = lambda _request: _reply(200, start)
    with pytest.raises(PrivateArtifactError) as caught:
        _publish(platform, _generated(tmp_path))
    _refused(caught)
    assert [operation for operation in platform.vault_operations if 'PUT' in operation] == []
    assert [path for path, _payload in platform.data if path.endswith('/bind-status')] == []


def test_upload_session_expiring_with_the_work_is_accepted(tmp_path: Path) -> None:
    """An upload session that ends exactly when the work ends is accepted."""
    platform = Platform()
    assert _publish(platform, _generated(tmp_path)) == platform.artifact


@pytest.mark.parametrize(
    ('operation', 'payload'),
    [
        ('private_original', {'state': 'open'}),
        ('complete', _accepted(operation_id='operation_0002')),
        ('complete', _accepted(intent='server_intent_0002')),
        ('complete', _accepted(operation='delete')),
    ],
)
def test_part_and_completion_replies_must_be_bound(
    tmp_path: Path, operation: str, payload: dict[str, str]
) -> None:
    """An unsealed part or a completion for other work is refused."""
    platform = Platform()
    platform.replies[operation] = lambda _request: _reply(200, payload)
    with pytest.raises(PrivateArtifactError) as caught:
        _publish(platform, _generated(tmp_path))
    _refused(caught)
    assert 'bind-status' not in ' '.join(path for path, _payload in platform.data)


def test_part_upload_redirect_is_refused(tmp_path: Path) -> None:
    """A redirect from a part upload is neither followed nor accepted."""
    platform = Platform()
    platform.replies['private_original'] = lambda _request: httpx.Response(
        307, headers={'location': 'https://elsewhere.example.test/'}
    )
    with pytest.raises(PrivateArtifactError):
        _publish(platform, _generated(tmp_path))
    assert all(request.url.host == 'vault.example.test' for request in platform.vault)


@pytest.mark.parametrize(
    'artifact',
    [
        _ref(
            kind='spreadsheet',
            mime_type='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
            display_label='Private spreadsheet',
        ),
        _ref(mime_type='application/vnd.openxmlformats-officedocument.wordprocessingml.document'),
        _ref(producer={'root_invocation_id': 'other_root'}),
        _ref(producer={'session_id': 'other_session'}),
        _ref(producer={'dispatch_id': 'operation_0002'}),
        _ref(revision=2, supersedes_artifact_id='older_artifact'),
    ],
)
def test_committed_reference_must_describe_this_new_output(
    tmp_path: Path, artifact: PrivateArtifactRef
) -> None:
    """A reference of another kind, root, session, operation or revision is refused."""
    platform = Platform()
    platform.artifact = artifact
    with pytest.raises(PrivateArtifactError) as caught:
        _publish(platform, _generated(tmp_path))
    _refused(caught)


def test_reference_with_the_matching_session_is_accepted(tmp_path: Path) -> None:
    """A producer session equal to the work session is accepted."""
    platform = Platform()
    platform.artifact = _ref(producer={'session_id': 'session_0001'})
    assert _publish(platform, _generated(tmp_path)) == platform.artifact


def _revision_platform(
    tmp_path: Path, artifact: PrivateArtifactRef
) -> tuple[Platform, GeneratedFile]:
    base = _ref(revision=2, supersedes_artifact_id='first_artifact')
    platform = Platform()

    def prepare(payload: dict[str, Any]) -> dict[str, Any]:
        work = _work_payload(payload)
        work['input_handles'] = [
            {
                'local_id': 'source',
                'role': 'source_artifact',
                'record_handle': base.logical_output_id,
                'generation_handle': base.artifact_id,
            }
        ]
        return work

    platform.prepare = prepare
    platform.artifact = artifact
    return platform, _generated(tmp_path, private_expected_base=base.model_dump(mode='json'))


def test_revision_supersedes_its_expected_base(tmp_path: Path) -> None:
    """A revision of a private base must name the base and increase its revision."""
    artifact = _ref(
        artifact_id='private_artifact_0002',
        revision=3,
        supersedes_artifact_id='private_artifact_0001',
    )
    platform, generated = _revision_platform(tmp_path, artifact)
    assert _publish(platform, generated) == artifact
    expected = platform.data[0][1]['file_intake']['expected_base_ref']
    assert expected['artifact_id'] == 'private_artifact_0001'


@pytest.mark.parametrize(
    'artifact',
    [
        _ref(artifact_id='a2', revision=2, supersedes_artifact_id='private_artifact_0001'),
        _ref(artifact_id='a2', revision=3, supersedes_artifact_id='other_base'),
        _ref(
            artifact_id='a2',
            revision=3,
            supersedes_artifact_id='private_artifact_0001',
            logical_output_id='other_output',
        ),
    ],
)
def test_revision_must_not_regress_or_fork(tmp_path: Path, artifact: PrivateArtifactRef) -> None:
    """A revision that does not advance, supersedes another base or forks the output refuses."""
    platform, generated = _revision_platform(tmp_path, artifact)
    with pytest.raises(PrivateArtifactError):
        _publish(platform, generated)


def _raw_proof_of(platform: Platform, client: Client, proof: TransientVaultClientProof) -> str:
    """Learn a proof's raw value the way Vault does: from the header it is sent in."""
    seen: list[str] = []

    def capture(request: httpx.Request) -> httpx.Response:
        seen.append(request.headers[_PROOF_HEADER])
        return httpx.Response(500)

    platform.replies['retrieve_session'] = capture
    work = _work_payload(
        {'session_id': 'session_0001', 'client_proof_digest': proof.digest, 'file_intake': None}
    )
    work.update(
        {
            'operation': 'retrieve_session',
            'input_handles': [
                {
                    'local_id': 'source_artifact',
                    'record_handle': 'record_0001',
                    'generation_handle': 'generation_0001',
                    'role': 'source_artifact',
                }
            ],
        }
    )
    with pytest.raises(PrivateArtifactError):
        client.private_artifacts.download(VaultWorkRequest.model_validate(work), proof=proof)
    platform.vault.clear()
    platform.vault_bodies.clear()
    platform.data.clear()
    platform.data_bodies.clear()
    return seen[0]


def _ingest(  # noqa: PLR0913 - each knob shapes one committed intake.
    tmp_path: Path,
    *,
    source: bytes = _SOURCE,
    committed_source: bytes | None = None,
    max_source_bytes: int = 1024,
    output: str | None = None,
    platform: Platform | None = None,
    foreign_digest: bool = False,
) -> tuple[Platform, Callable[[], PrivateArtifactRef]]:
    platform = platform or Platform()
    client = platform.client()
    proof = client.private_artifacts.new_retrieval_proof()
    raw = _raw_proof_of(platform, client, proof)
    output_path = tmp_path / 'output.pdf'
    output_path.write_bytes(_OUTPUT)
    source_path = tmp_path / 'source.bin'
    source_path.write_bytes(source)
    work = VaultWorkRequest.model_validate(
        _work_payload(
            {
                'session_id': 'session_0001',
                'client_proof_digest': (
                    client.private_artifacts.new_retrieval_proof().digest
                    if foreign_digest
                    else proof.digest
                ),
                'file_intake': {
                    'kind': 'document',
                    'mime_type': 'application/pdf',
                    'max_source_bytes': max_source_bytes,
                    'output_commitment': vault_file_intake_commitment(
                        raw, 'private_original', _OUTPUT
                    ),
                    'source_commitment': vault_file_intake_commitment(
                        raw,
                        'editable_source',
                        source if committed_source is None else committed_source,
                    ),
                },
            }
        )
    )

    def run() -> PrivateArtifactRef:
        return client.private_artifacts.ingest_file(
            work, proof=proof, output=output or output_path, source=source_path
        )

    return platform, run


def test_ingest_uploads_exact_committed_files(tmp_path: Path) -> None:
    """Explicit work uploads the committed output and source and returns the reference."""
    platform, run = _ingest(tmp_path)
    assert run() == platform.artifact
    assert platform.vault_bodies[4:] == [_OUTPUT, _SOURCE, b'']


def test_ingest_source_at_its_bound_is_accepted(tmp_path: Path) -> None:
    """A source exactly as large as the manifest bound is uploaded."""
    platform, run = _ingest(tmp_path, max_source_bytes=len(_SOURCE))
    assert run() == platform.artifact


@pytest.mark.parametrize(
    ('source', 'committed', 'bound'),
    [
        (b'', b'', 1024),
        (_SOURCE, None, len(_SOURCE) - 1),
        (b'CHANGED-SOURCE', _SOURCE, 1024),
    ],
)
def test_ingest_refuses_empty_oversized_or_changed_sources_offline(
    tmp_path: Path, source: bytes, committed: bytes | None, bound: int
) -> None:
    """Sources that are empty, above the bound or not the committed bytes never upload."""
    platform, run = _ingest(
        tmp_path, source=source, committed_source=committed, max_source_bytes=bound
    )
    with pytest.raises(PrivateArtifactError) as caught:
        run()
    _refused(caught)
    assert platform.vault == []
    assert platform.data == []


def test_ingest_refuses_a_non_regular_output(tmp_path: Path) -> None:
    """A device or other non-regular file is never treated as private output."""
    platform, run = _ingest(tmp_path, output=os.devnull)
    with pytest.raises(PrivateArtifactError):
        run()
    assert platform.vault == []


def test_lost_grant_recovers_ingestion_with_the_proof(tmp_path: Path) -> None:
    """A non-replayable approval recovers ingestion using only the client proof."""
    platform, run = _ingest(tmp_path)
    platform.replies['approve'] = lambda _request: _reply(
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
    assert run() == platform.artifact
    assert platform.vault_operations[:4] == [
        'POST admit',
        'POST approve',
        'POST recover_ingestion',
        'POST ingest_file',
    ]
    recover = platform.vault[2]
    tickets = [payload for path, payload in platform.data if path.endswith('/tickets')]
    assert tickets[2]['operation'] == 'recover_ingestion'
    assert tickets[2]['client_header_digests'] == {
        _PROOF_HEADER: vault_client_proof_header_digest(recover.headers[_PROOF_HEADER])
    }
    assert recover.headers.get('x-maivn-grant-credential') is None


def test_ingest_refuses_a_proof_the_work_did_not_admit(tmp_path: Path) -> None:
    """Files committed under one proof cannot ride on work admitted for another proof."""
    platform, run = _ingest(tmp_path, foreign_digest=True)
    with pytest.raises(PrivateArtifactError) as caught:
        run()
    _refused(caught)
    assert platform.vault == []
    assert platform.data == []
