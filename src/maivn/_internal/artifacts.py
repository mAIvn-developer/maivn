"""Typed SDK surface for ordinary returnable-artifact retrieval."""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Literal, NoReturn, TypeAlias, cast
from urllib.parse import quote, unquote_to_bytes

from maivn_contracts.artifacts import (
    ArtifactKind,
    OrdinaryArtifactRef,
    OrdinaryArtifactState,
    ToolFileSourceRequest,
)
from pydantic import BaseModel, ConfigDict, Field, StrictInt, StrictStr

from maivn._internal.api.async_stream import run_blocking
from maivn._internal.wire import (
    ARTIFACT_DOWNLOAD_PATH,
    ARTIFACT_PATH,
    ARTIFACT_PREVIEW_PATH,
    ARTIFACTS_PATH,
)

if TYPE_CHECKING:
    from collections.abc import Callable, Mapping
    from os import PathLike

    from maivn._internal.transport.http import HttpJsonClient

ArtifactListState: TypeAlias = OrdinaryArtifactState | Literal['all']
_CONTROL_CHARACTER_CEILING = 32
_DELETE_CODEPOINT = 127
_SHA256_HEX_LENGTH = 64
_MAX_SOURCE_BYTES = 52_428_800
_PREVIEW_CONTENT_TYPES = frozenset({'application/pdf', 'image/jpeg', 'image/png'})


class ArtifactMetadata(BaseModel):
    """Custody-safe ordinary artifact metadata returned by public read routes."""

    model_config = ConfigDict(extra='forbid', frozen=True)

    artifact_id: StrictStr = Field(min_length=1)
    revision: StrictInt = Field(ge=1)
    state: OrdinaryArtifactState
    invocation_id: StrictStr = Field(min_length=1)
    logical_output_id: StrictStr = Field(min_length=1)
    kind: ArtifactKind
    mime_type: StrictStr = Field(min_length=1)
    display_filename: StrictStr = Field(min_length=1, max_length=255)
    byte_size: StrictInt = Field(ge=1)
    sha256: StrictStr = Field(pattern=r'^[0-9a-f]{64}$')
    created_at: StrictStr = Field(min_length=1)
    expires_at: StrictStr = Field(min_length=1)


class ArtifactListPage(BaseModel):
    """One stable project-scoped page of ordinary artifacts."""

    model_config = ConfigDict(extra='forbid', frozen=True)

    artifacts: tuple[ArtifactMetadata, ...] = ()
    next_cursor: StrictStr | None = Field(default=None, min_length=1)


@dataclass(frozen=True, slots=True)
class ArtifactDownload:
    """Downloaded artifact bytes plus allowlisted response metadata."""

    filename: str
    mime_type: str
    size_bytes: int
    sha256: str
    content: bytes = field(repr=False)


@dataclass(frozen=True, slots=True)
class ArtifactPreview:
    """Verified transient preview bytes for one exact source revision."""

    content: bytes = field(repr=False)
    content_type: str
    filename: str
    sha256: str
    source_revision_id: int


class ArtifactsClient:
    """Root facade for ordinary artifact list, metadata, and byte retrieval."""

    def __init__(self, http: Callable[[], HttpJsonClient]) -> None:
        """Bind the facade to the parent client's authenticated HTTP accessor."""
        self._get_http = http

    def download_source(self, artifact: OrdinaryArtifactRef, *, session_id: str) -> bytes:
        """Download a selected revision's portable editable source for toolset resumption."""
        return run_blocking(lambda: self.adownload_source(artifact, session_id=session_id))

    async def adownload_source(self, artifact: OrdinaryArtifactRef, *, session_id: str) -> bytes:
        """Read a caller-owned source through the same conversation and artifact custody."""
        request = ToolFileSourceRequest(artifact_ref=artifact, session_id=session_id)
        response = await self._get_http().post_bytes(
            '/v1/artifacts/source', request.model_dump(mode='json')
        )
        raw = response.content
        if (
            not raw
            or len(raw) > _MAX_SOURCE_BYTES
            or response.headers.get('x-maivn-source-format') != 'maivn-source-archive-v1'
            or response.headers.get('x-maivn-source-sha256') != hashlib.sha256(raw).hexdigest()
        ):
            _invalid_download()
        package: object = json.loads(raw)
        if (
            not isinstance(package, dict)
            or cast('dict[str, object]', package).get('output_sha256') != artifact.sha256
        ):
            _invalid_download()
        return raw

    def list(
        self,
        *,
        invocation_id: str | None = None,
        state: ArtifactListState = 'available',
        revision: int | None = None,
        cursor: str | None = None,
        limit: int = 50,
    ) -> ArtifactListPage:
        """List ordinary artifacts visible to the authenticated project."""
        return run_blocking(
            lambda: self.alist(
                invocation_id=invocation_id,
                state=state,
                revision=revision,
                cursor=cursor,
                limit=limit,
            )
        )

    async def alist(
        self,
        *,
        invocation_id: str | None = None,
        state: ArtifactListState = 'available',
        revision: int | None = None,
        cursor: str | None = None,
        limit: int = 50,
    ) -> ArtifactListPage:
        """List ordinary artifacts asynchronously."""
        params: dict[str, str | int] = {'state': state, 'limit': limit}
        if invocation_id is not None:
            params['invocation_id'] = invocation_id
        if revision is not None:
            params['revision'] = revision
        if cursor is not None:
            params['cursor'] = cursor
        return ArtifactListPage.model_validate(
            await self._get_http().get(ARTIFACTS_PATH, params=params)
        )

    def get(self, artifact_id: str, *, revision: int | None = None) -> ArtifactMetadata:
        """Fetch one exact visible artifact revision's metadata."""
        return run_blocking(lambda: self.aget(artifact_id, revision=revision))

    async def aget(
        self,
        artifact_id: str,
        *,
        revision: int | None = None,
    ) -> ArtifactMetadata:
        """Fetch artifact metadata asynchronously."""
        return ArtifactMetadata.model_validate(
            await self._get_http().get(
                ARTIFACT_PATH.format(artifact_id=artifact_id),
                params=_revision_params(revision),
            )
        )

    def download(
        self,
        artifact_id: str | OrdinaryArtifactRef,
        *,
        revision: int | None = None,
    ) -> ArtifactDownload:
        """Download by ID or verify bytes against an exact message attachment ref."""
        return run_blocking(lambda: self.adownload(artifact_id, revision=revision))

    async def adownload(
        self,
        artifact_id: str | OrdinaryArtifactRef,
        *,
        revision: int | None = None,
    ) -> ArtifactDownload:
        """Download exact artifact bytes asynchronously without retaining a URL."""
        ref = artifact_id if isinstance(artifact_id, OrdinaryArtifactRef) else None
        if ref is not None:
            ref = OrdinaryArtifactRef.model_validate(ref.model_dump(mode='json'))
            if revision is not None and revision != ref.revision:
                message = 'artifact reference revision cannot be overridden'
                raise ValueError(message)
            artifact_id, revision = ref.artifact_id, ref.revision
        if not isinstance(artifact_id, str) or not artifact_id:
            message = 'artifact download requires an ordinary reference or non-empty artifact id'
            raise ValueError(message)
        response = await self._get_http().get_bytes(
            ARTIFACT_DOWNLOAD_PATH.format(artifact_id=quote(artifact_id, safe='')),
            params=_revision_params(revision),
        )
        content = response.content
        headers = response.headers
        declared_length = headers.get('content-length')
        if declared_length is not None:
            try:
                expected_length = int(declared_length)
            except ValueError:
                _invalid_download()
            if expected_length != len(content):
                _invalid_download()
        mime_type = headers.get('content-type', '').partition(';')[0].strip()
        filename = _download_filename(headers)
        if not content or not mime_type or filename is None:
            _invalid_download()
        download = ArtifactDownload(
            filename=filename,
            mime_type=mime_type,
            size_bytes=len(content),
            sha256=hashlib.sha256(content).hexdigest(),
            content=content,
        )
        if ref is not None and (
            download.sha256 != ref.sha256
            or download.size_bytes != ref.size_bytes
            or download.mime_type != ref.mime_type
        ):
            _invalid_download()
        return download

    def download_to(
        self,
        artifact_id: str | OrdinaryArtifactRef,
        path: str | PathLike[str],
        *,
        revision: int | None = None,
    ) -> ArtifactDownload:
        """Verify downloaded bytes before atomically replacing a local destination."""
        download = self.download(artifact_id, revision=revision)
        _atomic_write(path, download.content)
        return download

    async def adownload_to(
        self,
        artifact_id: str | OrdinaryArtifactRef,
        path: str | PathLike[str],
        *,
        revision: int | None = None,
    ) -> ArtifactDownload:
        """Verify and atomically save an attachment without blocking the event loop."""
        download = await self.adownload(artifact_id, revision=revision)
        await asyncio.to_thread(_atomic_write, path, download.content)
        return download

    def preview(self, artifact_id: str, *, revision: int) -> ArtifactPreview:
        """Fetch and verify transient preview bytes for one exact revision."""
        return run_blocking(lambda: self.apreview(artifact_id, revision=revision))

    async def apreview(self, artifact_id: str, *, revision: int) -> ArtifactPreview:
        """Fetch and verify transient preview bytes asynchronously."""
        response = await self._get_http().get_bytes(_preview_path(artifact_id, revision))
        return _validated_preview(response.content, response.headers, revision=revision)

    def preview_to(
        self,
        artifact_id: str,
        path: str | PathLike[str],
        *,
        revision: int,
    ) -> ArtifactPreview:
        """Verify a preview and atomically replace ``path`` with its bytes."""
        preview = self.preview(artifact_id, revision=revision)
        _atomic_write(path, preview.content)
        return preview

    async def apreview_to(
        self,
        artifact_id: str,
        path: str | PathLike[str],
        *,
        revision: int,
    ) -> ArtifactPreview:
        """Verify and atomically write a preview without blocking the event loop."""
        preview = await self.apreview(artifact_id, revision=revision)
        await asyncio.to_thread(_atomic_write, path, preview.content)
        return preview


def _revision_params(revision: int | None) -> dict[str, int] | None:
    return None if revision is None else {'revision': revision}


def _preview_path(artifact_id: str, revision: int) -> str:
    if not artifact_id or isinstance(revision, bool) or revision < 1:
        message = 'artifact preview requires a non-empty artifact id and positive revision'
        raise ValueError(message)
    return ARTIFACT_PREVIEW_PATH.format(
        artifact_id=quote(artifact_id, safe=''),
        revision=revision,
    )


def _validated_preview(
    content: bytes,
    headers: Mapping[str, str],
    *,
    revision: int,
) -> ArtifactPreview:
    declared_length = headers.get('content-length')
    try:
        expected_length = int(declared_length) if declared_length is not None else None
        source_revision = int(headers.get('x-artifact-source-revision', ''))
    except ValueError:
        _invalid_preview()
    content_type = headers.get('content-type', '').partition(';')[0].strip().lower()
    filename = _preview_filename(headers)
    declared_digest = headers.get('x-artifact-sha256', '')
    computed_digest = hashlib.sha256(content).hexdigest()
    cache_control = {
        directive.strip().lower()
        for directive in headers.get('cache-control', '').split(',')
        if directive.strip()
    }
    if (
        not content
        or expected_length != len(content)
        or content_type not in _PREVIEW_CONTENT_TYPES
        or filename is None
        or not _valid_sha256(declared_digest)
        or declared_digest != computed_digest
        or source_revision != revision
        or not {'private', 'no-store'}.issubset(cache_control)
        or headers.get('x-content-type-options', '').lower() != 'nosniff'
    ):
        _invalid_preview()
    return ArtifactPreview(
        content=content,
        content_type=content_type,
        filename=filename,
        sha256=declared_digest,
        source_revision_id=source_revision,
    )


def _download_filename(headers: Mapping[str, str]) -> str | None:
    return _response_filename(headers)


def _preview_filename(headers: Mapping[str, str]) -> str | None:
    disposition = headers.get('content-disposition', '')
    if disposition.partition(';')[0].strip().lower() != 'inline':
        return None
    return _response_filename(headers)


def _response_filename(headers: Mapping[str, str]) -> str | None:
    disposition = headers.get('content-disposition', '')
    parts = [part.strip() for part in disposition.split(';')]
    extended = next((part for part in parts if part.lower().startswith('filename*=')), None)
    fallback = next((part for part in parts if part.lower().startswith('filename=')), None)
    try:
        if extended is not None:
            encoded = extended.split('=', 1)[1]
            if not encoded.startswith("UTF-8''"):
                return None
            filename = unquote_to_bytes(encoded.removeprefix("UTF-8''")).decode('utf-8')
        elif fallback is not None:
            filename = fallback.split('=', 1)[1].strip('"')
        else:
            return None
    except (UnicodeDecodeError, ValueError):
        return None
    if (
        not filename
        or filename != filename.strip()
        or filename in {'.', '..'}
        or '/' in filename
        or '\\' in filename
        or any(
            ord(character) < _CONTROL_CHARACTER_CEILING or ord(character) == _DELETE_CODEPOINT
            for character in filename
        )
    ):
        return None
    return filename


def _valid_sha256(value: str) -> bool:
    return len(value) == _SHA256_HEX_LENGTH and all(
        character in '0123456789abcdef' for character in value
    )


def _atomic_write(path: str | PathLike[str], content: bytes) -> None:
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
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        temporary_path.replace(destination)
        temporary_path = None
    finally:
        if temporary_path is not None:
            temporary_path.unlink(missing_ok=True)


def _invalid_download() -> NoReturn:
    message = 'artifact download response was invalid'
    raise ValueError(message)


def _invalid_preview() -> NoReturn:
    message = 'artifact preview response was invalid'
    raise ValueError(message)


__all__ = [
    'ArtifactDownload',
    'ArtifactListPage',
    'ArtifactListState',
    'ArtifactMetadata',
    'ArtifactPreview',
    'ArtifactsClient',
]
