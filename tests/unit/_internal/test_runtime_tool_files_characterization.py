"""Characterize generated-file intake guards for ordinary and vault custody."""

from __future__ import annotations

import asyncio
import base64
from hashlib import sha256
from http import HTTPStatus
from typing import TYPE_CHECKING, Any, cast

import httpx
import pytest
from maivn_contracts.artifacts import GeneratedFile, GeneratedFiles, PrivateArtifactRef
from maivn_contracts.tools import ErrorToolOutcome, OkToolOutcome, ToolCall

from maivn._internal.models import ToolMetadata
from maivn._internal.private_artifacts import PrivateArtifactsClient
from maivn._internal.tool_files import intake_generated_file_outcome
from maivn._internal.transport.http import HttpJsonClient

if TYPE_CHECKING:
    from collections.abc import Callable
    from pathlib import Path

DOCX_MIME = 'application/vnd.openxmlformats-officedocument.wordprocessingml.document'
_ORDINARY_ERROR = (
    'sdk_generated_file_intake_error',
    'generated file could not be admitted to ordinary artifact storage',
)
_PRIVATE_ERROR = (
    'sdk_private_generated_file_intake_error',
    'Private generated files could not be saved to the vault.',
)
_RAW = b'PK\x03\x04characterized-file'


def _call() -> ToolCall:
    return ToolCall.model_validate(
        {
            'call_id': 'call_render',
            'spec_ref': {'tool_id': 'render', 'namespace': 'sdk', 'version': 'v1'},
            'arguments': {},
            'lineage': {'session_id': 'session_file', 'invocation_id': 'invocation_file'},
        }
    )


def _generated(path: Path, raw: bytes = _RAW, **changes: object) -> dict[str, object]:
    value: dict[str, object] = {
        'kind': 'generated_file',
        'artifact_kind': 'document',
        'filename': path.name,
        'path': str(path.resolve()),
        'mime_type': DOCX_MIME,
        'size_bytes': len(raw),
        'sha256': sha256(raw).hexdigest(),
        'custody': 'ordinary',
        'workspace': {'document_id': 'document_0001', 'filename': path.name},
    }
    value.update(changes)
    return value


def _ordinary_ref(raw: bytes = _RAW, **changes: object) -> dict[str, object]:
    value: dict[str, object] = {
        'artifact_id': 'artifact_file',
        'logical_output_id': 'call_render_0',
        'revision': 1,
        'kind': 'document',
        'mime_type': DOCX_MIME,
        'created_at': '2026-08-23T12:00:00Z',
        'effective_retention': {
            'policy_snapshot_id': 'policy_file',
            'retention_class': 'artifact_30d',
            'expires_at': '2099-09-22T12:00:00Z',
        },
        'custody': 'ordinary',
        'state': 'available',
        'display_filename': 'report.docx',
        'size_bytes': len(raw),
        'sha256': sha256(raw).hexdigest(),
        'producer': {
            'producer_class': 'external_tool',
            'producer_id': 'sdk_local_tool',
            'root_invocation_id': 'invocation_file',
            'session_id': 'session_file',
            'call_id': 'call_render',
        },
        'validation_receipt': {
            'receipt_id': 'receipt_file',
            'status': 'validated',
            'validator_profile': 'tool_file_document',
            'validator_version': '1',
        },
        'safe_preview': {'kind': 'document', 'page_count': 1},
        'retrieval_action': {'relation': 'artifact.download_authorization'},
    }
    value.update(changes)
    return value


def _private_ref(**changes: object) -> PrivateArtifactRef:
    value: dict[str, object] = {
        'artifact_id': 'private_file',
        'logical_output_id': 'call_render_0',
        'revision': 1,
        'kind': 'document',
        'mime_type': DOCX_MIME,
        'created_at': '2026-08-23T12:00:00Z',
        'effective_retention': {
            'policy_snapshot_id': 'policy_file',
            'retention_class': 'artifact_30d',
            'expires_at': '2099-09-22T12:00:00Z',
        },
        'custody': 'vault_private',
        'state': 'available_in_vault',
        'display_label': 'Private document',
        'creation_receipt_id': 'vault-receipt',
        'producer': {
            'producer_class': 'vault',
            'producer_id': 'vault',
            'root_invocation_id': 'invocation_file',
        },
        'retrieval_action': {'relation': 'artifact.vault_download_authorization'},
    }
    value.update(changes)
    return PrivateArtifactRef.model_validate(value)


class _Owner:
    def __init__(self, roots: object) -> None:
        self.roots = roots
        self.bound: list[tuple[str, str]] = []

    def authorized_generated_file_roots(self) -> object:
        return self.roots

    def render(self) -> None:
        return None


def _tool(target: Callable[..., object], *, batch: bool = False) -> ToolMetadata:
    schema = (GeneratedFiles if batch else GeneratedFile).model_json_schema()
    return ToolMetadata(name='render', target=target, output_schema=schema)


class _Recorder:
    def __init__(self, response: Callable[[httpx.Request], httpx.Response] | None = None) -> None:
        self.requests: list[str] = []
        self.response = response

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request.url.path)
        if self.response is None:
            return httpx.Response(HTTPStatus.INTERNAL_SERVER_ERROR)
        return self.response(request)

    def http(self) -> HttpJsonClient:
        return HttpJsonClient(
            base_url='http://testserver',
            api_key='test-key',
            timeout_seconds=1,
            transport=httpx.MockTransport(self),
        )


def _intake(
    tool: ToolMetadata,
    result: object,
    recorder: _Recorder,
    *,
    private_keys: tuple[str, ...] = (),
    publisher: PrivateArtifactsClient | None = None,
) -> Any:
    return asyncio.run(
        intake_generated_file_outcome(
            session_id='session_file',
            tool_call=_call(),
            tool=tool,
            private_data_keys=private_keys,
            outcome=OkToolOutcome(call_id='call_render', duration_ms=3, status='ok', result=result),
            http=recorder.http(),
            private_publisher=publisher,
        )
    )


def _error(outcome: object) -> tuple[str, str]:
    assert isinstance(outcome, ErrorToolOutcome)
    return outcome.error.code, outcome.error.message


def _file(tmp_path: Path, raw: bytes = _RAW) -> Path:
    path = tmp_path / 'report.docx'
    path.write_bytes(raw)
    return path


def _list_roots(root: Path) -> object:
    return [root]


def _no_roots(_root: Path) -> object:
    return ()


def _unusable_roots(root: Path) -> object:
    return (5, root / 'missing')


# --- ordinary custody --------------------------------------------------------------


@pytest.mark.parametrize(
    'roots',
    [
        pytest.param(_list_roots, id='list-not-tuple'),
        pytest.param(_no_roots, id='no-roots'),
        pytest.param(_unusable_roots, id='unusable-roots'),
    ],
)
def test_unusable_authorized_roots_refuse_before_upload(
    tmp_path: Path, roots: Callable[[Path], object]
) -> None:
    """Roots must be a non-empty tuple of existing directories containing the file."""
    path = _file(tmp_path)
    recorder = _Recorder()
    outcome = _intake(_tool(_Owner(roots(tmp_path)).render), _generated(path), recorder)
    assert _error(outcome) == _ORDINARY_ERROR
    assert recorder.requests == []


def test_a_tool_without_an_owner_cannot_admit_files(tmp_path: Path) -> None:
    """A bare function has no authorized roots and is refused."""
    path = _file(tmp_path)
    recorder = _Recorder()
    outcome = _intake(_tool(lambda: None), _generated(path), recorder)
    assert _error(outcome) == _ORDINARY_ERROR
    assert recorder.requests == []


@pytest.mark.parametrize(
    'changes',
    [
        pytest.param({'size_bytes': 52_428_801}, id='above-local-limit'),
        pytest.param({'size_bytes': len(_RAW) + 1}, id='size-mismatch'),
    ],
)
def test_declared_size_must_match_a_bounded_local_file(
    tmp_path: Path, changes: dict[str, object]
) -> None:
    """Oversized declarations and size mismatches fail before upload."""
    path = _file(tmp_path)
    recorder = _Recorder()
    outcome = _intake(_tool(_Owner((tmp_path,)).render), {**_generated(path), **changes}, recorder)
    assert _error(outcome) == _ORDINARY_ERROR
    assert recorder.requests == []


def test_source_exporter_must_return_bytes(tmp_path: Path) -> None:
    """A source exporter returning text is refused without an upload."""
    path = _file(tmp_path)

    class TextExporter(_Owner):
        def export_generated_file_source(self, _generated: GeneratedFile) -> object:
            return 'not bytes'

    recorder = _Recorder()
    outcome = _intake(_tool(TextExporter((tmp_path,)).render), _generated(path), recorder)
    assert _error(outcome) == _ORDINARY_ERROR
    assert recorder.requests == []


def test_matching_inline_content_is_admitted_and_sanitized(tmp_path: Path) -> None:
    """Inline base64 that matches the file is accepted; path and content are dropped."""
    path = _file(tmp_path)
    recorder = _Recorder(
        lambda _request: httpx.Response(
            HTTPStatus.CREATED,
            json={'status': 'accepted', 'artifact': _ordinary_ref(), 'source_archive': None},
        )
    )
    outcome = _intake(
        _tool(_Owner((tmp_path,)).render),
        _generated(path, content_base64=base64.b64encode(_RAW).decode('ascii')),
        recorder,
    )
    assert isinstance(outcome, OkToolOutcome)
    assert recorder.requests == ['/v1/sessions/session_file/tools/call_render/artifacts/0']
    assert set(cast('dict[str, object]', outcome.result)) == {
        'kind',
        'artifact_kind',
        'filename',
        'mime_type',
        'size_bytes',
        'sha256',
        'custody',
        'workspace',
    }
    assert [ref.artifact_id for ref in outcome.artifact_refs or ()] == ['artifact_file']


def test_an_invalid_batch_result_is_refused() -> None:
    """A batch-declared tool whose result is not GeneratedFiles is a batch intake error."""
    recorder = _Recorder()
    outcome = _intake(_tool(_Owner(()).render, batch=True), {'kind': 'nope'}, recorder)
    assert _error(outcome) == (
        'sdk_generated_file_intake_error',
        'generated files could not be admitted to artifact storage',
    )
    assert recorder.requests == []


# --- vault custody -----------------------------------------------------------------


class _Publisher(PrivateArtifactsClient):
    def __init__(self, ref: PrivateArtifactRef, http: Callable[[], HttpJsonClient]) -> None:
        super().__init__(http, vault_origin=None, vault_transport=None, timeout_seconds=1)
        self.ref = ref
        self.sources: list[bytes] = []

    async def apublish_file(
        self,
        output: GeneratedFile,
        *,
        source: bytes,
        session_id: str,
        call_id: str,
        output_index: int = 0,
    ) -> PrivateArtifactRef:
        _ = (output, session_id, call_id, output_index)
        self.sources.append(source)
        return self.ref


class _PrivateOwner(_Owner):
    def export_generated_file_source(self, _generated: GeneratedFile) -> bytes:
        return b'exported-source'

    def bind_private_generated_file_receipt(
        self, generated: GeneratedFile, artifact: PrivateArtifactRef
    ) -> None:
        self.bound.append((generated.filename, artifact.artifact_id))


def test_private_file_uses_the_owner_exporter_and_binds_the_receipt(tmp_path: Path) -> None:
    """Vault intake publishes the exported source and binds the returned receipt."""
    path = _file(tmp_path)
    recorder = _Recorder()
    owner = _PrivateOwner((tmp_path,))
    publisher = _Publisher(_private_ref(), recorder.http)
    outcome = _intake(
        _tool(owner.render), _generated(path), recorder, private_keys=('k',), publisher=publisher
    )
    assert isinstance(outcome, OkToolOutcome)
    assert outcome.result == {'kind': 'private_generated_file', 'custody': 'vault_private'}
    assert publisher.sources == [b'exported-source']
    assert owner.bound == [('report.docx', 'private_file')]
    assert recorder.requests == []


def test_private_exporter_must_return_bytes(tmp_path: Path) -> None:
    """A vault source exporter returning text is refused before publishing."""
    path = _file(tmp_path)

    class TextExporter(_PrivateOwner):
        def export_generated_file_source(self, _generated: GeneratedFile) -> bytes:
            return cast('bytes', 'not bytes')

    recorder = _Recorder()
    owner = TextExporter((tmp_path,))
    publisher = _Publisher(_private_ref(), recorder.http)
    outcome = _intake(
        _tool(owner.render), _generated(path), recorder, private_keys=('k',), publisher=publisher
    )
    assert _error(outcome) == _PRIVATE_ERROR
    assert publisher.sources == []


def test_private_receipt_of_the_wrong_kind_is_refused(tmp_path: Path) -> None:
    """A vault receipt that does not describe the file is an intake error."""
    path = _file(tmp_path)
    recorder = _Recorder()
    owner = _PrivateOwner((tmp_path,))
    publisher = _Publisher(_private_ref(mime_type='application/pdf'), recorder.http)
    outcome = _intake(
        _tool(owner.render), _generated(path), recorder, private_keys=('k',), publisher=publisher
    )
    assert _error(outcome) == _PRIVATE_ERROR
    assert owner.bound == []


def test_private_file_cannot_revise_an_ordinary_base(tmp_path: Path) -> None:
    """An ordinary expected base cannot be carried into vault custody."""
    path = _file(tmp_path)
    recorder = _Recorder()
    owner = _PrivateOwner((tmp_path,))
    publisher = _Publisher(_private_ref(), recorder.http)
    outcome = _intake(
        _tool(owner.render),
        _generated(path, expected_base=_ordinary_ref()),
        recorder,
        private_keys=('k',),
        publisher=publisher,
    )
    assert _error(outcome) == _PRIVATE_ERROR
    assert publisher.sources == []
