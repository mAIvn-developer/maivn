"""Safe projection tests for the public run-telemetry schema."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from maivn._internal.models import StreamEvent
from maivn.telemetry import RunTelemetryEvent
from maivn.telemetry._projection import project_stream_event

EXPECTED_DURATION_MS = 87


def _event(event_type: str, data: dict[str, object], *, position: int = 4) -> StreamEvent:
    return StreamEvent(position=position, event_type=event_type, data=data)


def test_unknown_future_event_fails_closed_to_safe_envelope() -> None:
    """Unknown payloads cannot silently expand the persistent telemetry surface."""
    event = _event(
        'future_secret_event',
        {
            'event_id': 'evt-future',
            'session_id': 'ses-safe',
            'thread_id': 'thr-safe',
            'ts': '2026-08-31T12:30:00Z',
            'payload': {
                'status': 'secret-status-from-unknown-payload',
                'message': 'MESSAGE-BODY-MUST-NOT-LEAK',
                'resolved_private_value': 'PRIVATE-VALUE-MUST-NOT-LEAK',
            },
        },
    )

    projected = project_stream_event(event)

    assert projected.model_dump(mode='json', exclude_none=True) == {
        'schema_version': '1',
        'event_name': 'future_secret_event',
        'event_kind': 'unknown',
        'position': 4,
        'timestamp': '2026-08-31T12:30:00Z',
        'event_id': 'evt-future',
        'session_id': 'ses-safe',
        'thread_id': 'thr-safe',
    }


@pytest.mark.parametrize(
    ('event_type', 'expected_kind'),
    [
        ('assignment_received', 'status'),
        ('assignment_completed', 'status'),
        ('progress_update', 'message'),
        ('model_routing', 'status'),
        ('status_message', 'message'),
        ('trigger.fired', 'status'),
        ('tool.dispatch_started', 'tool'),
        ('session.completed', 'run'),
    ],
)
def test_current_canonical_event_types_are_classified(
    event_type: str,
    expected_kind: str,
) -> None:
    """Current event-contract names do not fall into the future-event bucket."""
    projected = project_stream_event(_event(event_type, {'event_id': 'evt-current'}))

    assert projected.event_kind == expected_kind


def test_model_tool_completion_projects_a_terminal_tool_without_its_result() -> None:
    """Canonical model-tool completion creates safe tool lifecycle metadata."""
    event = _event(
        'model_tool_complete',
        {
            'event_id': 'evt-model-tool',
            'session_id': 'ses-1',
            'payload': {
                'tool_name': 'classify_document',
                'result': {'private': 'MODEL-TOOL-RESULT-MUST-NOT-LEAK'},
            },
        },
    )

    projected = project_stream_event(event)

    assert projected.event_kind == 'tool'
    assert projected.status == 'completed'
    assert projected.tool_call is not None
    assert projected.tool_call.name == 'classify_document'
    assert projected.tool_call.call_id == 'evt-model-tool'
    assert 'MODEL-TOOL-RESULT-MUST-NOT-LEAK' not in projected.model_dump_json()


def test_tool_start_projects_metadata_without_reading_arguments() -> None:
    """Tool identity is metadata while arguments remain structurally absent."""
    event = _event(
        'system_tool_start',
        {
            'event_id': 'evt-tool',
            'session_id': 'ses-1',
            'parent_session_id': 'ses-parent',
            'correlation_id': 'call-1',
            'ts': '2026-08-31T12:30:01Z',
            'payload': {
                'agent_name': 'Researcher',
                'swarm_name': 'Planning swarm',
                'tool_name': 'lookup',
                'tool_call': {
                    'call_id': 'call-1',
                    'spec_ref': {
                        'tool_id': 'lookup-v1',
                        'namespace': 'sdk',
                        'version': 'v1',
                    },
                    'arguments': {'account': '{_{account_number}_}'},
                },
            },
        },
    )

    projected = project_stream_event(event)

    assert projected.event_kind == 'tool'
    assert projected.status == 'started'
    assert projected.scope is not None
    assert projected.scope.agent_name == 'Researcher'
    assert projected.scope.swarm_name == 'Planning swarm'
    assert projected.tool_call is not None
    assert projected.tool_call.call_id == 'call-1'
    assert projected.tool_call.name == 'lookup'
    assert projected.tool_call.namespace == 'sdk'
    assert 'content' not in RunTelemetryEvent.model_fields
    assert 'account_number' not in projected.model_dump_json()


def test_final_projects_usage_status_without_response_or_result() -> None:
    """Final usage is safe metadata while response and result remain absent."""
    event = _event(
        'final',
        {
            'event_id': 'evt-final',
            'session_id': 'ses-1',
            'root_event_id': 'evt-root',
            'latency_ms': EXPECTED_DURATION_MS,
            'ts': '2026-08-31T12:30:02Z',
            'payload': {
                'message': {
                    'message_id': 'msg-1',
                    'role': 'assistant',
                    'content': 'Account {_{account_number}_} is active.',
                    'ts': '2026-08-31T12:30:02Z',
                },
                'result': {'account': '{_{account_number}_}'},
                'usage': {
                    'input_tokens': 11,
                    'output_tokens': 7,
                    'cache_read_tokens': 3,
                    'reasoning_tokens': 2,
                },
                'stop_reason': 'completed',
            },
        },
    )

    projected = project_stream_event(event)

    assert projected.event_kind == 'run'
    assert projected.status == 'completed'
    assert projected.duration_ms == EXPECTED_DURATION_MS
    assert projected.token_usage is not None
    assert projected.token_usage.model_dump() == {
        'input_tokens': 11,
        'output_tokens': 7,
        'cache_read_tokens': 3,
        'cache_creation_tokens': 0,
        'reasoning_tokens': 2,
        'total_tokens': 18,
    }
    serialized = projected.model_dump_json()
    assert 'content' not in RunTelemetryEvent.model_fields
    assert 'Account' not in serialized
    assert 'account_number' not in serialized


def test_public_models_are_frozen_and_reject_unknown_fields() -> None:
    """Integrators get a closed, immutable schema rather than payload bags."""
    event = RunTelemetryEvent(
        schema_version='1',
        event_name='future',
        event_kind='unknown',
        position=0,
    )

    with pytest.raises(ValidationError):
        RunTelemetryEvent.model_validate(
            {
                'schema_version': '1',
                'event_name': 'future',
                'event_kind': 'unknown',
                'position': 0,
                'arbitrary_payload': 'forbidden',
            }
        )

    with pytest.raises(ValidationError):
        event.__setattr__('status', 'mutated')
