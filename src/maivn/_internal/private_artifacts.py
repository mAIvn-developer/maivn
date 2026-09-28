"""Ticket-driven SDK boundary for private returnable artifacts."""

from __future__ import annotations

import asyncio
import base64
import hmac
import os
import re
import secrets
import tempfile
import time
from contextlib import suppress
from contextvars import copy_context
from datetime import datetime, timezone
from pathlib import Path
from queue import Full, Queue
from threading import Event, Thread
from typing import TYPE_CHECKING, Any, Literal, NoReturn, SupportsIndex, TypeVar, cast
from urllib.parse import urlsplit

import httpx
from maivn_contracts.artifacts import (
    CLIENT_PROOF_HEADER,
    GRANT_CREDENTIAL_HEADER,
    RETRIEVAL_SESSION_CREDENTIAL_HEADER,
    SENDER_ASSERTION_HEADER,
    SENDER_SIGNATURE_HEADER,
    PrivateArtifactRef,
    PrivateArtifactResolveRequest,
    PrivateWorkIntentStatus,
    RedactedExportReceipt,
    VaultActionTicket,
    VaultEffectAccepted,
    VaultGrantCredentialDelivery,
    VaultIntentStatusRequest,
    VaultRetrievalSessionCredentialDelivery,
    VaultWorkRequest,
    validate_vault_effect_status_binding,
    vault_client_proof_header_digest,
    vault_grant_header_digest,
    vault_retrieval_session_credential_digest,
)
from pydantic import BaseModel, ConfigDict, StrictStr

from maivn._internal.api.async_stream import run_blocking
from maivn._internal.transport.leases import lease_transport

if TYPE_CHECKING:
    from collections.abc import AsyncGenerator, Callable, Iterator, Mapping
    from os import PathLike

    from maivn_contracts.artifacts import GeneratedFile

    from maivn._internal.transport.http import HttpJsonClient

_PRIVATE_ARTIFACT_REFUSED = 'private_artifact_request_refused'
_PROOF_FACTORY = object()
_DEFAULT_CHUNK_BYTES = 65_536
_DATA_TICKET_PATH = '/v1/private-artifacts/tickets'
_DATA_RESOLVE_PATH = '/v1/private-artifacts/resolve'
_DATA_BIND_STATUS_PATH = '/v1/private-artifacts/intents/bind-status'
_DATA_STATUS_PATH = '/v1/private-artifacts/intents/status'
_POLL_INITIAL_SECONDS = 0.05
_POLL_MAX_SECONDS = 1.0
_MAX_FILE_PROOFS = 1024
_FILE_PROOF_TTL_SECONDS = 1800
_MAX_RANGE_CHUNK_BYTES = 65_536
_MAX_CONTROL_RESPONSE_BYTES = 16_384
_MAX_RANGE_OFFSET = (2**63) - 1
_LOOPBACK_HOSTS = frozenset({'127.0.0.1', 'localhost', '::1'})
_HTTP_SUCCESS_MIN = 200
_HTTP_REDIRECT_MIN = 300
_HTTP_PARTIAL_CONTENT = 206
_CONTENT_RANGE = re.compile(r'^bytes (0|[1-9][0-9]*)-(0|[1-9][0-9]*)/(0|[1-9][0-9]*)$')
_PRIVATE_MIME_TYPES = frozenset(
    {
        'application/octet-stream',
        'application/pdf',
        'application/vnd.openxmlformats-officedocument.presentationml.presentation',
        'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
        'application/vnd.openxmlformats-officedocument.wordprocessingml.document',
        'image/jpeg',
        'image/png',
    }
)
_RAW_CAPABILITY_HEADERS = frozenset(
    {
        CLIENT_PROOF_HEADER,
        GRANT_CREDENTIAL_HEADER,
        RETRIEVAL_SESSION_CREDENTIAL_HEADER,
    },
)
_ModelT = TypeVar('_ModelT', bound=BaseModel)


class _WorkIntentAdmitted(BaseModel):
    model_config = ConfigDict(extra='forbid', frozen=True)

    intent_id: StrictStr
    state: StrictStr
    replayed: bool


class _WorkOperationRedeemed(BaseModel):
    model_config = ConfigDict(extra='forbid', frozen=True)

    operation_id: StrictStr
    replayed: bool


class PrivateArtifactError(Exception):
    """Fixed, value-free private-artifact client failure."""

    def __init__(self, code: str = _PRIVATE_ARTIFACT_REFUSED) -> None:
        self.code = code
        super().__init__(code)


class PrivateArtifactAuthorizationDeniedError(PrivateArtifactError):
    """The admitted private-artifact operation was durably denied."""

    def __init__(self) -> None:
        super().__init__('private_artifact_authorization_denied')


class PrivateArtifactGrantExpiredError(PrivateArtifactError):
    """The admitted private-artifact grant expired before completion."""

    def __init__(self) -> None:
        super().__init__('private_artifact_grant_expired')


class PrivateArtifactEffectFailedError(PrivateArtifactError):
    """The private-artifact effect reached a durable terminal failure."""

    def __init__(self) -> None:
        super().__init__('private_artifact_effect_failed')


class PrivateArtifactTimeoutError(PrivateArtifactError):
    """The bounded private-artifact status wait expired."""

    def __init__(self) -> None:
        super().__init__('private_artifact_timeout')


_TERMINAL_FAILURE_ERRORS: dict[str, type[PrivateArtifactError]] = {
    'authorization_denied': PrivateArtifactAuthorizationDeniedError,
    'grant_expired': PrivateArtifactGrantExpiredError,
    'failed': PrivateArtifactEffectFailedError,
}


class TransientVaultClientProof:
    """Opaque, process-local proof used only for one retrieval workflow."""

    __slots__ = ('__raw', '_digest')

    def __init__(self, raw: str, *, _factory: object) -> None:
        if _factory is not _PROOF_FACTORY:
            raise TypeError(_PRIVATE_ARTIFACT_REFUSED)
        object.__setattr__(self, '_TransientVaultClientProof__raw', raw)
        object.__setattr__(self, '_digest', vault_client_proof_header_digest(raw))

    @property
    def digest(self) -> str:
        """Return only the domain-separated digest admitted in the work request."""
        return self._digest

    def __repr__(self) -> str:
        return 'TransientVaultClientProof(<redacted>)'

    __str__ = __repr__

    def __copy__(self) -> NoReturn:
        raise TypeError(_PRIVATE_ARTIFACT_REFUSED)

    def __deepcopy__(self, _memo: object) -> NoReturn:
        raise TypeError(_PRIVATE_ARTIFACT_REFUSED)

    def __reduce__(self) -> NoReturn:
        raise TypeError(_PRIVATE_ARTIFACT_REFUSED)

    def __reduce_ex__(self, _protocol: SupportsIndex) -> NoReturn:
        raise TypeError(_PRIVATE_ARTIFACT_REFUSED)


class PrivateArtifactsClient:
    """Public private-artifact API backed by Data-issued Vault action tickets."""

    def __init__(
        self,
        data_http_factory: Callable[[], HttpJsonClient],
        *,
        vault_origin: str | None,
        vault_transport: httpx.AsyncBaseTransport | None,
        timeout_seconds: float,
    ) -> None:
        self._data_http_factory = data_http_factory
        self._vault_origin = vault_origin
        self._vault_transport = vault_transport
        self._timeout_seconds = timeout_seconds
        self._file_proofs: dict[tuple[str, str, int], tuple[float, TransientVaultClientProof]] = {}

    def new_retrieval_proof(self) -> TransientVaultClientProof:
        """Generate a transient proof whose raw value never leaves this client."""
        raw = base64.urlsafe_b64encode(secrets.token_bytes(32)).rstrip(b'=').decode('ascii')
        return TransientVaultClientProof(raw, _factory=_PROOF_FACTORY)

    def ingest_file(
        self,
        work: VaultWorkRequest,
        *,
        proof: TransientVaultClientProof,
        output: str | PathLike[str],
        source: str | PathLike[str],
    ) -> PrivateArtifactRef:
        """Store exact private generated output and editable source directly in Vault."""
        return run_blocking(
            lambda: self.aingest_file(work, proof=proof, output=output, source=source)
        )

    def publish_file(
        self,
        output: GeneratedFile,
        *,
        source: bytes,
        session_id: str,
        call_id: str,
        output_index: int = 0,
    ) -> PrivateArtifactRef:
        """Prepare and privately publish an SDK-generated file with its portable source."""
        return run_blocking(
            lambda: self.apublish_file(
                output,
                source=source,
                session_id=session_id,
                call_id=call_id,
                output_index=output_index,
            )
        )

    async def apublish_file(
        self,
        output: GeneratedFile,
        *,
        source: bytes,
        session_id: str,
        call_id: str,
        output_index: int = 0,
    ) -> PrivateArtifactRef:
        """Keep private file metadata and proof inside the SDK/Vault boundary."""
        from maivn._internal.private_file_intake import (  # noqa: PLC0415 - custody-module cycle.
            publish_private_file,
        )

        proof = self._file_upload_proof(session_id, call_id, output_index)
        return await publish_private_file(
            self,
            output,
            source=source,
            session_id=session_id,
            call_id=call_id,
            output_index=output_index,
            raw_proof=_proof_raw(proof),
            proof_digest=proof.digest,
        )

    def _file_upload_proof(
        self,
        session_id: str,
        call_id: str,
        output_index: int,
    ) -> TransientVaultClientProof:
        """Retain a bounded transient proof so exact publication retries remain identical."""
        now = time.monotonic()
        self._file_proofs = {key: item for key, item in self._file_proofs.items() if item[0] > now}
        key = (session_id, call_id, output_index)
        existing = self._file_proofs.get(key)
        if existing is not None:
            return existing[1]
        if len(self._file_proofs) >= _MAX_FILE_PROOFS:
            raise PrivateArtifactError
        proof = self.new_retrieval_proof()
        return self._file_proofs.setdefault(key, (now + _FILE_PROOF_TTL_SECONDS, proof))[1]

    async def aingest_file(
        self,
        work: VaultWorkRequest,
        *,
        proof: TransientVaultClientProof,
        output: str | PathLike[str],
        source: str | PathLike[str],
    ) -> PrivateArtifactRef:
        """Upload the two proof-committed private parts and await their immutable reference."""
        from maivn._internal.private_file_intake import (  # noqa: PLC0415 - breaks custody-module cycle.
            upload_private_file,
        )

        normalized = _strict_work(work, expected_operation='ingest_file')
        raw_proof = _proof_raw(proof)
        if normalized.client_proof_digest != proof.digest:
            raise PrivateArtifactError
        return await upload_private_file(
            self,
            normalized,
            raw_proof=raw_proof,
            output=output,
            source=source,
        )

    def retrieve_stream(
        self,
        work: VaultWorkRequest,
        *,
        proof: TransientVaultClientProof,
        chunk_bytes: int = _DEFAULT_CHUNK_BYTES,
    ) -> Iterator[bytes]:
        """Yield bounded direct-Vault ranges for one authorized retrieval session."""
        return _sync_byte_stream(
            lambda: self.aretrieve_stream(work, proof=proof, chunk_bytes=chunk_bytes),
        )

    def resolve(
        self,
        artifact_ref: PrivateArtifactRef,
        *,
        session_id: str,
        proof: TransientVaultClientProof,
        retrieval_part: Literal['private_original', 'editable_source'] = 'private_original',
    ) -> VaultWorkRequest:
        """Resolve an exact message attachment to server-authorized retrieval work.

        Use ``download(ref, session_id=...)`` to manage the proof automatically.
        Explicit workflows retain their opaque proof for subsequent retrieval.
        """
        return run_blocking(
            lambda: self.aresolve(
                artifact_ref,
                session_id=session_id,
                proof=proof,
                retrieval_part=retrieval_part,
            )
        )

    async def aresolve(
        self,
        artifact_ref: PrivateArtifactRef,
        *,
        session_id: str,
        proof: TransientVaultClientProof,
        retrieval_part: Literal['private_original', 'editable_source'] = 'private_original',
    ) -> VaultWorkRequest:
        """Resolve an exact reference from its original or a later authorized thread session."""
        resolved: VaultWorkRequest | None = None
        try:
            _proof_raw(proof)
            request = PrivateArtifactResolveRequest.model_validate(
                {
                    'artifact_ref': _deep_primitive(artifact_ref),
                    'session_id': session_id,
                    'client_proof_digest': proof.digest,
                    **(
                        {'retrieval_part': retrieval_part}
                        if retrieval_part != 'private_original'
                        else {}
                    ),
                }
            )
            resolved = await self._resolve_attachment(request)
        except asyncio.CancelledError:
            raise
        except Exception:  # noqa: BLE001, S110 - no private transport or model detail escapes.
            pass
        if resolved is None:
            raise PrivateArtifactError
        return resolved

    async def _resolve_attachment(self, request: PrivateArtifactResolveRequest) -> VaultWorkRequest:
        payload = await self._data_post(
            _DATA_RESOLVE_PATH,
            request.model_dump(mode='json', exclude_unset=True),
        )
        work = _strict_work(
            VaultWorkRequest.model_validate(payload), expected_operation=request.operation
        )
        source = work.input_handles[0]
        if (
            source.generation_handle != request.artifact_ref.artifact_id
            or source.record_handle != request.artifact_ref.logical_output_id
            or (
                request.artifact_ref.producer.session_id is not None
                and work.surface.session_id != request.artifact_ref.producer.session_id
            )
            or work.surface.root_invocation_id != request.artifact_ref.producer.root_invocation_id
            or work.client_proof_digest != request.client_proof_digest
            or work.retrieval_part
            != ('editable_source' if request.retrieval_part == 'editable_source' else None)
            or work.export_filename != request.export_filename
            or work.deletion_scope != request.deletion_scope
        ):
            raise PrivateArtifactError
        return work

    def download_source(self, artifact_ref: PrivateArtifactRef, *, session_id: str) -> bytes:
        """Read one exact revision's encrypted editable source for a later SDK edit."""
        return run_blocking(lambda: self.adownload_source(artifact_ref, session_id=session_id))

    async def adownload_source(self, artifact_ref: PrivateArtifactRef, *, session_id: str) -> bytes:
        """Resolve fresh source-specific authority and retrieve source bytes directly from Vault."""
        proof = self.new_retrieval_proof()
        work = await self.aresolve(
            artifact_ref,
            session_id=session_id,
            proof=proof,
            retrieval_part='editable_source',
        )
        return await self.adownload(work, proof=proof)

    def download(
        self,
        work: VaultWorkRequest | PrivateArtifactRef,
        *,
        proof: TransientVaultClientProof | None = None,
        session_id: str | None = None,
        chunk_bytes: int = _DEFAULT_CHUNK_BYTES,
    ) -> bytes:
        """Download an exact message attachment or previously authorized work.

        Pass a private reference and ``session_id`` to resolve fresh retrieval
        work with an internally managed proof. Explicit work calls retain the
        existing ``proof`` argument and must omit ``session_id``.
        """
        return run_blocking(
            lambda: self.adownload(
                work,
                proof=proof,
                session_id=session_id,
                chunk_bytes=chunk_bytes,
            )
        )

    async def adownload(
        self,
        work: VaultWorkRequest | PrivateArtifactRef,
        *,
        proof: TransientVaultClientProof | None = None,
        session_id: str | None = None,
        chunk_bytes: int = _DEFAULT_CHUNK_BYTES,
    ) -> bytes:
        """Collect verified private ranges within the work's admitted byte bound."""
        resolved, selected_proof = await self._download_work(
            work, proof=proof, session_id=session_id
        )
        content = bytearray()
        async for chunk in self.aretrieve_stream(
            resolved,
            proof=selected_proof,
            chunk_bytes=chunk_bytes,
        ):
            content.extend(chunk)
        return bytes(content)

    def download_to(
        self,
        work: VaultWorkRequest | PrivateArtifactRef,
        path: str | PathLike[str],
        *,
        proof: TransientVaultClientProof | None = None,
        session_id: str | None = None,
        chunk_bytes: int = _DEFAULT_CHUNK_BYTES,
    ) -> Path:
        """Stream private bytes to a local file, replacing it only after success.

        The destination is chosen explicitly by the caller. No private filename
        or local path is sent to the API or stored in message history.
        """
        return run_blocking(
            lambda: self.adownload_to(
                work,
                path,
                proof=proof,
                session_id=session_id,
                chunk_bytes=chunk_bytes,
            ),
        )

    async def adownload_to(
        self,
        work: VaultWorkRequest | PrivateArtifactRef,
        path: str | PathLike[str],
        *,
        proof: TransientVaultClientProof | None = None,
        session_id: str | None = None,
        chunk_bytes: int = _DEFAULT_CHUNK_BYTES,
    ) -> Path:
        """Save bounded ranges atomically, cleaning partial bytes on failure or cancellation."""
        resolved, selected_proof = await self._download_work(
            work, proof=proof, session_id=session_id
        )
        destination: Path | None = None
        with suppress(OSError):
            destination = await _save_private_stream(
                path,
                self.aretrieve_stream(resolved, proof=selected_proof, chunk_bytes=chunk_bytes),
            )
        if destination is None:
            raise PrivateArtifactError
        return destination

    async def _download_work(
        self,
        work: VaultWorkRequest | PrivateArtifactRef,
        *,
        proof: TransientVaultClientProof | None,
        session_id: str | None,
    ) -> tuple[VaultWorkRequest, TransientVaultClientProof]:
        if isinstance(work, PrivateArtifactRef):
            if session_id is None or proof is not None:
                raise PrivateArtifactError
            selected_proof = self.new_retrieval_proof()
            resolved = await self.aresolve(work, session_id=session_id, proof=selected_proof)
            return resolved, selected_proof
        if session_id is not None or proof is None:
            raise PrivateArtifactError
        return _strict_work(work, expected_operation='retrieve_session'), proof

    async def aretrieve_stream(
        self,
        work: VaultWorkRequest,
        *,
        proof: TransientVaultClientProof,
        chunk_bytes: int = _DEFAULT_CHUNK_BYTES,
    ) -> AsyncGenerator[bytes, None]:
        """Yield bounded direct-Vault ranges asynchronously."""
        normalized = _strict_work(work, expected_operation='retrieve_session')
        if type(chunk_bytes) is not int or not 1 <= chunk_bytes <= _MAX_RANGE_CHUNK_BYTES:
            raise PrivateArtifactError
        raw_proof = _proof_raw(proof)
        if not hmac.compare_digest(proof.digest, cast('str', normalized.client_proof_digest)):
            raise PrivateArtifactError
        operation_id = await self._authorize(normalized)
        session_response = await self._ticket_call(
            operation='retrieve_session',
            work=normalized,
            operation_id=operation_id,
            capabilities={CLIENT_PROOF_HEADER: raw_proof},
            capability_digests={CLIENT_PROOF_HEADER: proof.digest},
        )
        _require_secret_delivery_headers(session_response)
        delivery = response_model(session_response, VaultRetrievalSessionCredentialDelivery)
        credential = delivery.session_credential
        if (
            delivery.credential_delivery != 'delivered'
            or credential is None
            or rfc3339(delivery.expires_at) <= datetime.now(timezone.utc)
        ):
            raise PrivateArtifactError
        credential_digest = vault_retrieval_session_credential_digest(credential)
        first = 0
        trusted_total: int | None = None
        while trusted_total is None or first < trusted_total:
            requested_last = min(first + chunk_bytes - 1, _MAX_RANGE_OFFSET)
            response = await self._ticket_call(
                operation='read_bytes',
                work=normalized,
                session_id=delivery.session_id,
                first=first,
                last=requested_last,
                capabilities={
                    CLIENT_PROOF_HEADER: raw_proof,
                    RETRIEVAL_SESSION_CREDENTIAL_HEADER: credential,
                },
                capability_digests={
                    CLIENT_PROOF_HEADER: proof.digest,
                    RETRIEVAL_SESSION_CREDENTIAL_HEADER: credential_digest,
                },
            )
            admitted_last, total = _validate_range_response(
                response,
                expected_first=first,
                requested_last=requested_last,
                trusted_total=trusted_total,
                retrieval_part=normalized.retrieval_part,
            )
            if total > normalized.bounds.max_output_bytes:
                raise PrivateArtifactError
            trusted_total = total
            yield response.content
            first = admitted_last + 1

    def export_redacted(
        self,
        work: VaultWorkRequest | PrivateArtifactRef,
        *,
        session_id: str | None = None,
        filename: str | None = None,
    ) -> RedactedExportReceipt:
        """Authorize, queue, and wait for one redacted ordinary export."""
        return run_blocking(
            lambda: self.aexport_redacted(work, session_id=session_id, filename=filename)
        )

    async def aexport_redacted(
        self,
        work: VaultWorkRequest | PrivateArtifactRef,
        *,
        session_id: str | None = None,
        filename: str | None = None,
    ) -> RedactedExportReceipt:
        """Authorize, queue, and wait for one export receipt asynchronously."""
        normalized = await self._attachment_action_work(
            work,
            operation='redacted_export',
            session_id=session_id,
            filename=filename,
        )
        status = await self._effect_status(normalized, expected_operation='redacted_export')
        if (
            status.redacted_export_receipt is None
            or status.redacted_export_receipt.correlation_id != normalized.intent_id
            or status.redacted_export_receipt.ordinary_artifact.display_filename
            != normalized.export_filename
        ):
            raise PrivateArtifactError
        return status.redacted_export_receipt

    def delete(
        self,
        work: VaultWorkRequest | PrivateArtifactRef,
        *,
        session_id: str | None = None,
        include_redacted_exports: bool = False,
    ) -> PrivateWorkIntentStatus:
        """Authorize, queue, and return the first committed delete status."""
        return run_blocking(
            lambda: self.adelete(
                work,
                session_id=session_id,
                include_redacted_exports=include_redacted_exports,
            )
        )

    async def adelete(
        self,
        work: VaultWorkRequest | PrivateArtifactRef,
        *,
        session_id: str | None = None,
        include_redacted_exports: bool = False,
    ) -> PrivateWorkIntentStatus:
        """Authorize and queue deletion without widening its admitted scope."""
        normalized = await self._attachment_action_work(
            work,
            operation='delete',
            session_id=session_id,
            include_redacted_exports=include_redacted_exports,
        )
        status = await self._effect_status(normalized, expected_operation='delete')
        source_records = [
            handle.record_handle
            for handle in normalized.input_handles
            if handle.role == 'source_artifact'
        ]
        if (
            len(source_records) != 1
            or status.lifecycle_receipt is None
            or status.lifecycle_receipt.artifact_id != source_records[0]
            or status.lifecycle_receipt.correlation_id != normalized.intent_id
        ):
            raise PrivateArtifactError
        return status

    async def _attachment_action_work(
        self,
        work: VaultWorkRequest | PrivateArtifactRef,
        *,
        operation: Literal['delete', 'redacted_export'],
        session_id: str | None,
        filename: str | None = None,
        include_redacted_exports: bool = False,
    ) -> VaultWorkRequest:
        resolved: VaultWorkRequest | None = None
        if type(include_redacted_exports) is not bool:
            raise PrivateArtifactError
        try:
            if isinstance(work, PrivateArtifactRef):
                payload: dict[str, Any] = {
                    'artifact_ref': _deep_primitive(work),
                    'session_id': session_id,
                    'operation': operation,
                }
                if operation == 'redacted_export':
                    payload['export_filename'] = filename
                else:
                    payload['deletion_scope'] = {
                        'mode': 'include_redacted_exports'
                        if include_redacted_exports
                        else 'private_only',
                    }
                request = PrivateArtifactResolveRequest.model_validate(payload)
                resolved = await self._resolve_attachment(request)
            elif session_id is None and filename is None and not include_redacted_exports:
                resolved = _strict_work(work, expected_operation=operation)
        except asyncio.CancelledError:
            raise
        except Exception:  # noqa: BLE001 - never retain rejected private request inputs.
            resolved = None
        if resolved is None:
            raise PrivateArtifactError
        return resolved

    async def _effect_status(
        self,
        work: VaultWorkRequest,
        *,
        expected_operation: str,
    ) -> PrivateWorkIntentStatus:
        normalized = _strict_work(work, expected_operation=expected_operation)
        operation_id = await self._authorize(normalized)
        accepted = await self._run_effect(normalized, operation_id=operation_id)
        return await self._bind_and_poll(normalized, accepted)

    def status(self, server_intent_id: str) -> PrivateWorkIntentStatus:
        """Read one authenticated status by its server poll handle."""
        return run_blocking(lambda: self.astatus(server_intent_id))

    async def astatus(self, server_intent_id: str) -> PrivateWorkIntentStatus:
        """Read one fixed-body authenticated status."""
        result: PrivateWorkIntentStatus | None = None
        failed = False
        try:
            request = VaultIntentStatusRequest(
                schema_version='vault-intent-status-request-v1',
                server_intent_id=server_intent_id,
            )
            payload = await self._data_post(
                _DATA_STATUS_PATH,
                request.model_dump(mode='json'),
            )
            candidate = PrivateWorkIntentStatus.model_validate(payload)
            if hmac.compare_digest(candidate.intent_id, request.server_intent_id):
                result = candidate
            else:
                failed = True
        except asyncio.CancelledError:
            raise
        except Exception:  # noqa: BLE001 - collapse every transport/model detail.
            failed = True
        if failed or result is None:
            raise PrivateArtifactError
        return result

    async def _authorize(self, work: VaultWorkRequest, *, file_proof: str | None = None) -> str:
        admitted_response = await self._ticket_call(operation='admit', work=work)
        admitted = response_model(admitted_response, _WorkIntentAdmitted)
        approval_response = await self._ticket_call(
            operation='approve',
            work=work,
            server_intent_id=admitted.intent_id,
        )
        _require_secret_delivery_headers(approval_response)
        delivery = response_model(approval_response, VaultGrantCredentialDelivery)
        credential = delivery.grant_credential
        if delivery.intent_id != admitted.intent_id or rfc3339(delivery.expires_at) <= datetime.now(
            timezone.utc
        ):
            raise PrivateArtifactError
        if (
            delivery.credential_delivery == 'not_replayable'
            and work.operation == 'ingest_file'
            and file_proof is not None
            and work.client_proof_digest is not None
        ):
            recovered = await self._ticket_call(
                operation='recover_ingestion',
                work=work,
                capabilities={CLIENT_PROOF_HEADER: file_proof},
                capability_digests={CLIENT_PROOF_HEADER: work.client_proof_digest},
            )
            return response_model(recovered, _WorkOperationRedeemed).operation_id
        if credential is None:
            raise PrivateArtifactError
        credential_digest = vault_grant_header_digest(credential)
        redemption_response = await self._ticket_call(
            operation='redeem',
            work=work,
            server_intent_id=admitted.intent_id,
            capabilities={GRANT_CREDENTIAL_HEADER: credential},
            capability_digests={GRANT_CREDENTIAL_HEADER: credential_digest},
        )
        redeemed = response_model(redemption_response, _WorkOperationRedeemed)
        return redeemed.operation_id

    async def _run_effect(
        self,
        work: VaultWorkRequest,
        *,
        operation_id: str,
    ) -> VaultEffectAccepted:
        response = await self._ticket_call(
            operation=work.operation,
            work=work,
            operation_id=operation_id,
        )
        accepted = response_model(response, VaultEffectAccepted)
        if accepted.operation_id != operation_id or accepted.operation != work.operation:
            raise PrivateArtifactError
        return accepted

    async def _bind_and_poll(
        self,
        work: VaultWorkRequest,
        accepted: VaultEffectAccepted,
    ) -> PrivateWorkIntentStatus:
        result: PrivateWorkIntentStatus | None = None
        terminal_error: type[PrivateArtifactError] | None = None
        failed = False
        try:
            payload = await self._data_post(
                _DATA_BIND_STATUS_PATH,
                accepted.model_dump(mode='json'),
            )
            current = PrivateWorkIntentStatus.model_validate(payload)
            validate_vault_effect_status_binding(work, accepted, current)
            deadline = time.monotonic() + self._timeout_seconds
            delay = _POLL_INITIAL_SECONDS
            while current.state != 'committed':
                terminal_error = _TERMINAL_FAILURE_ERRORS.get(current.state)
                if terminal_error is None and time.monotonic() >= deadline:
                    terminal_error = PrivateArtifactTimeoutError
                if terminal_error is not None:
                    break
                await asyncio.sleep(_jittered_poll_delay(delay))
                current = await self.astatus(accepted.server_intent_id)
                validate_vault_effect_status_binding(work, accepted, current)
                delay = min(delay * 2, _POLL_MAX_SECONDS)
            if terminal_error is None:
                result = current
        except asyncio.CancelledError:
            raise
        except Exception:  # noqa: BLE001 - the public boundary is intentionally fixed.
            failed = True
        if terminal_error is not None:
            raise terminal_error()
        if failed or result is None:
            raise PrivateArtifactError
        return result

    async def _ticket_call(  # noqa: PLR0913 - one closed ticket request shape.
        self,
        *,
        operation: str,
        work: VaultWorkRequest,
        server_intent_id: str | None = None,
        operation_id: str | None = None,
        session_id: str | None = None,
        first: int | None = None,
        last: int | None = None,
        capabilities: Mapping[str, str] | None = None,
        capability_digests: Mapping[str, str] | None = None,
    ) -> httpx.Response:
        payload: dict[str, Any] = {
            'operation': operation,
            'work': work.model_dump(mode='json'),
            'client_header_digests': dict(capability_digests or {}),
        }
        payload.update(
            {
                name: value
                for name, value in (
                    ('server_intent_id', server_intent_id),
                    ('operation_id', operation_id),
                    ('session_id', session_id),
                    ('first', first),
                    ('last', last),
                )
                if value is not None
            },
        )
        response: httpx.Response | None = None
        failed = False
        try:
            ticket_payload = await self._data_post(_DATA_TICKET_PATH, payload)
            ticket = VaultActionTicket.model_validate(ticket_payload)
            response = await self._direct(ticket, capabilities=dict(capabilities or {}))
        except asyncio.CancelledError:
            raise
        except Exception:  # noqa: BLE001 - tickets and failures are non-disclosing.
            failed = True
        if failed or response is None:
            raise PrivateArtifactError
        return response

    async def _data_post(self, path: str, payload: dict[str, Any]) -> dict[str, Any]:
        result: dict[str, Any] | None = None
        failed = False
        try:
            result = await self._data_http_factory().post(path, payload)
        except asyncio.CancelledError:
            raise
        except Exception:  # noqa: BLE001 - Data errors must not retain work values.
            failed = True
        if failed or result is None:
            raise PrivateArtifactError
        return result

    async def _direct(
        self,
        ticket: VaultActionTicket,
        *,
        capabilities: Mapping[str, str],
    ) -> httpx.Response:
        origin = trusted_origin(self._vault_origin)
        ticket_url = urlsplit(str(ticket.url))
        if f'{ticket_url.scheme}://{ticket_url.netloc}' != origin or rfc3339(
            ticket.expires_at
        ) <= datetime.now(timezone.utc):
            raise PrivateArtifactError
        expected_digests = {
            name: _capability_digest(name, value) for name, value in capabilities.items()
        }
        ticket_digests = cast('Mapping[str, str]', ticket.client_header_digests)
        if set(expected_digests) != set(ticket_digests) or any(
            not hmac.compare_digest(expected_digests[name], ticket_digests[name])
            for name in expected_digests
        ):
            raise PrivateArtifactError
        body = _decode_base64url(ticket.body_base64url)
        headers = {
            # Hosting edges compress whenever the client allows it, and a
            # compressed reply fails bounded_response: ask for the raw bytes.
            'Accept-Encoding': 'identity',
            **ticket.public_headers,
            SENDER_ASSERTION_HEADER: ticket.sender_assertion_base64url,
            SENDER_SIGNATURE_HEADER: ticket.sender_signature_base64url,
            **dict(capabilities),
        }
        response: httpx.Response | None = None
        failed = False
        try:
            async with (
                httpx.AsyncClient(
                    timeout=self._timeout_seconds,
                    transport=lease_transport(self._vault_transport),
                    follow_redirects=False,
                    trust_env=False,
                ) as client,
                client.stream(
                    ticket.method,
                    str(ticket.url),
                    headers=headers,
                    content=body,
                ) as candidate,
            ):
                response = await bounded_response(candidate, method=ticket.method)
        except asyncio.CancelledError:
            raise
        except Exception:  # noqa: BLE001 - network/provider details are value-free outside.
            failed = True
        if failed or response is None:
            raise PrivateArtifactError
        return response


def _proof_raw(proof: object) -> str:
    """Read the secret only inside the SDK module that created it."""
    if not isinstance(proof, TransientVaultClientProof):
        raise PrivateArtifactError
    return object.__getattribute__(proof, '_TransientVaultClientProof__raw')


async def _save_private_stream(
    path: str | PathLike[str],
    stream: AsyncGenerator[bytes, None],
) -> Path:
    destination = Path(path)
    temporary_path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode='wb',
            dir=destination.parent,
            prefix=f'.{destination.name}.',
            suffix='.tmp',
            delete=False,
        ) as handle:
            temporary_path = Path(handle.name)
            async for chunk in stream:
                handle.write(chunk)
            handle.flush()
            os.fsync(handle.fileno())
        temporary_path.replace(destination)
        temporary_path = None
    finally:
        if temporary_path is not None:
            temporary_path.unlink(missing_ok=True)
    return destination


def _strict_work(work: VaultWorkRequest, *, expected_operation: str) -> VaultWorkRequest:
    normalized: VaultWorkRequest | None = None
    try:  # noqa: SIM105 - the later raise must happen outside exception context.
        normalized = VaultWorkRequest.model_validate(_deep_primitive(work))
    except Exception:  # noqa: BLE001, S110 - fixed boundary needs no context.
        pass
    if normalized is None:
        raise PrivateArtifactError
    if normalized.operation != expected_operation:
        raise PrivateArtifactError
    return normalized


def _deep_primitive(value: object) -> object:
    if isinstance(value, BaseModel):
        fields = cast('dict[str, object]', value.__dict__)
        return {name: _deep_primitive(item) for name, item in fields.items()}
    if isinstance(value, dict):
        fields = cast('dict[object, object]', value)
        return {name: _deep_primitive(item) for name, item in fields.items()}
    if isinstance(value, (tuple, list)):
        items = cast('tuple[object, ...] | list[object]', value)
        return [_deep_primitive(item) for item in items]
    return value


def trusted_origin(value: str | None) -> str:
    if value is None:
        raise PrivateArtifactError
    parsed = urlsplit(value)
    # Private bytes travel over TLS. Plain HTTP is accepted only on this
    # machine's loopback, where the local API origin listens.
    if (
        parsed.scheme not in {'https', 'http'}
        or (parsed.scheme == 'http' and parsed.hostname not in _LOOPBACK_HOSTS)
        or not parsed.netloc
        or parsed.username is not None
        or parsed.password is not None
        or parsed.query
        or parsed.fragment
        or parsed.path not in {'', '/'}
    ):
        raise PrivateArtifactError
    return f'{parsed.scheme}://{parsed.netloc}'


def rfc3339(value: str) -> datetime:
    try:
        parsed = datetime.fromisoformat(value[:-1] + '+00:00')
    except (TypeError, ValueError):
        parsed = None
    if parsed is None or parsed.tzinfo is None:
        raise PrivateArtifactError
    return parsed.astimezone(timezone.utc)


def _decode_base64url(value: str | None) -> bytes:
    if value is None:
        return b''
    decoded: bytes | None = None
    try:
        encoded = value.encode('ascii')
        decoded = base64.b64decode(
            encoded + b'=' * (-len(encoded) % 4),
            altchars=b'-_',
            validate=True,
        )
    except Exception:  # noqa: BLE001, S110 - fixed boundary needs no context.
        pass
    if decoded is None or base64.urlsafe_b64encode(decoded).rstrip(b'=').decode('ascii') != value:
        raise PrivateArtifactError
    return decoded


def _capability_digest(name: str, value: str) -> str:
    functions = {
        GRANT_CREDENTIAL_HEADER: vault_grant_header_digest,
        CLIENT_PROOF_HEADER: vault_client_proof_header_digest,
        RETRIEVAL_SESSION_CREDENTIAL_HEADER: vault_retrieval_session_credential_digest,
    }
    function = functions.get(name)
    if function is None:
        raise PrivateArtifactError
    return function(value)


def response_model(response: httpx.Response, model: type[_ModelT]) -> _ModelT:
    result: _ModelT | None = None
    try:  # noqa: SIM105 - the later raise must happen outside exception context.
        result = model.model_validate_json(response.content)
    except Exception:  # noqa: BLE001, S110 - fixed boundary needs no context.
        pass
    if result is None:
        raise PrivateArtifactError
    return result


def _require_secret_delivery_headers(response: httpx.Response) -> None:
    if (
        response.headers.get('cache-control', '').lower() != 'no-store'
        or response.headers.get('x-content-type-options', '').lower() != 'nosniff'
    ):
        raise PrivateArtifactError


async def bounded_response(
    response: httpx.Response,
    *,
    method: str,
) -> httpx.Response:
    """Detach one successful response after a strictly bounded streamed read."""
    result: httpx.Response | None = None
    try:
        limit = _MAX_RANGE_CHUNK_BYTES if method == 'GET' else _MAX_CONTROL_RESPONSE_BYTES
        content_lengths = response.headers.get_list('content-length')
        content_encodings = response.headers.get_list('content-encoding')
        transfer_encodings = response.headers.get_list('transfer-encoding')
        if (
            not _HTTP_SUCCESS_MIN <= response.status_code < _HTTP_REDIRECT_MIN
            or len(content_lengths) != 1
            or content_encodings
            or transfer_encodings
            or response.headers.get('cache-control', '').lower() != 'no-store'
            or response.headers.get('x-content-type-options', '').lower() != 'nosniff'
            or any(header in response.headers for header in _RAW_CAPABILITY_HEADERS)
        ):
            _raise_private_artifact_error()
        declared_text = content_lengths[0]
        if not re.fullmatch(r'0|[1-9][0-9]*', declared_text):
            _raise_private_artifact_error()
        declared = int(declared_text)
        if declared > limit:
            _raise_private_artifact_error()
        chunks: list[bytes] = []
        measured = 0
        async for chunk in response.aiter_bytes():
            measured += len(chunk)
            if measured > declared or measured > limit:
                _raise_private_artifact_error()
            chunks.append(chunk)
        if measured != declared:
            _raise_private_artifact_error()
        result = httpx.Response(
            response.status_code,
            headers=response.headers,
            content=b''.join(chunks),
            request=response.request,
        )
    except asyncio.CancelledError:
        raise
    except Exception:  # noqa: BLE001, S110 - return one fixed failure outside context.
        pass
    if result is None:
        raise PrivateArtifactError
    return result


def _validate_range_response(
    response: httpx.Response,
    *,
    expected_first: int,
    requested_last: int,
    trusted_total: int | None,
    retrieval_part: str | None = None,
) -> tuple[int, int]:
    result: tuple[int, int] | None = None
    try:
        match = _CONTENT_RANGE.fullmatch(response.headers['content-range'])
        if match is None:
            _raise_private_artifact_error()
        first, last, total = (int(value) for value in match.groups())
        content_length = int(response.headers['content-length'])
        valid = (
            response.status_code == _HTTP_PARTIAL_CONTENT
            and response.headers.get('accept-ranges', '').lower() == 'bytes'
            and response.headers.get('cache-control', '').lower() == 'no-store'
            and response.headers.get('content-disposition', '').lower() == 'attachment'
            and response.headers.get('x-content-type-options', '').lower() == 'nosniff'
            and response.headers.get('content-type', '').split(';', 1)[0].lower()
            in (
                {'application/octet-stream'}
                if retrieval_part == 'editable_source'
                else _PRIVATE_MIME_TYPES
            )
            and 'content-encoding' not in response.headers
            and 'transfer-encoding' not in response.headers
            and first == expected_first
            and first <= last <= requested_last
            and 1 <= total <= _MAX_RANGE_OFFSET
            and last < total
            and (trusted_total is None or total == trusted_total)
            and content_length == (last - first) + 1
            and content_length == len(response.content)
            and content_length <= _MAX_RANGE_CHUNK_BYTES
            and response.headers['content-length'] == str(content_length)
        )
        if not valid:
            return _raise_private_artifact_error()
        result = (last, total)
    except Exception:  # noqa: BLE001, S110 - fixed boundary needs no context.
        pass
    if result is None:
        raise PrivateArtifactError
    return result


def _raise_private_artifact_error() -> NoReturn:
    raise PrivateArtifactError


def _jittered_poll_delay(delay: float) -> float:
    jitter_percent = 75 + secrets.randbelow(51)
    return min(delay * jitter_percent / 100, _POLL_MAX_SECONDS)


class _ByteStreamEnd:
    """Private sentinel for the synchronous async-stream bridge."""


def _sync_byte_stream(  # noqa: C901 - cleanup crosses async, thread, and iterator boundaries.
    factory: Callable[[], AsyncGenerator[bytes, None]],
) -> Iterator[bytes]:
    """Bridge one bounded async byte stream without collecting the artifact."""
    queue: Queue[bytes | BaseException | _ByteStreamEnd] = Queue(maxsize=1)
    stop = Event()
    sentinel = _ByteStreamEnd()

    def put_until_stopped(item: bytes | BaseException | _ByteStreamEnd) -> bool:
        while not stop.is_set():
            try:
                queue.put(item, timeout=0.05)
            except Full:
                continue
            return True
        return False

    async def drain() -> None:
        stream = factory()
        try:
            async for chunk in stream:
                if not await asyncio.to_thread(put_until_stopped, chunk):
                    break
        except BaseException as exc:  # noqa: BLE001 - cross the thread boundary intact.
            await asyncio.to_thread(put_until_stopped, exc)
        finally:
            await stream.aclose()
            await asyncio.to_thread(put_until_stopped, sentinel)

    caller_context = copy_context()
    worker = Thread(
        target=lambda: caller_context.run(lambda: run_blocking(drain)),
        name='maivn-private-artifact-stream',
        daemon=True,
    )
    worker.start()
    try:
        while True:
            item = queue.get()
            if isinstance(item, _ByteStreamEnd):
                return
            if isinstance(item, BaseException):
                raise item
            yield item
    finally:
        stop.set()
        worker.join(timeout=5)


__all__ = [
    'PrivateArtifactAuthorizationDeniedError',
    'PrivateArtifactEffectFailedError',
    'PrivateArtifactError',
    'PrivateArtifactGrantExpiredError',
    'PrivateArtifactTimeoutError',
    'PrivateArtifactsClient',
    'TransientVaultClientProof',
]
