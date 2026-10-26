"""Characterize response records, memory/resource guards and bound-thread exclusivity."""

from __future__ import annotations

import asyncio
from http import HTTPStatus
from typing import TYPE_CHECKING, Any

import httpx
import pytest
from maivn_contracts.messages import Message
from pydantic import AnyUrl, ValidationError

from maivn import Client, ClientConfig
from maivn._internal.models import InvokeResponse, RunOptions, StreamEvent, tool_card_id
from maivn._internal.resources import ResourceRecord
from maivn._internal.thread import BoundThread

if TYPE_CHECKING:
    from collections.abc import AsyncIterator, Iterator


def _final_message(**update: object) -> dict[str, object]:
    message: dict[str, object] = {
        'message_id': 'msg-final',
        'role': 'assistant',
        'content': 'done',
        'ts': '2026-07-09T12:00:00Z',
    }
    message.update(update)
    return message


def _invoke_payload(**update: object) -> dict[str, Any]:
    payload: dict[str, Any] = {
        'final_message': _final_message(),
        'session_id': 'ses-1',
        'root_event_id': 'evt-root',
        'event_positions': [1, 2],
    }
    payload.update(update)
    return payload


def _artifact() -> dict[str, object]:
    return {
        'artifact_id': 'artifact-sdk',
        'logical_output_id': 'report',
        'revision': 1,
        'kind': 'image',
        'mime_type': 'image/png',
        'created_at': '2026-08-11T12:00:00Z',
        'effective_retention': {
            'policy_snapshot_id': 'policy-sdk',
            'retention_class': 'artifact-30d',
            'expires_at': '2026-09-10T12:00:00Z',
        },
        'custody': 'ordinary',
        'state': 'available',
        'display_filename': 'report.png',
        'size_bytes': 13,
        'sha256': '0' * 64,
        'producer': {
            'producer_class': 'hosted_tool',
            'producer_id': 'system-create-artifact',
            'root_invocation_id': 'invocation-sdk',
            'session_id': 'ses-1',
            'call_id': 'call-create-artifact',
        },
        'validation_receipt': {
            'receipt_id': 'receipt-sdk',
            'status': 'validated',
            'validator_profile': 'image-validator-v1',
            'validator_version': '1.0.0',
        },
        'safe_preview': {'kind': 'image', 'width_pixels': 4, 'height_pixels': 4},
        'retrieval_action': {'relation': 'artifact.download_authorization'},
    }


# --- models ------------------------------------------------------------------------


def test_invoke_response_defaults_from_a_minimal_payload() -> None:
    """Optional members default to empty values and no thread id."""
    response = InvokeResponse.from_payload(_invoke_payload())
    assert response.thread_id is None
    assert response.usage == {}
    assert response.stop_reason is None
    assert response.tool_call_count == 0
    assert response.tool_names == []
    assert response.artifacts == ()
    assert response.responses == ['done']


def test_invoke_response_reads_thread_id_from_run_config() -> None:
    """A missing top-level thread id falls back to the echoed run config."""
    response = InvokeResponse.from_payload(
        _invoke_payload(thread_id='', run_config={'thread_id': 'thr-run-config'})
    )
    assert response.thread_id == 'thr-run-config'
    top_level = InvokeResponse.from_payload(
        _invoke_payload(thread_id='thr-top', run_config={'thread_id': 'thr-run-config'})
    )
    assert top_level.thread_id == 'thr-top'
    not_a_mapping = InvokeResponse.from_payload(_invoke_payload(run_config='thr'))
    assert not_a_mapping.thread_id is None


def test_invoke_response_prefers_payload_artifacts_over_message_refs() -> None:
    """Payload-level artifact refs are validated and win over the message's refs."""
    response = InvokeResponse.from_payload(
        _invoke_payload(
            artifact_refs=[_artifact()],
            final_message=_final_message(artifact_refs=[]),
            stop_reason='end_turn',
            tool_call_count=2,
            tool_names=['a', 1],
            assistant_id='',
        )
    )
    assert [ref.artifact_id for ref in response.artifacts] == ['artifact-sdk']
    assert response.stop_reason == 'end_turn'
    assert response.tool_call_count == 2  # noqa: PLR2004 - echoed count.
    assert response.tool_names == ['a', '1']
    assert response.assistant_id is None


def test_invoke_response_without_text_has_no_v1_responses() -> None:
    """A non-text or empty final message yields an empty v1 responses list."""
    blocks = InvokeResponse.from_payload(
        _invoke_payload(final_message=_final_message(content=[{'type': 'text', 'text': 'x'}]))
    )
    assert blocks.response == ''
    assert blocks.responses == []
    empty = InvokeResponse(
        final_message=Message.model_validate(_final_message()),
        session_id='s',
        root_event_id='r',
        event_positions=[1],
        response='',
    )
    assert empty.responses == []


def test_stream_event_validates_its_canonical_event_envelope() -> None:
    """The event property validates the stored v2 envelope."""
    data = {
        'event_id': 'evt-1',
        'ordinal': 'ord-1',
        'type': 'status_message_chunk',
        'session_id': 'ses-1',
        'root_event_id': 'evt-root',
        'actor': 'system',
        'mode': 'hosted',
        'trigger_chain_depth': 0,
        'job_depth': 0,
        'job_index': 0,
        'payload': {'content_delta': 'hi'},
        'ts': '2026-07-04T12:00:00Z',
    }
    event = StreamEvent(position=1, event_type='status_message_chunk', data=data)
    assert event.event.session_id == 'ses-1'
    assert event.payload == {'content_delta': 'hi'}


@pytest.mark.parametrize(
    ('payload', 'expected'),
    [
        ({'assignment_id': ' a-1 ', 'tool_id': 't'}, 'a-1'),
        ({'assignment_id': '  ', 'tool_id': 't-1'}, 't-1'),
        ({'correlation_id': 'c-1', 'tool_call': {'call_id': 'call'}}, 'c-1'),
        ({'tool_call': {'call_id': 'call-1'}}, 'call-1'),
        ({'tool_call': 'not-a-mapping', 'tool_id': 7}, None),
        ({}, None),
    ],
)
def test_tool_card_id_precedence(payload: dict[str, Any], expected: str | None) -> None:
    """Assignment, tool, correlation then call id; blank or non-strings are skipped."""
    assert tool_card_id(payload) == expected


# --- memory and resources ----------------------------------------------------------


def _client(seen: list[httpx.Request], body: object) -> Client:
    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(HTTPStatus.OK, json=body)

    return Client(
        config=ClientConfig(api_key='test-key', base_url=AnyUrl('http://testserver')),
        transport=httpx.MockTransport(handler),
    )


@pytest.mark.parametrize('collection', ['skills', 'insights'])
def test_memory_list_without_query_sends_only_the_default_limit(collection: str) -> None:
    """Listing without a query sends limit=16 and no query parameter."""
    seen: list[httpx.Request] = []
    client = _client(seen, {'items': []})
    facade = getattr(client.memory, collection)
    assert facade.list() == ()
    assert [(request.url.path, dict(request.url.params)) for request in seen] == [
        (f'/v1/memory/{collection}', {'limit': '16'})
    ]


def test_memory_enrichments_refuse_an_empty_session_without_a_request() -> None:
    """An empty session id is rejected locally before any HTTP call."""
    seen: list[httpx.Request] = []
    client = _client(seen, {'items': []})
    with pytest.raises(ValueError, match=r'^session_id must not be empty$'):
        client.memory.enrichments.list('')
    assert seen == []


def test_memory_enrichment_session_ids_are_path_escaped() -> None:
    """Session ids are percent-encoded as one path segment."""
    seen: list[httpx.Request] = []
    client = _client(seen, {'items': []})
    assert client.memory.enrichments.list('ses/1 x') == ()
    assert seen[0].url.raw_path == b'/v1/memory/enrichments/ses%2F1%20x'


def test_resource_upload_refuses_empty_content_without_a_request() -> None:
    """Empty upload bytes are rejected locally with a fixed message."""
    seen: list[httpx.Request] = []
    client = _client(seen, {})
    with pytest.raises(ValueError, match=r'^resource content must not be empty$'):
        client.resources.upload(b'', media_type='text/plain', name='empty.txt')
    assert seen == []


def test_resource_record_revalidates_an_existing_record_unchanged() -> None:
    """Validating an existing record instance bypasses the viewer-metadata filter."""
    record = ResourceRecord.model_validate(
        {
            'memory_id': 'resource-sdk',
            'resource_id': 'resource-sdk',
            'resource_thread_id': 'rth-0123456789abcdef0123456789abcdef',
            'scope': {
                'project_id': 'project-sdk',
                'organization_id': 'org-sdk',
                'sharing_scope': 'project',
            },
            'name': 'renewal.txt',
            'media_type': 'text/plain',
            'content_hash': '0' * 64,
            'size_bytes': 17,
            'status': 'registered',
            'processing_status': 'pending',
            'binding_type': 'portal',
            'deduplicated': False,
            'created_at': '2026-07-05T12:00:00Z',
            'updated_at': '2026-07-05T12:00:00Z',
        }
    )
    assert ResourceRecord.model_validate(record) == record


def test_resource_record_rejects_non_mapping_input() -> None:
    """Non-mapping input passes the viewer filter untouched and fails validation."""
    with pytest.raises(ValidationError) as caught:
        ResourceRecord.model_validate(['not', 'a', 'record'])
    assert [error['type'] for error in caught.value.errors()] == ['model_type']


# --- bound thread ------------------------------------------------------------------


def _event(position: int, session_id: str | None = None) -> StreamEvent:
    data: dict[str, object] = {'payload': {}}
    if session_id is not None:
        data['session_id'] = session_id
    return StreamEvent(position=position, event_type='progress', data=data)


class _ListOwner:
    """Owner whose streams are plain iterators without close methods."""

    def __init__(self) -> None:
        self.invocations: list[RunOptions | None] = []

    def invoke(self, messages: object, *, options: RunOptions | None, **_kwargs: object) -> object:
        self.invocations.append(options)
        return {'messages': messages}

    def stream(
        self, _messages: object, *, options: RunOptions | None, **_kwargs: object
    ) -> Iterator[StreamEvent]:
        self.invocations.append(options)
        return iter([_event(3, 'ses-a'), _event(5)])

    def astream(
        self, _messages: object, *, options: RunOptions | None, **_kwargs: object
    ) -> AsyncIterator[StreamEvent]:
        self.invocations.append(options)
        return _PlainAsyncIterator([_event(7, 'ses-b')])

    def thread_events(self, _thread_id: str, *, options: RunOptions) -> Iterator[StreamEvent]:
        self.invocations.append(options)
        return iter([_event(options.resume_from_position + 1)])

    def athread_events(self, _thread_id: str, *, options: RunOptions) -> AsyncIterator[StreamEvent]:
        self.invocations.append(options)
        return _PlainAsyncIterator([_event(options.resume_from_position + 1)])


class _PlainAsyncIterator:
    def __init__(self, events: list[StreamEvent]) -> None:
        self._events = list(events)

    def __aiter__(self) -> _PlainAsyncIterator:
        return self

    async def __anext__(self) -> StreamEvent:
        if not self._events:
            raise StopAsyncIteration
        return self._events.pop(0)


def test_bound_thread_refuses_a_send_while_a_stream_is_active() -> None:
    """Only one send or stream may run at a time on one bound thread."""
    owner = _ListOwner()
    thread = BoundThread(owner, 'thr-1')
    stream = thread.stream('first')
    assert next(stream).position == 3  # noqa: PLR2004 - first event position.
    with pytest.raises(
        RuntimeError, match=r'^a BoundThread permits only one active send or stream operation$'
    ):
        thread.send('second')
    assert [event.position for event in stream] == [5]
    assert thread.send('third') == {'messages': 'third'}


def test_bound_thread_streams_plain_iterators_without_close_methods() -> None:
    """Sources lacking close/aclose still stream, and replay resumes after the last event."""
    owner = _ListOwner()
    thread = BoundThread(owner, 'thr-1')
    assert [event.position for event in thread.stream('hello')] == [3, 5]
    assert [event.position for event in thread.events()] == [6]

    async def collect() -> tuple[list[int], list[int]]:
        streamed = [event.position async for event in thread.astream('again')]
        replayed = [event.position async for event in thread.aevents()]
        return streamed, replayed

    assert asyncio.run(collect()) == ([7], [8])
    options = owner.invocations[-1]
    assert options is not None
    assert options.thread_id == 'thr-1'
    assert options.resume_from_position == 7  # noqa: PLR2004 - last observed position.
