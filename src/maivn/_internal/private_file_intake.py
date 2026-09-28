"""Bounded direct-to-Vault upload of private generated files and editable sources."""

from __future__ import annotations

import asyncio
import hashlib
import hmac
import os
import stat
from datetime import datetime, timezone
from io import BytesIO
from pathlib import Path
from typing import TYPE_CHECKING, Literal, NoReturn

import httpx
from maivn_contracts.artifacts import (
    CLIENT_PROOF_HEADER,
    PrivateFileIntakePrepareRequest,
    VaultEffectAccepted,
    VaultFileIntakePartSealed,
    VaultFileIntakeSession,
    VaultWorkRequest,
    vault_file_intake_commitment,
    vault_file_intake_hasher,
)
from pydantic import TypeAdapter

from maivn._internal.private_artifacts import (
    PrivateArtifactError,
    bounded_response,
    response_model,
    rfc3339,
    trusted_origin,
)
from maivn._internal.transport.leases import lease_transport

if TYPE_CHECKING:
    from collections.abc import AsyncIterator
    from os import PathLike
    from typing import BinaryIO

    from maivn_contracts.artifacts import GeneratedFile, PrivateArtifactRef

    from maivn._internal.private_artifacts import PrivateArtifactsClient

_CHUNK_BYTES = 65_536
_MAX_FILE_BYTES = 52_428_800
_Part = Literal['private_original', 'editable_source']
_START_RESPONSE: TypeAdapter[VaultFileIntakeSession | VaultEffectAccepted] = TypeAdapter(
    VaultFileIntakeSession | VaultEffectAccepted,
)


async def publish_private_file(  # noqa: PLR0913 - explicit generated-output coordinate.
    client: PrivateArtifactsClient,
    generated: GeneratedFile,
    *,
    source: bytes,
    session_id: str,
    call_id: str,
    output_index: int,
    raw_proof: str,
    proof_digest: str,
) -> PrivateArtifactRef:
    """Prepare one exact server-owned work request without exposing private file metadata."""
    result: PrivateArtifactRef | None = None
    try:
        if type(source) is not bytes or not 0 < len(source) <= _MAX_FILE_BYTES:
            _refuse()
        commitment = _generated_commitment(generated, raw_proof)
        request = PrivateFileIntakePrepareRequest.model_validate(
            {
                'session_id': session_id,
                'call_id': call_id,
                'output_index': output_index,
                'client_proof_digest': proof_digest,
                'file_intake': {
                    'kind': generated.artifact_kind,
                    'mime_type': generated.mime_type,
                    'output_commitment': commitment,
                    'source_commitment': vault_file_intake_commitment(
                        raw_proof, 'editable_source', source
                    ),
                    'max_source_bytes': _MAX_FILE_BYTES,
                    'expected_base_ref': getattr(generated, 'private_expected_base', None),
                },
            }
        )
        payload = await client._data_post(  # noqa: SLF001 # pyright: ignore[reportPrivateUsage]
            '/v1/private-artifacts/ingestions',
            request.model_dump(mode='json', exclude_none=True),
        )
        work = VaultWorkRequest.model_validate(payload)
        if (
            work.operation != 'ingest_file'
            or work.file_intake != request.file_intake
            or work.client_proof_digest != proof_digest
            or work.surface.session_id != session_id
            or work.bounds.max_output_bytes < generated.size_bytes
        ):
            _refuse()
        result = await upload_private_file(
            client,
            work,
            raw_proof=raw_proof,
            output=generated.path,
            source=source,
        )
    except asyncio.CancelledError:
        raise
    except Exception:  # noqa: BLE001 - value-free private SDK boundary.
        result = None
    if result is None:
        raise PrivateArtifactError
    return result


def _generated_commitment(generated: GeneratedFile, proof: str) -> str:
    with Path(generated.path).open('rb') as content:
        descriptor = os.fstat(content.fileno())
        if (
            not stat.S_ISREG(descriptor.st_mode)
            or descriptor.st_size != generated.size_bytes
            or not 0 < descriptor.st_size <= _MAX_FILE_BYTES
        ):
            raise PrivateArtifactError
        digest = vault_file_intake_hasher(proof, 'private_original')
        output_hash = hashlib.sha256()
        total = 0
        while chunk := content.read(_CHUNK_BYTES):
            total += len(chunk)
            if total > generated.size_bytes:
                raise PrivateArtifactError
            digest.update(chunk)
            output_hash.update(chunk)
        if total != generated.size_bytes or output_hash.hexdigest() != generated.sha256:
            raise PrivateArtifactError
        return digest.hexdigest()


async def upload_private_file(
    client: PrivateArtifactsClient,
    work: VaultWorkRequest,
    *,
    raw_proof: str,
    output: str | PathLike[str],
    source: str | PathLike[str] | bytes,
) -> PrivateArtifactRef:
    """Transfer exactly committed parts and return only a verified final Vault reference."""
    result: PrivateArtifactRef | None = None
    try:
        manifest = work.file_intake
        proof_digest = work.client_proof_digest
        if manifest is None or proof_digest is None:
            _refuse()
        with (
            _open_part(
                output,
                raw_proof,
                'private_original',
                manifest.output_commitment,
                work.bounds.max_output_bytes,
            ) as original,
            _open_part(
                source,
                raw_proof,
                'editable_source',
                manifest.source_commitment,
                manifest.max_source_bytes,
            ) as editable,
        ):
            operation_id = await client._authorize(work, file_proof=raw_proof)  # noqa: SLF001 # pyright: ignore[reportPrivateUsage]
            response = await client._ticket_call(  # noqa: SLF001 # pyright: ignore[reportPrivateUsage]
                operation='ingest_file',
                work=work,
                operation_id=operation_id,
                capabilities={CLIENT_PROOF_HEADER: raw_proof},
                capability_digests={CLIENT_PROOF_HEADER: proof_digest},
            )
            accepted = await _complete_upload(
                client,
                work,
                response,
                operation_id=operation_id,
                raw_proof=raw_proof,
                parts=(
                    ('private_original', original, work.bounds.max_output_bytes),
                    ('editable_source', editable, manifest.max_source_bytes),
                ),
            )
            status = await client._bind_and_poll(work, accepted)  # noqa: SLF001 # pyright: ignore[reportPrivateUsage]
            result = _verified_artifact(status.artifact, work, operation_id)
    except asyncio.CancelledError:
        raise
    except Exception:  # noqa: BLE001 - no private paths, bytes or transport diagnostics escape.
        result = None
    if result is None:
        raise PrivateArtifactError
    return result


async def _complete_upload(  # noqa: PLR0913 - exact upload authority and bounded sources.
    client: PrivateArtifactsClient,
    work: VaultWorkRequest,
    response: httpx.Response,
    *,
    operation_id: str,
    raw_proof: str,
    parts: tuple[tuple[str, BinaryIO, int], ...],
) -> VaultEffectAccepted:
    started = _START_RESPONSE.validate_json(response.content)
    if started.operation_id != operation_id or started.server_intent_id in {
        work.intent_id,
        operation_id,
    }:
        raise PrivateArtifactError
    if isinstance(started, VaultEffectAccepted):
        if started.operation != 'ingest_file':
            raise PrivateArtifactError
        # Exact already-queued/committed work returns durable acceptance. Do not
        # allocate or send either private part again; authenticated status settles it.
        return started
    if rfc3339(started.expires_at) <= datetime.now(timezone.utc) or rfc3339(
        started.expires_at
    ) > rfc3339(work.expires_at):
        raise PrivateArtifactError
    origin = trusted_origin(client._vault_origin)  # noqa: SLF001 # pyright: ignore[reportPrivateUsage]
    base = f'{origin}/v1/vault/artifacts/ingestions/{started.upload_id}'
    async with httpx.AsyncClient(
        transport=lease_transport(client._vault_transport),  # noqa: SLF001 # pyright: ignore[reportPrivateUsage]
        timeout=client._timeout_seconds,  # noqa: SLF001 # pyright: ignore[reportPrivateUsage]
        follow_redirects=False,
        trust_env=False,
    ) as direct:
        for part, stream, limit in parts:
            async with direct.stream(
                'PUT',
                f'{base}/parts/{part}',
                headers={
                    CLIENT_PROOF_HEADER: raw_proof,
                    'content-type': 'application/octet-stream',
                },
                content=_chunks(stream, limit),
            ) as raw_response:
                sealed = await bounded_response(raw_response, method='PUT')
                response_model(sealed, VaultFileIntakePartSealed)
        async with direct.stream(
            'POST',
            f'{base}/complete',
            headers={CLIENT_PROOF_HEADER: raw_proof},
            content=b'',
        ) as raw_response:
            completed = await bounded_response(raw_response, method='POST')
    accepted = response_model(completed, VaultEffectAccepted)
    if (
        accepted.operation_id != operation_id
        or accepted.server_intent_id != started.server_intent_id
        or accepted.operation != 'ingest_file'
    ):
        raise PrivateArtifactError
    return accepted


def _open_part(
    path: str | PathLike[str] | bytes,
    proof: str,
    part: _Part,
    commitment: str,
    limit: int,
) -> BinaryIO:
    stream = BytesIO(path) if isinstance(path, bytes) else Path(path).open('rb')  # noqa: SIM115 - ownership transfers to caller context manager.
    try:
        _verify_part(stream, proof, part, commitment, limit)
    except BaseException:
        stream.close()
        raise
    return stream


def _verified_artifact(
    artifact: PrivateArtifactRef | None,
    work: VaultWorkRequest,
    operation_id: str,
) -> PrivateArtifactRef:
    manifest = work.file_intake
    if (
        manifest is None
        or artifact is None
        or artifact.kind != manifest.kind
        or artifact.mime_type != manifest.mime_type
        or artifact.producer.root_invocation_id != work.surface.root_invocation_id
        or (
            artifact.producer.session_id is not None
            and artifact.producer.session_id != work.surface.session_id
        )
        or artifact.producer.dispatch_id != operation_id
    ):
        raise PrivateArtifactError
    base_ref = manifest.expected_base_ref
    if base_ref is None:
        if artifact.revision != 1 or artifact.supersedes_artifact_id is not None:
            raise PrivateArtifactError
    elif (
        artifact.logical_output_id != base_ref.logical_output_id
        or artifact.revision <= base_ref.revision
        or artifact.supersedes_artifact_id != base_ref.artifact_id
    ):
        raise PrivateArtifactError
    return artifact


def _refuse() -> NoReturn:
    raise PrivateArtifactError


def _verify_part(stream: BinaryIO, proof: str, part: _Part, commitment: str, limit: int) -> None:
    if isinstance(stream, BytesIO):
        expected_size = len(stream.getbuffer())
    else:
        descriptor = os.fstat(stream.fileno())
        if not stat.S_ISREG(descriptor.st_mode):
            raise PrivateArtifactError
        expected_size = descriptor.st_size
    if not 0 < expected_size <= limit:
        raise PrivateArtifactError
    digest = vault_file_intake_hasher(proof, part)
    total = 0
    while chunk := stream.read(_CHUNK_BYTES):
        total += len(chunk)
        if total > limit:
            raise PrivateArtifactError
        digest.update(chunk)
    if total != expected_size or not hmac.compare_digest(digest.hexdigest(), commitment):
        raise PrivateArtifactError
    stream.seek(0)


async def _chunks(stream: BinaryIO, limit: int) -> AsyncIterator[bytes]:
    total = 0
    while chunk := stream.read(_CHUNK_BYTES):
        total += len(chunk)
        if total > limit:
            raise PrivateArtifactError
        yield chunk
