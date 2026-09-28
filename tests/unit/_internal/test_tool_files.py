# pyright: strict
"""SDK admission of contract-declared local generated files."""

from __future__ import annotations

import asyncio
import base64
import json
from hashlib import sha256
from http import HTTPStatus
from typing import TYPE_CHECKING, Any, cast

import httpx
import pytest
from maivn_contracts.artifacts import (
    GeneratedFile,
    GeneratedFiles,
    OrdinaryArtifactRef,
    PrivateArtifactRef,
    ToolFileSourceDescriptor,
)
from maivn_contracts.tools import ErrorToolOutcome, OkToolOutcome, ToolCall
from pydantic import AnyUrl

from maivn import Agent, Client, tool_output
from maivn._internal.config import ClientConfig
from maivn._internal.models import ToolMetadata
from maivn._internal.private_artifacts import PrivateArtifactsClient
from maivn._internal.transport.http import HttpJsonClient

if TYPE_CHECKING:
    from pathlib import Path

try:
    from maivn._internal.tool_files import intake_generated_file_outcome
except ModuleNotFoundError:
    intake_generated_file_outcome: Any = None

requires_tool_file_intake = pytest.mark.skipif(
    intake_generated_file_outcome is None,
    reason='SDK generated-file intake is not implemented yet',
)
DOCX_MIME = 'application/vnd.openxmlformats-officedocument.wordprocessingml.document'


def _artifact_ref(raw: bytes) -> dict[str, object]:
    return {
        'artifact_id': 'artifact_sdk_file',
        'logical_output_id': 'call_render_0',
        'revision': 1,
        'kind': 'document',
        'mime_type': DOCX_MIME,
        'created_at': '2026-08-23T12:00:00Z',
        'effective_retention': {
            'policy_snapshot_id': 'policy_sdk_file',
            'retention_class': 'artifact_30d',
            'expires_at': '2026-09-22T12:00:00Z',
        },
        'custody': 'ordinary',
        'state': 'available',
        'display_filename': 'report.docx',
        'size_bytes': len(raw),
        'sha256': sha256(raw).hexdigest(),
        'producer': {
            'producer_class': 'external_tool',
            'producer_id': 'sdk_local_tool',
            'root_invocation_id': 'invocation_sdk_file',
            'session_id': 'session_sdk_file',
            'call_id': 'call_render',
        },
        'validation_receipt': {
            'receipt_id': 'receipt_sdk_file',
            'status': 'validated',
            'validator_profile': 'tool_file_document',
            'validator_version': '1',
        },
        'safe_preview': {'kind': 'document', 'page_count': 1},
        'retrieval_action': {'relation': 'artifact.download_authorization'},
    }


def _tool_call() -> ToolCall:
    return ToolCall.model_validate(
        {
            'call_id': 'call_render',
            'spec_ref': {'tool_id': 'render', 'namespace': 'sdk', 'version': 'v1'},
            'arguments': {},
            'lineage': {
                'session_id': 'session_sdk_file',
                'invocation_id': 'invocation_sdk_file',
            },
        }
    )


def _generated(path: Path, raw: bytes, **changes: object) -> dict[str, object]:
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


def _outcome(result: object) -> OkToolOutcome:
    return OkToolOutcome(call_id='call_render', duration_ms=7, status='ok', result=result)


class _GeneratedFileOwner:
    def __init__(self, root: Path) -> None:
        self._root = root

    def authorized_generated_file_roots(self) -> tuple[Path, ...]:
        return (self._root,)

    def render(self) -> None:
        return None


def _tool(*, generated_output: bool, authorized_root: Path | None = None) -> ToolMetadata:
    target = (
        _GeneratedFileOwner(authorized_root).render if authorized_root is not None else lambda: None
    )
    return ToolMetadata(
        name='render',
        target=target,
        output_schema=(
            GeneratedFile.model_json_schema() if generated_output else {'type': 'object'}
        ),
    )


def test_generated_file_intake_entrypoint_exists() -> None:
    """The client pump needs one owned intake boundary."""
    assert callable(intake_generated_file_outcome)


@pytest.mark.parametrize('missing_source_receipt', [False, True])
def test_portable_source_is_framed_and_requires_committed_source_receipt(
    tmp_path: Path,
    *,
    missing_source_receipt: bool,
) -> None:
    """Only an accepted hidden-source commitment makes a file fully admitted."""
    raw = b'PK\x03\x04with-source'
    path = tmp_path / 'report.docx'
    path.write_bytes(raw)
    source = json.dumps(
        {
            'format': 'maivn-source-archive-v1',
            'workspace_type': 'documents',
            'manifest': {'filename': path.name},
            'output_sha256': sha256(raw).hexdigest(),
        }
    ).encode()
    descriptor = ToolFileSourceDescriptor(size_bytes=len(source), sha256=sha256(source).hexdigest())

    class SourceOwner(_GeneratedFileOwner):
        def export_generated_file_source(self, generated: GeneratedFile) -> bytes:
            assert generated.sha256 == sha256(raw).hexdigest()
            return source

    async def handler(request: httpx.Request) -> httpx.Response:
        frame = await request.aread()
        size = int.from_bytes(frame[:4], 'big')
        manifest = json.loads(frame[4 : 4 + size])
        assert ToolFileSourceDescriptor.model_validate(manifest['source_archive']) == descriptor
        assert frame[4 + size :] == raw + source
        return httpx.Response(
            201,
            json={
                'status': 'accepted',
                'artifact': _artifact_ref(raw),
                'source_archive': None
                if missing_source_receipt
                else descriptor.model_dump(mode='json'),
            },
        )

    tool = _tool(generated_output=True, authorized_root=tmp_path)
    tool = tool.model_copy(update={'target': SourceOwner(tmp_path).render})
    result = asyncio.run(
        intake_generated_file_outcome(
            session_id='session_sdk_file',
            tool_call=_tool_call(),
            tool=tool,
            private_data_keys=(),
            outcome=_outcome(_generated(path, raw)),
            http=HttpJsonClient(
                base_url='http://testserver',
                api_key='test-key',
                timeout_seconds=1,
                transport=httpx.MockTransport(handler),
            ),
        )
    )
    assert isinstance(result, ErrorToolOutcome if missing_source_receipt else OkToolOutcome)
    assert 'output_sha256' not in result.model_dump_json()


@pytest.mark.parametrize('explicit_private', [False, True])
@pytest.mark.parametrize('revision_gap', [False, True])
def test_private_generated_file_uses_vault_publisher_and_keeps_content_out_of_outcome(
    tmp_path: Path,
    *,
    explicit_private: bool,
    revision_gap: bool,
) -> None:
    """Private invocation output never calls ordinary intake, even for a plain file producer."""
    raw = b'private-customer-sentinel'
    path = tmp_path / 'report.docx'
    path.write_bytes(raw)
    ordinary = _artifact_ref(raw)
    ref = PrivateArtifactRef.model_validate(
        {
            **{
                key: ordinary[key]
                for key in (
                    'artifact_id',
                    'logical_output_id',
                    'revision',
                    'kind',
                    'mime_type',
                    'created_at',
                    'effective_retention',
                )
            },
            'custody': 'vault_private',
            'state': 'available_in_vault',
            'display_label': 'Private document',
            'creation_receipt_id': 'vault-receipt',
            'producer': {
                'producer_class': 'vault',
                'producer_id': 'vault',
                'root_invocation_id': 'invocation_sdk_file',
            },
            'retrieval_action': {'relation': 'artifact.vault_download_authorization'},
        }
    )

    base = ref if revision_gap else None
    if base is not None:
        ref = ref.model_copy(
            update={
                'artifact_id': 'later-vault-artifact',
                'revision': 3,
                'supersedes_artifact_id': base.artifact_id,
            }
        )

    class Publisher(PrivateArtifactsClient):
        async def apublish_file(
            self,
            output: GeneratedFile,
            *,
            source: bytes,
            session_id: str,
            call_id: str,
            output_index: int = 0,
        ) -> PrivateArtifactRef:
            assert output.path == str(path)
            assert (session_id, call_id, output_index) == ('session_sdk_file', 'call_render', 0)
            assert json.loads(source)['manifest'] == {
                'editable': False,
                'filename': output.filename,
            }
            assert json.loads(source)['output_sha256'] == output.sha256
            assert len(source) < 512  # noqa: PLR2004 - absent source must be a tiny envelope.
            return ref

    def forbidden(_request: httpx.Request) -> httpx.Response:
        pytest.fail('private generated bytes reached ordinary intake')

    http = HttpJsonClient(
        base_url='http://testserver',
        api_key='test-key',
        timeout_seconds=1,
        transport=httpx.MockTransport(forbidden),
    )
    publisher = Publisher(lambda: http, vault_origin=None, vault_transport=None, timeout_seconds=1)
    result = asyncio.run(
        intake_generated_file_outcome(
            session_id='session_sdk_file',
            tool_call=_tool_call(),
            tool=_tool(generated_output=True, authorized_root=tmp_path),
            private_data_keys=() if explicit_private else ('customer',),
            outcome=_outcome(
                {
                    **_generated(path, raw),
                    'custody': 'vault_private' if explicit_private else 'ordinary',
                    'private_expected_base': base.model_dump(mode='json') if base else None,
                }
            ),
            http=http,
            private_publisher=publisher,
        )
    )
    assert isinstance(result, OkToolOutcome)
    assert result.artifact_refs == (ref,)
    encoded = result.model_dump_json()
    assert 'report.docx' not in encoded
    assert sha256(raw).hexdigest() not in encoded
    assert 'private-customer-sentinel' not in encoded


@pytest.mark.parametrize('fail_second', [False, True])
def test_generated_batch_keeps_successes_and_stable_output_slots(
    tmp_path: Path,
    *,
    fail_second: bool,
) -> None:
    """A partial upload keeps its first downloadable ref without exposing local sources."""
    raw = b'PK\x03\x04deterministic-docx-bytes'
    files: list[dict[str, object]] = []
    for name in ('report.docx', 'second.docx'):
        path = tmp_path / name
        path.write_bytes(raw)
        files.append(_generated(path, raw))
    paths: list[str] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        paths.append(request.url.path)
        index = int(request.url.path.rsplit('/', 1)[1])
        if fail_second and index == 1:
            return httpx.Response(503, json={'detail': 'unavailable'})
        ref = _artifact_ref(raw)
        ref.update(
            artifact_id=f'artifact_sdk_{index}',
            logical_output_id=f'output_{index}',
            display_filename=files[index]['filename'],
        )
        return httpx.Response(
            201,
            json={
                'status': 'accepted',
                'artifact': ref,
                'idempotent_replay': False,
            },
        )

    client = HttpJsonClient(
        base_url='http://testserver',
        api_key='test-key',
        timeout_seconds=1,
        transport=httpx.MockTransport(handler),
    )
    metadata = _tool(generated_output=True, authorized_root=tmp_path).model_copy(
        update={'output_schema': GeneratedFiles.model_json_schema()},
    )
    outcome = asyncio.run(
        intake_generated_file_outcome(
            session_id='session_sdk_file',
            tool_call=_tool_call(),
            tool=metadata,
            private_data_keys=(),
            outcome=_outcome({'kind': 'generated_files', 'files': files}),
            http=client,
        )
    )

    assert paths == [
        '/v1/sessions/session_sdk_file/tools/call_render/artifacts/0',
        '/v1/sessions/session_sdk_file/tools/call_render/artifacts/1',
    ]
    assert [ref.artifact_id for ref in outcome.artifact_refs or ()] == (
        ['artifact_sdk_0'] if fail_second else ['artifact_sdk_0', 'artifact_sdk_1']
    )
    if fail_second:
        assert isinstance(outcome, ErrorToolOutcome)
        assert outcome.error.retryable
    else:
        assert isinstance(outcome, OkToolOutcome)
    assert str(tmp_path) not in outcome.model_dump_json()


@requires_tool_file_intake
def test_contract_declared_file_streams_and_local_sources_are_sanitized(tmp_path: Path) -> None:
    """A nominal file becomes an artifact ref before the tool outcome is submitted."""
    raw = b'PK\x03\x04deterministic-docx-bytes'
    path = tmp_path / 'report.docx'
    path.write_bytes(raw)
    requests: list[tuple[str, bytes]] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        body = await request.aread()
        requests.append((request.url.path, body))
        return httpx.Response(
            201,
            json={
                'status': 'accepted',
                'artifact': _artifact_ref(raw),
                'idempotent_replay': False,
            },
        )

    client = HttpJsonClient(
        base_url='http://testserver',
        api_key='test-key',
        timeout_seconds=1,
        transport=httpx.MockTransport(handler),
    )
    outcome = asyncio.run(
        intake_generated_file_outcome(
            session_id='session_sdk_file',
            tool_call=_tool_call(),
            tool=_tool(generated_output=True, authorized_root=tmp_path),
            private_data_keys=(),
            outcome=_outcome(_generated(path, raw)),
            http=client,
        )
    )

    assert isinstance(outcome, OkToolOutcome)
    assert len(outcome.artifact_refs or ()) == 1
    assert outcome.artifact_refs is not None
    assert outcome.artifact_refs[0].artifact_id == 'artifact_sdk_file'
    result = cast('dict[str, object]', outcome.result)
    assert 'path' not in result
    assert 'content_base64' not in result
    assert result['workspace'] == {'document_id': 'document_0001', 'filename': 'report.docx'}
    assert [item[0] for item in requests] == [
        '/v1/sessions/session_sdk_file/tools/call_render/artifacts/0'
    ]
    frame = requests[0][1]
    control_size = int.from_bytes(frame[:4], byteorder='big')
    control = cast('dict[str, object]', json.loads(frame[4 : 4 + control_size]))
    assert control['session_id'] == 'session_sdk_file'
    assert control['call_id'] == 'call_render'
    assert control['output_index'] == 0
    assert 'path' not in control
    assert 'content_base64' not in control
    assert 'expected_base' not in control
    assert frame[4 + control_size :] == raw


@pytest.mark.parametrize('wrong_lineage', [False, True])
def test_revision_intake_binds_receipt_to_exact_base_before_source_hook(
    tmp_path: Path,
    *,
    wrong_lineage: bool,
) -> None:
    """Only exact successor receipts can update the toolset source binding."""
    raw = b'PK\x03\x04revised-docx-bytes'
    path = tmp_path / 'report.docx'
    path.write_bytes(raw)
    base = OrdinaryArtifactRef.model_validate(_artifact_ref(b'previous'))
    bound: list[OrdinaryArtifactRef] = []

    class RevisionOwner(_GeneratedFileOwner):
        def bind_generated_file_receipt(
            self, generated: GeneratedFile, artifact: OrdinaryArtifactRef
        ) -> None:
            assert generated.expected_base == base
            bound.append(artifact)

    async def handler(request: httpx.Request) -> httpx.Response:
        frame = await request.aread()
        size = int.from_bytes(frame[:4], 'big')
        control = json.loads(frame[4 : 4 + size])
        assert OrdinaryArtifactRef.model_validate(control['expected_base']) == base
        ref = _artifact_ref(raw)
        ref.update(
            artifact_id='next_artifact',
            revision=2,
            supersedes_artifact_id=base.artifact_id,
            logical_output_id='wrong_output' if wrong_lineage else base.logical_output_id,
        )
        return httpx.Response(
            201, json={'status': 'accepted', 'artifact': ref, 'idempotent_replay': False}
        )

    owner = RevisionOwner(tmp_path)
    outcome = asyncio.run(
        intake_generated_file_outcome(
            session_id='session_sdk_file',
            tool_call=_tool_call(),
            tool=ToolMetadata(
                name='render', target=owner.render, output_schema=GeneratedFile.model_json_schema()
            ),
            private_data_keys=(),
            outcome=_outcome(_generated(path, raw, expected_base=base)),
            http=HttpJsonClient(
                base_url='http://testserver',
                api_key='test-key',
                timeout_seconds=1,
                transport=httpx.MockTransport(handler),
            ),
        )
    )
    assert isinstance(outcome, ErrorToolOutcome if wrong_lineage else OkToolOutcome)
    assert len(bound) == (0 if wrong_lineage else 1)


@pytest.mark.parametrize('wrong_field', ['sha256', 'size_bytes', 'mime_type', 'display_filename'])
def test_intake_receipt_must_describe_the_uploaded_file(tmp_path: Path, wrong_field: str) -> None:
    """A valid-shaped receipt for different bytes must never become the attachment."""
    raw = b'PK\x03\x04docx-bytes'
    path = tmp_path / 'report.docx'
    path.write_bytes(raw)
    ref = _artifact_ref(raw)
    replacements: dict[str, object] = {
        'sha256': 'f' * 64,
        'size_bytes': len(raw) + 1,
        'mime_type': 'application/pdf',
        'display_filename': 'wrong.docx',
    }
    ref[wrong_field] = replacements[wrong_field]
    client = HttpJsonClient(
        base_url='http://testserver',
        api_key='test-key',
        timeout_seconds=1,
        transport=httpx.MockTransport(
            lambda _request: httpx.Response(
                201,
                json={
                    'status': 'accepted',
                    'artifact': ref,
                    'idempotent_replay': False,
                },
            )
        ),
    )
    outcome = asyncio.run(
        intake_generated_file_outcome(
            session_id='session_sdk_file',
            tool_call=_tool_call(),
            tool=_tool(generated_output=True, authorized_root=tmp_path),
            private_data_keys=(),
            outcome=_outcome(_generated(path, raw)),
            http=client,
        )
    )
    assert isinstance(outcome, ErrorToolOutcome)
    assert not outcome.artifact_refs
    assert str(tmp_path) not in outcome.model_dump_json()


@requires_tool_file_intake
def test_lookalike_result_without_declared_contract_is_not_uploaded(tmp_path: Path) -> None:
    """Ordinary dictionaries never become files through shape guessing."""
    raw = b'not-an-artifact-without-schema'
    path = tmp_path / 'report.docx'
    path.write_bytes(raw)
    requests = 0

    async def handler(_request: httpx.Request) -> httpx.Response:
        nonlocal requests
        requests += 1
        return httpx.Response(500)

    original = _outcome(_generated(path, raw))
    outcome = asyncio.run(
        intake_generated_file_outcome(
            session_id='session_sdk_file',
            tool_call=_tool_call(),
            tool=_tool(generated_output=False),
            private_data_keys=(),
            outcome=original,
            http=HttpJsonClient(
                base_url='http://testserver',
                api_key='test-key',
                timeout_seconds=1,
                transport=httpx.MockTransport(handler),
            ),
        )
    )

    assert outcome == original
    assert requests == 0


@requires_tool_file_intake
def test_http_intake_failure_becomes_an_honest_tool_error(tmp_path: Path) -> None:
    """A storage outage must not leak a path or tear down the whole event stream."""
    raw = b'PK\x03\x04unavailable-intake'
    path = tmp_path / 'report.docx'
    path.write_bytes(raw)

    async def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            HTTPStatus.SERVICE_UNAVAILABLE,
            json={'detail': 'Storage unavailable.', 'code': 'artifact_intake_unavailable'},
        )

    outcome = asyncio.run(
        intake_generated_file_outcome(
            session_id='session_sdk_file',
            tool_call=_tool_call(),
            tool=_tool(generated_output=True, authorized_root=tmp_path),
            private_data_keys=(),
            outcome=_outcome(_generated(path, raw)),
            http=HttpJsonClient(
                base_url='http://testserver',
                api_key='test-key',
                timeout_seconds=1,
                transport=httpx.MockTransport(handler),
            ),
        )
    )

    assert isinstance(outcome, ErrorToolOutcome)
    assert outcome.error.code == 'sdk_generated_file_intake_error'
    assert str(path) not in outcome.error.message


@pytest.mark.parametrize(
    ('private_data_keys', 'changes', 'expected_code'),
    [
        (('customer_name',), {}, 'sdk_private_artifact_requires_vault'),
        ((), {'sha256': '0' * 64}, 'sdk_generated_file_intake_error'),
        (
            (),
            {'content_base64': base64.b64encode(b'different').decode('ascii')},
            'sdk_generated_file_intake_error',
        ),
    ],
)
@requires_tool_file_intake
def test_private_or_mismatched_file_fails_before_upload_without_leaking_local_values(
    tmp_path: Path,
    private_data_keys: tuple[str, ...],
    changes: dict[str, object],
    expected_code: str,
) -> None:
    """Unsafe admissions fail closed and return value-free SDK errors."""
    raw = b'PK\x03\x04private-or-mismatched'
    path = tmp_path / 'report.docx'
    path.write_bytes(raw)
    requests = 0

    async def handler(_request: httpx.Request) -> httpx.Response:
        nonlocal requests
        requests += 1
        return httpx.Response(500)

    outcome = asyncio.run(
        intake_generated_file_outcome(
            session_id='session_sdk_file',
            tool_call=_tool_call(),
            tool=_tool(generated_output=True, authorized_root=tmp_path),
            private_data_keys=private_data_keys,
            outcome=_outcome(_generated(path, raw, **changes)),
            http=HttpJsonClient(
                base_url='http://testserver',
                api_key='test-key',
                timeout_seconds=1,
                transport=httpx.MockTransport(handler),
            ),
        )
    )

    assert isinstance(outcome, ErrorToolOutcome)
    assert outcome.error.code == expected_code
    assert str(path) not in outcome.error.message
    assert path.name not in outcome.error.message
    assert requests == 0


@requires_tool_file_intake
def test_generated_file_outside_registered_tool_root_is_refused_before_upload(
    tmp_path: Path,
) -> None:
    """A nominal GeneratedFile cannot select an arbitrary readable local file."""
    allowed = tmp_path / 'allowed'
    allowed.mkdir()
    outside = tmp_path / 'outside'
    outside.mkdir()
    raw = b'PK\x03\x04outside-authorized-root'
    path = outside / 'report.docx'
    path.write_bytes(raw)
    requests = 0

    async def handler(_request: httpx.Request) -> httpx.Response:
        nonlocal requests
        requests += 1
        return httpx.Response(500)

    outcome = asyncio.run(
        intake_generated_file_outcome(
            session_id='session_sdk_file',
            tool_call=_tool_call(),
            tool=_tool(generated_output=True, authorized_root=allowed),
            private_data_keys=(),
            outcome=_outcome(_generated(path, raw)),
            http=HttpJsonClient(
                base_url='http://testserver',
                api_key='test-key',
                timeout_seconds=1,
                transport=httpx.MockTransport(handler),
            ),
        )
    )

    assert isinstance(outcome, ErrorToolOutcome)
    assert outcome.error.code == 'sdk_generated_file_intake_error'
    assert str(path) not in outcome.error.message
    assert requests == 0


@requires_tool_file_intake
@pytest.mark.parametrize(
    'owner_scope', [None, {'organization_id': 'org-authorized', 'project_id': 'project-authorized'}]
)
def test_client_pump_admits_file_before_submitting_path_free_tool_outcome(
    tmp_path: Path, owner_scope: dict[str, str] | None
) -> None:
    """The real local-tool event pump must run intake before `/tools/results`."""
    raw = b'PK\x03\x04client-pump-docx'
    path = tmp_path / 'report.docx'
    path.write_bytes(raw)
    calls: list[str] = []
    submitted: dict[str, object] = {}

    async def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request.url.path)
        if request.url.path == '/v1/invoke':
            return httpx.Response(
                HTTPStatus.ACCEPTED,
                json={'session_id': 'session_sdk_file', 'stream_position': 0},
            )
        if request.url.path.endswith('/events'):
            return _render_tool_sse_response(owner_scope)
        if request.url.path.endswith('/artifacts/0'):
            _ = await request.aread()
            return httpx.Response(
                HTTPStatus.CREATED,
                json={
                    'status': 'accepted',
                    'artifact': _artifact_ref(raw),
                    'idempotent_replay': False,
                },
            )
        if request.url.path.endswith('/tools/results'):
            submitted.update(cast('dict[str, object]', json.loads(await request.aread())))
            return httpx.Response(HTTPStatus.ACCEPTED, json={'accepted': True})
        return httpx.Response(HTTPStatus.NOT_FOUND)

    client = Client(
        config=ClientConfig(api_key='test-key', base_url=AnyUrl('http://testserver')),
        transport=httpx.MockTransport(handler),
    )
    agent = Agent(name='generated-file-intake', client=client)

    class Renderer:
        def authorized_generated_file_roots(self) -> tuple[Path, ...]:
            return (tmp_path,)

        @tool_output(GeneratedFile.model_json_schema())
        def render(self) -> GeneratedFile:
            return GeneratedFile.model_validate(_generated(path, raw))

    render = agent.toolify(name='render')(Renderer().render)
    _ = render
    response = agent.invoke('render the report')

    assert response.response == 'report ready'
    assert response.organization_id == (owner_scope or {}).get('organization_id')
    assert response.project_id == (owner_scope or {}).get('project_id')
    assert calls.index('/v1/sessions/session_sdk_file/tools/call_render/artifacts/0') < calls.index(
        '/v1/sessions/session_sdk_file/tools/results'
    )
    outcomes = cast('list[dict[str, object]]', submitted['outcomes'])
    result = cast('dict[str, object]', outcomes[0]['result'])
    assert 'path' not in result
    assert 'content_base64' not in result
    refs = cast('list[dict[str, object]]', outcomes[0]['artifact_refs'])
    assert refs[0]['artifact_id'] == 'artifact_sdk_file'


def _render_tool_sse_response(owner_scope: dict[str, str] | None = None) -> httpx.Response:
    start = {
        'event_id': 'event_render_start',
        'ordinal': 'ordinal_1',
        'type': 'system_tool_start',
        'session_id': 'session_sdk_file',
        'root_event_id': 'event_root',
        'correlation_id': 'call_render',
        'actor': 'tool:render',
        'mode': 'hosted',
        'trigger_chain_depth': 0,
        'job_depth': 0,
        'job_index': 0,
        'payload': {
            'stage': 'tool_start',
            'tool_name': 'render',
            'tool_call': _tool_call().model_dump(mode='json'),
        },
        'ts': '2026-08-23T12:00:00Z',
    }
    phase = {
        'event_id': 'event_render_phase',
        'ordinal': 'ordinal_2',
        'type': 'system_tool_chunk',
        'session_id': 'session_sdk_file',
        'root_event_id': 'event_root',
        'actor': 'invoke',
        'mode': 'hosted',
        'trigger_chain_depth': 0,
        'job_depth': 0,
        'job_index': 0,
        'payload': {'stage': 'tool_execution', 'phase': 'started'},
        'ts': '2026-08-23T12:00:00Z',
    }
    final = {
        'event_id': 'event_render_final',
        'ordinal': 'ordinal_3',
        'type': 'final',
        'session_id': 'session_sdk_file',
        'root_event_id': 'event_root',
        'payload': {
            'message': {
                'message_id': 'message_render_final',
                'role': 'assistant',
                'content': 'report ready',
                'ts': '2026-08-23T12:00:01Z',
            },
            'usage': {'input_tokens': 1, 'output_tokens': 1},
            'tool_calls': {'count': 1, 'names': ['render']},
            **(owner_scope or {}),
        },
        'ts': '2026-08-23T12:00:01Z',
    }
    content = (
        f'id: 1\nevent: system_tool_start\ndata: {json.dumps(start)}\n\n'
        f'id: 2\nevent: system_tool_chunk\ndata: {json.dumps(phase)}\n\n'
        f'id: 3\nevent: final\ndata: {json.dumps(final)}\n\n'
    )
    return httpx.Response(
        HTTPStatus.OK,
        headers={'content-type': 'text/event-stream'},
        content=content,
    )


@pytest.mark.parametrize('corrupt', ['none', 'digest', 'output'])
def test_source_download_posts_exact_reference_and_validates_its_package(corrupt: str) -> None:
    """An authenticated source read remains bound to the selected output bytes."""
    ref = OrdinaryArtifactRef.model_validate(_artifact_ref(b'original'))
    raw = json.dumps(
        {
            'format': 'maivn-source-archive-v1',
            'workspace_type': 'documents',
            'manifest': {'filename': 'report.docx'},
            'output_sha256': '0' * 64 if corrupt == 'output' else ref.sha256,
        }
    ).encode()

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == '/v1/artifacts/source'
        assert request.method == 'POST'
        assert request.headers['authorization'] == 'Bearer test-key'
        assert json.loads(request.content) == {
            'artifact_ref': ref.model_dump(mode='json'),
            'session_id': 'session-current',
        }
        return httpx.Response(
            200,
            content=raw,
            headers={
                'x-maivn-source-format': 'maivn-source-archive-v1',
                'x-maivn-source-sha256': '0' * 64
                if corrupt == 'digest'
                else sha256(raw).hexdigest(),
            },
        )

    client = Client(
        config=ClientConfig(api_key='test-key', base_url=AnyUrl('http://testserver')),
        transport=httpx.MockTransport(handler),
    )
    if corrupt == 'none':
        assert client.artifacts.download_source(ref, session_id='session-current') == raw
        assert (
            asyncio.run(client.artifacts.adownload_source(ref, session_id='session-current')) == raw
        )
    else:
        with pytest.raises(ValueError, match='artifact download response was invalid'):
            client.artifacts.download_source(ref, session_id='session-current')
