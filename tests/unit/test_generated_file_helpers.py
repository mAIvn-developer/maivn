# pyright: reportPrivateUsage=false
"""Developer-produced arbitrary files use the same exact artifact publication boundary."""

from __future__ import annotations

import asyncio
import base64
import hashlib
import json
from pathlib import Path
from typing import TYPE_CHECKING, Any, cast

import httpx
import pytest
from maivn_contracts.artifacts import PrivateArtifactRef
from maivn_contracts.tools import ToolCall

from maivn import Agent, toolify, toolset
from maivn._internal.client import _stream_with_local_tools
from maivn._internal.models import StreamEvent, ToolMetadata
from maivn._internal.tool_files import intake_generated_file_outcome
from maivn._internal.tool_runtime import LocalToolRuntime
from maivn._internal.transport.http import HttpJsonClient
from tests.unit._internal.test_tool_files import _artifact_ref

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

from maivn.files import GeneratedFile, GeneratedFiles, editable_source_archive, generated_file


@pytest.mark.parametrize('extension', ['csv', 'zip', 'future-format'])
def test_future_file_uses_generic_download_identity(tmp_path: Path, extension: str) -> None:
    """New extensions need no SDK/server MIME enumeration."""
    path = tmp_path / f'output.{extension}'
    raw = b'new format bytes'
    path.write_bytes(raw)
    output = generated_file(path)
    assert output.artifact_kind == 'other'
    assert output.mime_type == 'application/octet-stream'
    assert output.filename == path.name
    assert output.sha256 == hashlib.sha256(raw).hexdigest()
    assert output.size_bytes == len(raw)


def test_explicit_source_is_versioned_bounded_and_absent_from_generated_result(
    tmp_path: Path,
) -> None:
    """Private source metadata never appears in a GeneratedFile model dump."""
    path = tmp_path / 'output.future-format'
    path.write_bytes(b'rendered')
    output = generated_file(
        path,
        custody='vault_private',
        source=b'private-editable-value',
        source_format='example.future/v1',
    )
    assert 'private-editable-value' not in output.model_dump_json()
    assert 'example.future/v1' not in output.model_dump_json()
    package = json.loads(
        editable_source_archive(
            output, b'private-editable-value', source_format='example.future/v1'
        )
    )
    assert package['manifest']['editable'] is True
    assert package['manifest']['source_format'] == 'example.future/v1'
    assert base64.b64decode(package['manifest']['content_base64']) == b'private-editable-value'
    assert package['output_sha256'] == output.sha256


def test_source_omission_is_explicitly_noneditable_and_does_not_copy_original(
    tmp_path: Path,
) -> None:
    """The original is already separately retained; no duplicate base64 payload is needed."""
    path = tmp_path / 'large.future-format'
    path.write_bytes(b'x' * 1_000_000)
    package = editable_source_archive(generated_file(path))
    assert len(package) < 512  # noqa: PLR2004 - small fixed envelope excludes payload.
    assert json.loads(package)['manifest'] == {'editable': False, 'filename': path.name}


def test_custom_toolset_return_annotation_declares_nominal_generated_output(tmp_path: Path) -> None:
    """Only the standard file return type opts a tool into automatic custody intake."""

    @toolset(prefix='custom')
    class Custom:
        def authorized_generated_file_roots(self) -> tuple[Path, ...]:
            return (tmp_path,)

        @toolify()
        def make(self) -> GeneratedFile:
            path = tmp_path / 'out.future-format'
            path.write_bytes(b'new format')
            return generated_file(path)

    agent = Agent(name='Custom files', api_key='key')
    agent.add_toolset(Custom())
    metadata = agent.compile_tools()[0]
    assert isinstance(metadata.output_schema, dict)
    schema = cast('dict[str, object]', metadata.output_schema)
    assert schema['x-maivn-output-kind'] == 'generated_file'


@pytest.mark.parametrize('annotated', [False, True])
@pytest.mark.parametrize('extension', ['csv', 'zip', 'future-format'])
@pytest.mark.parametrize('private', [False, True])
@pytest.mark.parametrize('with_source', [False, True])
def test_custom_helper_flows_through_registered_runtime_and_custody(
    tmp_path: Path,
    extension: str,
    *,
    private: bool,
    with_source: bool,
    annotated: bool,
) -> None:
    """Exact local sources survive runtime serialization and stay out of posted tool outcomes."""
    raw = b'custom binary content'
    source_raw = b'private-source-value'
    expected_package: list[bytes] = []
    filename = f'custom.{extension}'
    ordinary = _artifact_ref(raw)
    ordinary.update(
        {
            'kind': 'other',
            'mime_type': 'application/octet-stream',
            'display_filename': filename,
            'safe_preview': None,
        }
    )
    private_ref = PrivateArtifactRef.model_validate(
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
            'display_label': 'Private artifact',
            'creation_receipt_id': 'receipt',
            'producer': {
                'producer_class': 'vault',
                'producer_id': 'vault',
                'root_invocation_id': 'invocation_sdk_file',
            },
            'retrieval_action': {'relation': 'artifact.vault_download_authorization'},
        }
    )

    @toolset(prefix='custom')
    class Custom:
        def authorized_generated_file_roots(self) -> tuple[Path, ...]:
            return (tmp_path,)

        @toolify()
        def make(self) -> GeneratedFile:
            path = tmp_path / filename
            path.write_bytes(raw)
            output = generated_file(
                path,
                custody='vault_private' if private else 'ordinary',
                source=source_raw if with_source else None,
                source_format='example.custom/v1' if with_source else None,
            )
            expected_package.append(
                editable_source_archive(
                    output,
                    source_raw if with_source else None,
                    source_format='example.custom/v1' if with_source else None,
                )
            )
            path.write_bytes(b'changed after snapshot')
            return output

    posted: list[dict[str, Any]] = []

    def receive(request: httpx.Request) -> httpx.Response:
        if '/artifacts/' in request.url.path:
            assert not private
            frame = request.content
            size = int.from_bytes(frame[:4], 'big')
            manifest = json.loads(frame[4 : 4 + size])
            assert frame[4 + size :] == raw + expected_package[0]
            return httpx.Response(
                200, json={'artifact': ordinary, 'source_archive': manifest['source_archive']}
            )
        posted.append(json.loads(request.content))
        return httpx.Response(200, json={})

    class Publisher:
        async def apublish_file(
            self,
            output: GeneratedFile,
            *,
            source: bytes,
            session_id: str,
            call_id: str,
            output_index: int,
        ) -> PrivateArtifactRef:
            assert private
            assert await asyncio.to_thread(Path(output.path).read_bytes) == raw
            assert (session_id, call_id) == ('session_sdk_file', 'call_render')
            assert source == expected_package[0]
            assert output_index == 0
            return private_ref

    if not annotated:
        Custom.make.__annotations__.pop('return', None)
    agent = Agent(name='Custom format', api_key='test-key')
    agent.add_toolset(Custom())
    runtime = LocalToolRuntime(agent.compile_tools(), private_data=None)

    async def run() -> None:
        async def events() -> AsyncIterator[StreamEvent]:
            yield StreamEvent(
                position=1,
                event_type='system_tool_start',
                data={
                    'payload': {
                        'tool_call': {
                            'call_id': 'call_render',
                            'spec_ref': {
                                'tool_id': 'CUSTOM_make',
                                'namespace': 'sdk',
                                'version': 'v1',
                            },
                            'arguments': {},
                            'lineage': {
                                'session_id': 'session_sdk_file',
                                'invocation_id': 'invocation_sdk_file',
                            },
                        }
                    }
                },
            )

        http = HttpJsonClient(
            base_url='http://testserver',
            api_key='key',
            timeout_seconds=1,
            transport=httpx.MockTransport(receive),
        )
        async for _ in _stream_with_local_tools(
            events=events(),
            tool_runtime=runtime,
            tool_http=http,
            session_id='session_sdk_file',
            private_publisher=cast('Any', Publisher()),
        ):
            pass

    asyncio.run(run())
    result = posted[0]['outcomes'][0]
    assert result['status'] == 'ok'
    assert result['artifact_refs'][0]['kind'] == 'other'
    wire = json.dumps(posted)
    assert 'private-source-value' not in wire
    assert base64.b64encode(source_raw).decode() not in wire
    assert 'example.custom/v1' not in wire
    assert str(tmp_path) not in wire
    assert not private or filename not in wire


@pytest.mark.parametrize(
    ('source', 'source_format'), [(b'data', None), (None, 'example/v1'), (b'', 'example/v1')]
)
def test_ambiguous_source_configuration_is_refused(
    tmp_path: Path, source: bytes | None, source_format: str | None
) -> None:
    """An editable promise requires nonempty source and an explicit adapter version."""
    path = tmp_path / 'data.csv'
    path.write_bytes(b'header\nvalue')
    with pytest.raises(ValueError, match=r'source|nonempty'):
        generated_file(path, source=source, source_format=source_format)
    assert list((tmp_path / '.maivn-generated').iterdir()) == []
    assert path.read_bytes() == b'header\nvalue'


def test_arbitrary_mime_is_generic_and_empty_file_is_refused(tmp_path: Path) -> None:
    """A future MIME label does not grant inline content execution or bypass byte limits."""
    path = tmp_path / 'future.custom'
    path.write_bytes(b'future format')
    output = generated_file(path, mime_type='application/vnd.future.custom')
    assert (output.artifact_kind, output.mime_type) == ('other', 'application/octet-stream')
    path.write_bytes(b'')
    with pytest.raises(ValueError, match=r'source|nonempty'):
        generated_file(path)


def test_batch_helper_retains_each_exact_local_source_before_json_serialization(
    tmp_path: Path,
) -> None:
    """A nominal batch keeps distinct private source objects associated with each output slot."""
    outputs: list[GeneratedFile] = []
    for index in range(3):
        path = tmp_path / f'output-{index}.future-format'
        path.write_bytes(str(index).encode())
        outputs.append(
            generated_file(path, source=f'source-{index}'.encode(), source_format='example/v1')
        )
    batch = GeneratedFiles(kind='generated_files', files=tuple(outputs))
    runtime = LocalToolRuntime([ToolMetadata(name='make', target=lambda: batch)], private_data=None)
    call = ToolCall.model_validate(
        {
            'call_id': 'batch',
            'spec_ref': {'tool_id': 'make', 'namespace': 'sdk', 'version': 'v1'},
            'arguments': {},
            'lineage': {'session_id': 'session', 'invocation_id': 'invocation'},
        }
    )
    event = StreamEvent(
        position=1,
        event_type='system_tool_start',
        data={'payload': {'tool_call': call.model_dump(mode='json')}},
    )
    outcome = asyncio.run(runtime.outcome_for_event(event))
    assert outcome is not None
    assert outcome.status == 'ok'
    metadata = runtime.metadata_for_call(call)
    assert metadata is not None
    assert metadata.output_schema == GeneratedFiles.model_json_schema()
    sources = runtime.take_generated_file_sources(call)
    assert len(sources) == len(outputs)
    for index, source in enumerate(sources):
        assert source is not None
        package = json.loads(source)
        assert base64.b64decode(package['manifest']['content_base64']) == f'source-{index}'.encode()
        assert package['output_sha256'] == outputs[index].sha256
    assert runtime.take_generated_file_sources(call) == ()


@pytest.mark.parametrize('private', [False, True])
def test_unannotated_nominal_file_without_custody_capability_fails_closed(
    tmp_path: Path, *, private: bool
) -> None:
    """Omitting roots or a Vault publisher cannot expose an unannotated local file."""
    path = tmp_path / 'sensitive-filename.future-format'
    path.write_bytes(b'sensitive-original')
    output = generated_file(path, custody='vault_private' if private else 'ordinary')
    tool = ToolMetadata(name='make', target=lambda: output)
    runtime = LocalToolRuntime([tool], private_data=None)
    call = ToolCall.model_validate(
        {
            'call_id': 'unannotated',
            'spec_ref': {'tool_id': 'make', 'namespace': 'sdk', 'version': 'v1'},
            'arguments': {},
            'lineage': {'session_id': 'session', 'invocation_id': 'invocation'},
        }
    )

    def forbid_network(_request: httpx.Request) -> httpx.Response:
        pytest.fail('A file without custody capability must never reach a transport')

    async def run() -> None:
        outcome = await runtime.outcome_for_event(
            StreamEvent(
                position=1,
                event_type='system_tool_start',
                data={'payload': {'tool_call': call.model_dump(mode='json')}},
            )
        )
        assert outcome is not None
        safe = await intake_generated_file_outcome(
            session_id='session',
            tool_call=call,
            tool=runtime.metadata_for_call(call),
            private_data_keys=(),
            outcome=outcome,
            http=HttpJsonClient(
                base_url='http://testserver',
                api_key='key',
                timeout_seconds=1,
                transport=httpx.MockTransport(forbid_network),
            ),
            local_sources=runtime.take_generated_file_sources(call),
        )
        assert safe.status == 'error'
        wire = safe.model_dump_json()
        assert 'sensitive' not in wire
        assert str(tmp_path) not in wire

    asyncio.run(run())
    assert tool.output_schema is None
    unrelated = call.model_copy(update={'call_id': 'different-call'})
    assert runtime.metadata_for_call(unrelated) is tool
