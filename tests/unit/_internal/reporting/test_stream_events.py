"""Contract tests for the SDK's v1-visible stream-event projection."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from maivn._internal.compat.invocation import response_from_stream_events
from maivn._internal.event_message import message_from_event
from maivn._internal.models import StreamEvent
from maivn._internal.reporting.stream_events import StreamEventProjector


def test_served_result_uses_retained_envelope_metadata_without_changing_wire() -> None:
    """Served receipts retain message content with identity/time on the event envelope."""
    message = {'role': 'assistant', 'content': 'Order alpha received.'}
    data = {
        'type': 'final',
        'event_id': 'evt-served',
        'ts': '2026-09-08T18:42:13Z',
        'session_id': 'ses-served',
        'root_event_id': 'evt-root',
        'payload': {
            'message': message,
            'usage': {'input_tokens': 267, 'output_tokens': 22},
        },
    }
    event = StreamEvent(position=42, event_type='final', data=data)
    projected = StreamEventProjector().project(event)
    result = response_from_stream_events([projected])
    assert result.response == 'Order alpha received.'
    assert result.final_message.message_id == 'msg-event-evt-served'
    assert result.final_message.ts == data['ts']
    expected_total = 289
    assert projected.payload['token_usage']['total_tokens'] == expected_total
    assert projected.data == data
    assert 'message_id' not in message


@pytest.mark.parametrize('field', ['message_id', 'ts', 'role'])
def test_event_metadata_does_not_mask_invalid_explicit_message_fields(field: str) -> None:
    """Only omitted identity fields can use envelope metadata; invalid values still fail."""
    payload: dict[str, object] = {
        'message_id': 'msg-original',
        'ts': '2026-09-08T18:00:00Z',
        'role': 'assistant',
        'content': 'Result',
        field: None,
    }
    with pytest.raises(ValidationError):
        message_from_event(payload, {'event_id': 'evt-1', 'ts': '2026-09-08T19:00:00Z'})


def _event(
    event_type: str,
    payload: dict[str, object],
    *,
    position: int = 1,
) -> StreamEvent:
    """Build a canonical v2 stream event for projection tests."""
    return StreamEvent(
        position=position,
        event_type=event_type,
        data={'type': event_type, 'payload': payload},
    )


def test_status_chunks_expose_cumulative_streaming_content() -> None:
    """A first non-empty update is available immediately for TTFT measurement."""
    projector = StreamEventProjector()

    first = projector.project(
        _event('status_message_chunk', {'message_id': 'msg-1', 'content_delta': 'Hel'})
    )
    second = projector.project(
        _event('status_message_chunk', {'message_id': 'msg-1', 'content_delta': 'lo'}, position=2)
    )

    assert first.event_type == 'status_message_chunk'
    assert first.data['payload'] == {'message_id': 'msg-1', 'content_delta': 'Hel'}
    assert first.name == 'update'
    assert first.payload['streaming_content'] == 'Hel'
    assert second.payload['streaming_content'] == 'Hello'


def test_status_chunks_keep_cumulative_state_per_message() -> None:
    """Interleaved assistant messages never share cumulative streamed content."""
    projector = StreamEventProjector()

    events = (
        _event('status_message_chunk', {'message_id': 'left', 'content_delta': 'A'}),
        _event('status_message_chunk', {'message_id': 'right', 'content_delta': '1'}, position=2),
        _event('status_message_chunk', {'message_id': 'left', 'content_delta': 'B'}, position=3),
        _event('status_message_chunk', {'message_id': 'right', 'content_delta': '2'}, position=4),
    )

    assert [projector.project(event).payload['streaming_content'] for event in events] == [
        'A',
        '1',
        'AB',
        '12',
    ]


def test_final_and_error_events_expose_v1_terminal_payload_fields() -> None:
    """Terminal compatibility payloads use v1 response and error field names."""
    projector = StreamEventProjector()

    final = projector.project(
        _event(
            'final',
            {
                'message': {
                    'message_id': 'msg-1',
                    'role': 'assistant',
                    'content': 'Done.',
                    'ts': '2026-07-12T00:00:00Z',
                },
                'usage': {'input_tokens': 1_000, 'output_tokens': 234},
            },
        )
    )
    error = projector.project(
        _event('error', {'message': 'Provider failed', 'error_type': 'provider_error'})
    )

    assert final.payload['response'] == 'Done.'
    assert final.payload['responses'] == ['Done.']
    assert final.payload['token_usage'] == {
        'input_tokens': 1_000,
        'output_tokens': 234,
        'cache_read_tokens': 0,
        'cache_creation_tokens': 0,
        'reasoning_tokens': 0,
        'total_tokens': 1_234,
    }
    assert error.payload['error'] == 'Provider failed'
    assert error.payload['details'] == {
        'error_type': 'provider_error',
        'termination_reason': None,
    }


@pytest.mark.parametrize(
    ('canonical_name', 'compat_name'),
    [
        ('progress_update', 'progress_update'),
        ('system_tool_start', 'system_tool_start'),
        ('system_tool_chunk', 'system_tool_chunk'),
        ('system_tool_complete', 'system_tool_complete'),
        ('system_tool_error', 'system_tool_error'),
        ('future_event', 'future_event'),
    ],
)
def test_v1_visible_event_name_mapping_preserves_v2_envelopes(
    canonical_name: str,
    compat_name: str,
) -> None:
    """Only the v1-visible surface changes; internal canonical fields remain native."""
    source = _event(canonical_name, {'value': canonical_name})
    event = StreamEventProjector().project(source)

    assert event.event_type == canonical_name
    assert event.data is source.data
    assert event.name == compat_name
    assert event.payload == {'value': canonical_name}


def test_final_event_preserves_value_and_exposes_v1_defaults() -> None:
    """Final compatibility payloads preserve canonical values and add v1 defaults."""
    event = StreamEventProjector().project(_event('final', {'value': 'final'}))

    assert event.payload == {
        'value': 'final',
        'response': '',
        'responses': [],
    }


def test_error_event_preserves_value_and_exposes_v1_defaults() -> None:
    """Error compatibility payloads preserve canonical values and add v1 defaults."""
    event = StreamEventProjector().project(_event('error', {'value': 'error'}))

    assert event.payload == {
        'value': 'error',
        'error': 'Unknown error',
        'details': {
            'error_type': None,
            'termination_reason': None,
        },
    }


@pytest.mark.parametrize('code', ['provider_refusal_reasoning_extraction', 'tool_repeated_failure'])
def test_durable_error_code_survives_compatibility_projection(code: str) -> None:
    """A value-free durable error remains actionable without a provider message."""
    source = _event(
        'error',
        {
            'error_code': code,
            'error_class': 'LoopError',
            'termination_reason': 'refusal',
        },
    )
    projected = StreamEventProjector().project(source)
    assert projected.payload['error'] == code
    assert projected.payload['details']['error_type'] == 'LoopError'
    assert projected.payload['details']['termination_reason'] == 'refusal'
    assert projected.data is source.data
