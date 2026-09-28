"""Regression coverage for canonical data-plane tool lifecycle metadata."""

from __future__ import annotations

import asyncio
from typing import cast

from maivn._internal.models import StreamEvent
from maivn.events import (
    EventBridge,
    NormalizedStreamState,
    RawSSEEvent,
    forward_normalized_event,
    normalize_stream_event,
)

EXPECTED_DURATION_MS = 8500


class _CompletionReporter:
    def __init__(self) -> None:
        self.calls: list[tuple[str, int | None, object | None]] = []

    def report_tool_complete(
        self,
        event_id: str,
        elapsed_ms: int | None = None,
        result: object | None = None,
    ) -> None:
        self.calls.append((event_id, elapsed_ms, result))


class _AssignmentReporter:
    def __init__(self) -> None:
        self.calls: list[dict[str, object]] = []

    def report_agent_assignment(self, **kwargs: object) -> None:
        self.calls.append(kwargs)


class _ToolStartReporter:
    def __init__(self) -> None:
        self.calls: list[dict[str, object]] = []

    def report_tool_start(  # noqa: PLR0913 - mirrors the reporter signature.
        self,
        tool_name: str,
        event_id: str,
        tool_type: str | None = None,
        agent_name: str | None = None,
        tool_args: dict[str, object] | None = None,
        swarm_name: str | None = None,
        private_data_keys: list[str] | None = None,
    ) -> None:
        self.calls.append(
            {
                'tool_name': tool_name,
                'event_id': event_id,
                'tool_type': tool_type,
                'agent_name': agent_name,
                'tool_args': tool_args,
                'swarm_name': swarm_name,
                'private_data_keys': private_data_keys,
            }
        )


def test_system_tool_completion_preserves_correlation_and_duration() -> None:
    """Canonical envelope timing must survive the SDK compatibility projection."""
    event = StreamEvent(
        position=7,
        event_type='system_tool_complete',
        data={
            'event_id': 'evt-7',
            'type': 'system_tool_complete',
            'session_id': 'ses-1',
            'actor': 'tool:compose_artifact',
            'correlation_id': 'call-1',
            'latency_ms': EXPECTED_DURATION_MS,
            'payload': {
                'stage': 'tool_complete',
                'tool_name': 'compose_artifact',
                'outcome': {
                    'call_id': 'call-1',
                    'status': 'ok',
                    'result': {'artifact': 'query'},
                    'duration_ms': EXPECTED_DURATION_MS,
                },
            },
            'ts': '2026-07-21T12:00:00Z',
        },
    )

    normalized = normalize_stream_event(cast('RawSSEEvent', event))

    assert len(normalized) == 1
    completion = normalized[0]
    assert completion.tool is not None
    assert completion.tool.id == 'call-1'
    assert completion.tool.name == 'compose_artifact'
    assert completion.tool.result == {'artifact': 'query'}
    assert completion.model_extra is not None
    assert completion.model_extra['duration_ms'] == EXPECTED_DURATION_MS

    reporter = _CompletionReporter()
    asyncio.run(forward_normalized_event(completion, reporter=reporter))
    assert reporter.calls == [('call-1', EXPECTED_DURATION_MS, {'artifact': 'query'})]


def _system_tool_error_event(error: dict[str, object]) -> StreamEvent:
    return StreamEvent(
        position=8,
        event_type='system_tool_error',
        data={
            'event_id': 'evt-8',
            'type': 'system_tool_error',
            'session_id': 'ses-1',
            'actor': 'tool:Motor',
            'correlation_id': 'call-err-1',
            'latency_ms': 0,
            'payload': {
                'stage': 'tool_error',
                'tool_name': 'Motor',
                'outcome': {
                    'call_id': 'call-err-1',
                    'status': 'error',
                    'duration_ms': 0,
                    'error': error,
                },
            },
            'ts': '2026-07-21T12:00:01Z',
        },
    )


def test_durable_tool_error_surfaces_its_code_instead_of_unknown_error() -> None:
    """A message-free durable error names its stable code, never "Unknown error"."""
    normalized = normalize_stream_event(
        cast(
            'RawSSEEvent',
            _system_tool_error_event(
                {
                    'error_code': 'sdk_tool_error',
                    'error_class': 'ToolOutcomeError',
                    'retryable': False,
                }
            ),
        )
    )

    assert len(normalized) == 1
    failed = normalized[0]
    assert failed.tool is not None
    assert failed.tool.status == 'failed'
    assert failed.tool.error == 'sdk_tool_error'


def test_durable_tool_error_prefers_its_carried_message_over_the_code() -> None:
    """A value-free message carried by the durable error wins over the bare code."""
    normalized = normalize_stream_event(
        cast(
            'RawSSEEvent',
            _system_tool_error_event(
                {
                    'error_code': 'sdk_tool_validation_error',
                    'error_class': 'ToolOutcomeError',
                    'retryable': False,
                    'message': 'invalid arguments: max_power_w: missing',
                }
            ),
        )
    )

    assert len(normalized) == 1
    failed = normalized[0]
    assert failed.tool is not None
    assert failed.tool.error == 'invalid arguments: max_power_w: missing'


def test_canonical_system_tool_start_preserves_call_identity_type_and_arguments() -> None:
    """Canonical starts must correlate with completion and retain typed inputs."""
    event = StreamEvent(
        position=6,
        event_type='system_tool_start',
        data={
            'event_id': 'evt-6',
            'type': 'system_tool_start',
            'session_id': 'ses-1',
            'correlation_id': 'call-1',
            'payload': {
                'stage': 'tool_start',
                'tool_name': 'compose_artifact',
                'tool_call': {
                    'call_id': 'call-1',
                    'spec_ref': {
                        'tool_id': 'system-compose-artifact-v1',
                        'namespace': 'system',
                        'version': 'v1',
                    },
                    'arguments': {'prompt': 'draft the query'},
                },
            },
            'ts': '2026-07-21T11:59:51Z',
        },
    )

    normalized = normalize_stream_event(
        cast('RawSSEEvent', event),
        state=NormalizedStreamState(),
        tool_metadata_map={
            'compose_artifact': {'tool_name': 'compose_artifact', 'tool_type': 'system'}
        },
    )

    assert len(normalized) == 1
    start = normalized[0]
    assert start.tool is not None
    assert start.tool.id == 'call-1'
    assert start.tool.name == 'compose_artifact'
    assert start.tool.type == 'system'
    assert start.tool.args == {'prompt': 'draft the query'}


def test_canonical_system_tool_start_preserves_private_data_field_names() -> None:
    """Studio may disclose injected field names, but never their private values."""
    event = StreamEvent(
        position=3,
        event_type='system_tool_start',
        data={
            'event_id': 'evt-private-tool',
            'type': 'system_tool_start',
            'session_id': 'ses-private',
            'correlation_id': 'call-private',
            'payload': {
                'stage': 'tool_start',
                'tool_name': 'hardware_scanner',
                'tool_call': {
                    'call_id': 'call-private',
                    'spec_ref': {
                        'tool_id': 'hardware_scanner',
                        'namespace': 'sdk',
                        'version': 'v1',
                    },
                    'arguments': {},
                },
            },
            'ts': '2026-07-24T12:00:00Z',
        },
    )

    start = normalize_stream_event(
        cast('RawSSEEvent', event),
        state=NormalizedStreamState(),
        tool_metadata_map={
            'hardware_scanner': {
                'tool_name': 'hardware_scanner',
                'private_data_keys': ['serial_number'],
            }
        },
    )[0]

    assert start.tool is not None
    assert start.tool.model_extra is not None
    assert start.tool.model_extra['private_data_keys'] == ['serial_number']
    assert start.model_extra is not None
    assert start.model_extra['private_data_keys'] == ['serial_number']


def test_normalized_tool_event_accepts_late_private_data_metadata() -> None:
    """Studio metadata enriches canonical SDK events without exposing values."""
    event = StreamEvent(
        position=4,
        event_type='tool_event',
        data={'type': 'tool_event', 'payload': {}},
        compat_name='tool_event',
        compat_payload={
            'contract_version': 'v1',
            'event_name': 'tool_event',
            'event_kind': 'tool',
            'tool_id': 'call-private',
            'tool_name': 'hardware_scanner',
            'tool_type': 'func',
            'status': 'executing',
            'args': {},
            'tool': {
                'id': 'call-private',
                'name': 'hardware_scanner',
                'type': 'func',
                'status': 'executing',
                'args': {},
            },
        },
    )

    start = normalize_stream_event(
        cast('RawSSEEvent', event),
        tool_metadata_map={
            'hardware_scanner': {
                'tool_name': 'hardware_scanner',
                'private_data_keys': ['serial_number'],
            }
        },
    )[0]

    assert start.tool is not None
    assert start.tool.model_extra is not None
    assert start.tool.model_extra['private_data_keys'] == ['serial_number']
    assert start.model_extra is not None
    assert start.model_extra['private_data_keys'] == ['serial_number']


def test_normalized_tool_start_forwards_private_data_field_names() -> None:
    """Reporter forwarding must retain safe private field names for Studio."""
    event = StreamEvent(
        position=4,
        event_type='tool_event',
        data={'type': 'tool_event', 'payload': {}},
        compat_name='tool_event',
        compat_payload={
            'contract_version': 'v1',
            'event_name': 'tool_event',
            'event_kind': 'tool',
            'tool_id': 'call-private',
            'tool_name': 'hardware_scanner',
            'tool_type': 'func',
            'status': 'executing',
            'args': {},
            'private_data_keys': ['serial_number'],
            'tool': {
                'id': 'call-private',
                'name': 'hardware_scanner',
                'type': 'func',
                'status': 'executing',
                'args': {},
                'private_data_keys': ['serial_number'],
            },
        },
    )
    start = normalize_stream_event(cast('RawSSEEvent', event))[0]
    reporter = _ToolStartReporter()

    asyncio.run(forward_normalized_event(start, reporter=reporter))

    assert reporter.calls == [
        {
            'tool_name': 'hardware_scanner',
            'event_id': 'call-private',
            'tool_type': 'func',
            'agent_name': None,
            'tool_args': {},
            'swarm_name': None,
            'private_data_keys': ['serial_number'],
        }
    ]


def test_canonical_tool_lifecycle_preserves_nested_agent_attribution() -> None:
    """A delegated tool stays attributable to its child agent through normalization."""
    state = NormalizedStreamState()
    common = {
        'event_id': 'evt-child-tool',
        'session_id': 'ses-child',
        'parent_session_id': 'ses-parent',
        'correlation_id': 'call-child-tool',
        'ts': '2026-07-21T12:00:00Z',
    }
    start_event = StreamEvent(
        position=9,
        event_type='system_tool_start',
        data={
            **common,
            'type': 'system_tool_start',
            'payload': {
                'stage': 'tool_start',
                'agent_name': 'Equity Analyst',
                'tool_name': 'market_fetch',
                'tool_call': {
                    'call_id': 'call-child-tool',
                    'spec_ref': {'tool_id': 'market-fetch', 'namespace': 'mcp', 'version': 'v1'},
                    'arguments': {'ticker': 'AAPL'},
                },
            },
        },
    )
    complete_event = StreamEvent(
        position=10,
        event_type='system_tool_complete',
        data={
            **common,
            'type': 'system_tool_complete',
            'payload': {
                'stage': 'tool_complete',
                'agent_name': 'Equity Analyst',
                'tool_name': 'market_fetch',
                'outcome': {
                    'call_id': 'call-child-tool',
                    'status': 'ok',
                    'result': {'price': 100},
                    'duration_ms': 4,
                },
            },
        },
    )

    started = normalize_stream_event(cast('RawSSEEvent', start_event), state=state)[0]
    completed = normalize_stream_event(cast('RawSSEEvent', complete_event), state=state)[0]

    assert started.scope is not None
    assert completed.scope is not None
    assert started.scope.type == 'agent'
    assert completed.scope.type == 'agent'
    assert started.scope.name == 'Equity Analyst'
    assert completed.scope.name == 'Equity Analyst'


def test_canonical_progress_update_projects_swarm_member_assignment() -> None:
    """Parent-session swarm member lifecycle must reach Studio's Inspector."""
    event = StreamEvent(
        position=8,
        event_type='progress_update',
        data={
            'event_id': 'evt-8',
            'type': 'progress_update',
            'session_id': 'ses-1',
            'payload': {
                'stage': 'swarm_agent',
                'action_type': 'swarm_agent',
                'action_id': 'action-research',
                'action_name': 'Member Research Agent',
                'status': 'completed',
                'use_as_final_output': True,
                'result': {'response': 'Research complete.'},
            },
            'ts': '2026-07-21T12:00:01Z',
        },
    )

    normalized = normalize_stream_event(
        cast('RawSSEEvent', event),
        default_swarm_name='Member Planning Swarm',
    )

    assert len(normalized) == 1
    assignment = normalized[0]
    assert assignment.event_name == 'agent_assignment'
    assert assignment.assignment is not None
    assert assignment.assignment.id == 'action-research'
    assert assignment.assignment.agent_name == 'Member Research Agent'
    assert assignment.assignment.swarm_name == 'Member Planning Swarm'
    assert assignment.assignment.status == 'completed'
    assert assignment.assignment.result == {'response': 'Research complete.'}
    assert assignment.model_extra is not None
    assert assignment.model_extra['use_as_final_output'] is True

    reporter = _AssignmentReporter()
    asyncio.run(forward_normalized_event(assignment, reporter=reporter))
    assert reporter.calls[0]['use_as_final_output'] is True

    async def forward_to_bridge() -> dict[str, object]:
        bridge = EventBridge('ses-1')
        await forward_normalized_event(assignment, bridge=bridge)
        return bridge.stream_queue_get_nowait().data

    bridge_data = asyncio.run(forward_to_bridge())
    assert bridge_data['use_as_final_output'] is True
