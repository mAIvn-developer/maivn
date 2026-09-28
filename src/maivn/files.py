"""Publish files produced by custom SDK toolsets without adding server format adapters."""

from __future__ import annotations

import base64
import hashlib
import json
import re
from contextlib import suppress
from pathlib import Path
from typing import Literal
from uuid import uuid4

from maivn_contracts.artifacts import (
    GeneratedFile,
    GeneratedFiles,
    OrdinaryArtifactRef,
    PrivateArtifactRef,
)
from pydantic import PrivateAttr

_MAX_BYTES = 52_428_800
_IDENTITIES = {
    '.docx': (
        'document',
        'application/vnd.openxmlformats-officedocument.wordprocessingml.document',
    ),
    '.pdf': ('document', 'application/pdf'),
    '.xlsx': ('spreadsheet', 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'),
    '.pptx': (
        'presentation',
        'application/vnd.openxmlformats-officedocument.presentationml.presentation',
    ),
    '.png': ('image', 'image/png'),
    '.jpg': ('image', 'image/jpeg'),
    '.jpeg': ('image', 'image/jpeg'),
}


class _LocalGeneratedFile(GeneratedFile):
    _source_archive: bytes = PrivateAttr(default=b'')

    def bind_source(self, source: bytes) -> None:
        """Retain the local source outside the generated-file JSON contract."""
        self._source_archive = source


def editable_source_archive(
    output: GeneratedFile,
    source: bytes | None = None,
    *,
    source_format: str | None = None,
) -> bytes:
    """Wrap optional custom source bytes; only the developer's versioned adapter edits them."""
    manifest: dict[str, object] = {'editable': False, 'filename': output.filename}
    if source is not None:
        if type(source) is not bytes or not source or len(source) > _MAX_BYTES:
            message = 'Invalid editable source size'
            raise ValueError(message)
        if (
            source_format is None
            or re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9._/-]{0,127}', source_format) is None
        ):
            message = 'A versioned editable source format is required'
            raise ValueError(message)
        manifest = {
            'editable': True,
            'filename': output.filename,
            'source_format': source_format,
            'content_base64': base64.b64encode(source).decode('ascii'),
        }
    elif source_format is not None:
        message = 'Editable source format requires source bytes'
        raise ValueError(message)
    raw = json.dumps(
        {
            'format': 'maivn-source-archive-v1',
            'workspace_type': 'binary',
            'manifest': manifest,
            'output_sha256': output.sha256,
        },
        sort_keys=True,
        separators=(',', ':'),
    ).encode()
    if len(raw) > _MAX_BYTES:
        message = 'Editable source archive exceeds the custody limit'
        raise ValueError(message)
    return raw


def generated_file(  # noqa: PLR0913 - file custody, revision and optional source are explicit.
    path: str | Path,
    *,
    custody: Literal['ordinary', 'vault_private'] = 'ordinary',
    mime_type: str | None = None,
    source: bytes | None = None,
    source_format: str | None = None,
    expected_base: OrdinaryArtifactRef | None = None,
    private_expected_base: PrivateArtifactRef | None = None,
) -> GeneratedFile:
    """Freeze one local file and declare its exact bytes for automatic SDK publication.

    Known document/image extensions retain typed validation. Every other
    format uses generic attachment custody. The owning toolset must authorize
    the original parent directory with authorized_generated_file_roots().
    Source bytes are retained privately for this exact local result, never JSON.
    """
    selected = Path(path).expanduser().resolve(strict=True)
    if not selected.is_file() or not 0 < selected.stat().st_size <= _MAX_BYTES:
        message = 'Generated file must be a nonempty bounded regular file'
        raise ValueError(message)
    identity = _IDENTITIES.get(selected.suffix.lower(), ('other', 'application/octet-stream'))
    if mime_type is not None:
        identity = next(
            (pair for pair in _IDENTITIES.values() if pair[1] == mime_type),
            ('other', 'application/octet-stream'),
        )
    directory = selected.parent / '.maivn-generated' / uuid4().hex
    directory.mkdir(parents=True)
    snapshot = directory / selected.name
    try:
        digest = hashlib.sha256()
        total = 0
        with selected.open('rb') as original, snapshot.open('xb') as captured:
            while chunk := original.read(65_536):
                total += len(chunk)
                if total > _MAX_BYTES:
                    message = 'Generated file exceeds the custody limit'
                    raise ValueError(message)  # noqa: TRY301 - capture cleanup must cover bounds failure.
                digest.update(chunk)
                captured.write(chunk)
        output = _LocalGeneratedFile.model_validate(
            {
                'kind': 'generated_file',
                'artifact_kind': identity[0],
                'mime_type': identity[1],
                'filename': selected.name,
                'path': str(snapshot),
                'size_bytes': total,
                'sha256': digest.hexdigest(),
                'custody': custody,
                'expected_base': expected_base,
                'private_expected_base': private_expected_base,
            }
        )
        output.bind_source(editable_source_archive(output, source, source_format=source_format))
    except BaseException:
        # Only this fresh capture is ours; never remove or alter the caller's file.
        with suppress(OSError):
            snapshot.unlink(missing_ok=True)
        with suppress(OSError):
            directory.rmdir()
        raise
    return output


__all__ = ['GeneratedFile', 'GeneratedFiles', 'editable_source_archive', 'generated_file']
