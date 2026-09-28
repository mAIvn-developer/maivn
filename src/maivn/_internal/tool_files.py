"""Admission of contract-declared SDK-local files into ordinary artifact custody."""

from __future__ import annotations

import base64
import binascii
import hashlib
import os
import stat
from pathlib import Path
from typing import TYPE_CHECKING, NoReturn, cast

import httpx
from maivn_contracts.artifacts import (
    GeneratedFile,
    GeneratedFiles,
    ToolFileIntakeAccepted,
    ToolFileIntakeManifest,
    ToolFileSourceDescriptor,
)
from maivn_contracts.tools import (
    ErrorToolOutcome,
    OkToolOutcome,
    ToolOutcomeError,
    ToolOutcomeVariant,
)
from pydantic import ValidationError

from maivn._internal.errors import MaivnHTTPError
from maivn._internal.private_artifacts import PrivateArtifactError
from maivn.files import editable_source_archive

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence
    from typing import BinaryIO

    from maivn_contracts.artifacts import OrdinaryArtifactRef
    from maivn_contracts.tools import ToolCall

    from maivn._internal.models import ToolMetadata
    from maivn._internal.private_artifacts import PrivateArtifactsClient
    from maivn._internal.transport.http import HttpJsonClient

_GENERATED_FILE_SCHEMA_MARKER = 'x-maivn-output-kind'
_GENERATED_FILE_SCHEMA_VALUES = frozenset({'generated_file', 'generated_files'})
_MAX_LOCAL_GENERATED_FILE_BYTES = 52_428_800


async def intake_generated_file_outcome(  # noqa: PLR0913 - exact invocation boundary facts.
    *,
    session_id: str,
    tool_call: ToolCall,
    tool: ToolMetadata | None,
    private_data_keys: Sequence[str],
    outcome: ToolOutcomeVariant,
    http: HttpJsonClient,
    private_publisher: PrivateArtifactsClient | None = None,
    local_sources: tuple[bytes | None, ...] = (),
) -> ToolOutcomeVariant:
    """Admit a declared local file and return a path-free tool outcome."""
    if not isinstance(outcome, OkToolOutcome) or not _declares_generated_file(tool):
        return outcome
    if private_data_keys:
        if private_publisher is not None:
            return await _intake_private_outcome(
                session_id=session_id,
                tool_call=tool_call,
                tool=tool,
                outcome=outcome,
                publisher=private_publisher,
                local_sources=local_sources,
            )
        return _intake_error(
            outcome,
            code='sdk_private_artifact_requires_vault',
            message=(
                'ordinary generated-file intake is unavailable for a tool call that uses '
                'private data; private artifact custody requires the vault'
            ),
        )
    if tool is not None and _declared_file_kind(tool) == 'generated_files':
        return await _intake_batch(
            session_id=session_id,
            tool_call=tool_call,
            tool=tool,
            outcome=outcome,
            http=http,
            private_publisher=private_publisher,
            local_sources=local_sources,
        )
    return await _intake_single(
        session_id=session_id,
        tool_call=tool_call,
        tool=tool,
        outcome=outcome,
        http=http,
        output_index=0,
        private_publisher=private_publisher,
        local_sources=local_sources,
    )


async def _intake_single(  # noqa: PLR0913 - exact file slot and invocation facts.
    *,
    session_id: str,
    tool_call: ToolCall,
    tool: ToolMetadata | None,
    outcome: OkToolOutcome,
    http: HttpJsonClient,
    output_index: int,
    private_publisher: PrivateArtifactsClient | None = None,
    local_sources: tuple[bytes | None, ...] = (),
) -> ToolOutcomeVariant:
    try:
        generated = GeneratedFile.model_validate(outcome.result)
        if generated.custody == 'vault_private' or generated.private_expected_base is not None:
            if private_publisher is None:
                return _intake_error(
                    outcome,
                    code='sdk_private_artifact_requires_vault',
                    message='Private generated files require Vault custody.',
                )
            return await _intake_private_outcome(
                session_id=session_id,
                tool_call=tool_call,
                tool=tool,
                outcome=outcome,
                publisher=private_publisher,
                local_sources=local_sources,
                single_file=generated,
                output_index=output_index,
            )
        authorized_path = _authorized_generated_file_path(tool, generated)
        owner = getattr(tool.target, '__self__', None) if tool is not None else None
        exporter = getattr(owner, 'export_generated_file_source', None)
        source = local_sources[output_index] if output_index < len(local_sources) else None
        descriptor: ToolFileSourceDescriptor | None = None
        if source is None and callable(exporter):
            source_value: object = exporter(generated)
            if not isinstance(source_value, bytes):
                _invalid_file()
            source = source_value
        if source is not None:
            descriptor = ToolFileSourceDescriptor(
                size_bytes=len(source), sha256=hashlib.sha256(source).hexdigest()
            )
        with _open_verified_file(generated, path=authorized_path) as stream:
            manifest = ToolFileIntakeManifest(
                protocol_version=1,
                session_id=session_id,
                call_id=tool_call.call_id,
                output_index=output_index,
                artifact_kind=generated.artifact_kind,
                filename=generated.filename,
                mime_type=generated.mime_type,
                size_bytes=generated.size_bytes,
                sha256=generated.sha256,
                custody='ordinary',
                expected_base=generated.expected_base,
                source_archive=descriptor,
            )
            raw_response = await http.post_framed_file(
                _intake_path(session_id, tool_call.call_id, output_index),
                control=manifest.model_dump(mode='json', exclude_none=True),
                stream=stream,
                size_bytes=generated.size_bytes,
                source=source,
            )
        accepted = ToolFileIntakeAccepted.model_validate(raw_response)
        _verify_receipt(generated, accepted.artifact)
        if accepted.source_archive != descriptor:
            _invalid_file()
        bind_receipt = getattr(owner, 'bind_generated_file_receipt', None)
        if callable(bind_receipt):
            bind_receipt(generated, accepted.artifact)
    except (
        OSError,
        TypeError,
        ValidationError,
        ValueError,
        binascii.Error,
        httpx.HTTPError,
        MaivnHTTPError,
    ):
        return _intake_error(
            outcome,
            code='sdk_generated_file_intake_error',
            message='generated file could not be admitted to ordinary artifact storage',
        )
    sanitized = generated.model_dump(
        mode='json',
        exclude={'path', 'content_base64'},
        exclude_none=True,
    )
    refs = (*tuple(outcome.artifact_refs or ()), accepted.artifact)
    return outcome.model_copy(update={'result': sanitized, 'artifact_refs': refs})


async def _intake_private_outcome(  # noqa: PLR0913 - exact custody and output-slot context.
    *,
    session_id: str,
    tool_call: ToolCall,
    tool: ToolMetadata | None,
    outcome: OkToolOutcome,
    publisher: PrivateArtifactsClient,
    single_file: GeneratedFile | None = None,
    output_index: int = 0,
    local_sources: tuple[bytes | None, ...] = (),
) -> ToolOutcomeVariant:
    refs = list(outcome.artifact_refs or ())
    results: list[dict[str, str]] = []
    try:
        files = (
            (single_file,)
            if single_file is not None
            else (
                GeneratedFiles.model_validate(outcome.result).files
                if tool is not None and _declared_file_kind(tool) == 'generated_files'
                else (GeneratedFile.model_validate(outcome.result),)
            )
        )
        if single_file is not None:
            files = (single_file,)
        for index, generated in enumerate(files, start=output_index):
            if generated.expected_base is not None:
                _invalid_file()
            path = _authorized_generated_file_path(tool, generated)
            owner = getattr(tool.target, '__self__', None) if tool is not None else None
            exporter = getattr(owner, 'export_generated_file_source', None)
            with _open_verified_file(generated, path=path):
                source: object = local_sources[index] if index < len(local_sources) else None
                if source is None:
                    source = (
                        exporter(generated)
                        if callable(exporter)
                        else editable_source_archive(generated)
                    )
            if not isinstance(source, bytes):
                _invalid_file()
            artifact = await publisher.apublish_file(
                output=generated,
                source=source,
                session_id=session_id,
                call_id=tool_call.call_id,
                output_index=index,
            )
            base = generated.private_expected_base
            if (
                artifact.kind != generated.artifact_kind
                or artifact.mime_type != generated.mime_type
                or (
                    base is not None
                    and (
                        artifact.logical_output_id != base.logical_output_id
                        or artifact.revision <= base.revision
                        or artifact.supersedes_artifact_id != base.artifact_id
                    )
                )
            ):
                _invalid_file()
            refs.append(artifact)
            bind = getattr(owner, 'bind_private_generated_file_receipt', None)
            if callable(bind):
                bind(generated, artifact)
            results.append({'kind': 'private_generated_file', 'custody': 'vault_private'})
    except (OSError, TypeError, ValueError, ValidationError, PrivateArtifactError):
        failed = _intake_error(
            outcome,
            code='sdk_private_generated_file_intake_error',
            message='Private generated files could not be saved to the vault.',
        )
        return failed.model_copy(update={'artifact_refs': tuple(refs)})
    return outcome.model_copy(
        update={
            'artifact_refs': tuple(refs),
            'result': results[0]
            if len(results) == 1
            else {'kind': 'private_generated_files', 'files': results},
        }
    )


def _verify_receipt(generated: GeneratedFile, artifact: OrdinaryArtifactRef) -> None:
    base = generated.expected_base
    if base is not None and (
        artifact.logical_output_id != base.logical_output_id
        or artifact.revision != base.revision + 1
        or artifact.supersedes_artifact_id != base.artifact_id
    ):
        _invalid_file()
    if (
        artifact.state != 'available'
        or artifact.sha256 != generated.sha256
        or artifact.size_bytes != generated.size_bytes
        or artifact.mime_type != generated.mime_type
        or artifact.kind != generated.artifact_kind
        or artifact.display_filename != generated.filename
    ):
        _invalid_file()


def _declares_generated_file(tool: ToolMetadata | None) -> bool:
    return _declared_file_kind(tool) in _GENERATED_FILE_SCHEMA_VALUES


def _declared_file_kind(tool: ToolMetadata | None) -> str | None:
    if tool is None or not isinstance(tool.output_schema, dict):
        return None
    schema = cast('Mapping[str, object]', tool.output_schema)
    value = schema.get(_GENERATED_FILE_SCHEMA_MARKER)
    return value if isinstance(value, str) else None


async def _intake_batch(  # noqa: PLR0913 - exact custody and invocation context.
    *,
    session_id: str,
    tool_call: ToolCall,
    tool: ToolMetadata,
    outcome: OkToolOutcome,
    http: HttpJsonClient,
    private_publisher: PrivateArtifactsClient | None = None,
    local_sources: tuple[bytes | None, ...] = (),
) -> ToolOutcomeVariant:
    try:
        batch = GeneratedFiles.model_validate(outcome.result)
    except (ValidationError, TypeError, ValueError):
        return _intake_error(
            outcome,
            code='sdk_generated_file_intake_error',
            message='generated files could not be admitted to artifact storage',
        )
    refs = list(outcome.artifact_refs or ())
    files: list[object] = []
    failed = False
    for index, generated in enumerate(batch.files):
        single = await _intake_single(
            session_id=session_id,
            tool_call=tool_call,
            tool=tool,
            outcome=OkToolOutcome(
                call_id=outcome.call_id,
                duration_ms=outcome.duration_ms,
                status='ok',
                result=generated.model_dump(mode='python'),
            ),
            http=http,
            output_index=index,
            private_publisher=private_publisher,
            local_sources=local_sources,
        )
        refs.extend(single.artifact_refs or ())
        if isinstance(single, OkToolOutcome):
            files.append(single.result)
        else:
            failed = True
    if failed:
        return ErrorToolOutcome(
            call_id=outcome.call_id,
            duration_ms=outcome.duration_ms,
            artifact_refs=tuple(refs),
            status='error',
            error=ToolOutcomeError(
                code='sdk_generated_file_batch_incomplete',
                message=(
                    'Some files could not be saved. Saved files remain attached; '
                    'retry the exact batch.'
                ),
                retryable=True,
            ),
        )
    return outcome.model_copy(
        update={
            'result': {'kind': 'generated_files', 'files': files},
            'artifact_refs': tuple(refs),
        }
    )


def _authorized_generated_file_path(tool: ToolMetadata | None, generated: GeneratedFile) -> Path:
    if tool is None:
        _invalid_file()
    owner = getattr(tool.target, '__self__', None)
    roots_provider = getattr(owner, 'authorized_generated_file_roots', None)
    if not callable(roots_provider):
        _invalid_file()
    roots_value: object = roots_provider()
    if not isinstance(roots_value, tuple):
        _invalid_file()
    roots = cast('tuple[object, ...]', roots_value)
    if not roots:
        _invalid_file()
    candidate = Path(generated.path).resolve(strict=True)
    for raw_root in roots:
        if not isinstance(raw_root, (str, os.PathLike)):
            continue
        try:
            root = Path(cast('str | os.PathLike[str]', raw_root)).expanduser().resolve(strict=True)
        except (OSError, TypeError, ValueError):
            continue
        if root.is_dir() and candidate.is_relative_to(root):
            return candidate
    return _invalid_file()


def _open_verified_file(generated: GeneratedFile, *, path: Path) -> BinaryIO:
    if generated.size_bytes > _MAX_LOCAL_GENERATED_FILE_BYTES or path.is_symlink():
        _invalid_file()
    stream = path.open('rb')
    try:
        _verify_open_stream(stream, generated)
    except Exception:
        stream.close()
        raise
    stream.seek(0)
    return stream


def _verify_open_stream(stream: BinaryIO, generated: GeneratedFile) -> None:
    descriptor = os.fstat(stream.fileno())
    if not stat.S_ISREG(descriptor.st_mode) or descriptor.st_size != generated.size_bytes:
        _invalid_file()
    digest = _sha256_stream(stream)
    if digest != generated.sha256:
        _invalid_file()
    if generated.content_base64 is not None:
        decoded = base64.b64decode(generated.content_base64, validate=True)
        if len(decoded) != descriptor.st_size or hashlib.sha256(decoded).hexdigest() != digest:
            _invalid_file()


def _sha256_stream(stream: BinaryIO) -> str:
    digest = hashlib.sha256()
    while chunk := stream.read(64 * 1024):
        digest.update(chunk)
    return digest.hexdigest()


def _intake_error(
    outcome: OkToolOutcome,
    *,
    code: str,
    message: str,
) -> ErrorToolOutcome:
    return ErrorToolOutcome(
        call_id=outcome.call_id,
        duration_ms=outcome.duration_ms,
        status='error',
        error=ToolOutcomeError(code=code, message=message, retryable=False),
    )


def _intake_path(session_id: str, call_id: str, output_index: int) -> str:
    return f'/v1/sessions/{session_id}/tools/{call_id}/artifacts/{output_index}'


__all__ = ['intake_generated_file_outcome']


def _invalid_file() -> NoReturn:
    raise ValueError
