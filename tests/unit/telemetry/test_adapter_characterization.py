"""Characterize telemetry listeners, projection edge cases and the OpenTelemetry adapter.

Spans are read back from the real in-memory OpenTelemetry SDK exporter, so the
assertions are on exactly what an application's exporter would receive.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import TYPE_CHECKING, Literal, TypeAlias

import pytest
from opentelemetry.sdk.trace import ReadableSpan, TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter
from opentelemetry.trace import StatusCode

from maivn._internal.models import StreamEvent
from maivn.telemetry import (
    RunTelemetryEvent,
    RunTelemetryScope,
    ToolCallTelemetry,
    register_listener,
)
from maivn.telemetry._registry import publish_stream_event, reset_listeners_for_testing
from maivn.telemetry.opentelemetry import OpenTelemetryAdapter

if TYPE_CHECKING:
    from collections.abc import Mapping

_START = datetime(2026, 9, 1, 12, 0, tzinfo=timezone.utc)
_START_NS = int(_START.timestamp()) * 1_000_000_000
_MS = 1_000_000
_EventKind: TypeAlias = Literal['run', 'tool', 'message', 'status', 'unknown']


def setup_function() -> None:
    """Start with no process-wide listeners."""
    reset_listeners_for_testing()


def teardown_function() -> None:
    """Leave no process-wide listeners behind."""
    reset_listeners_for_testing()


def _runtime() -> tuple[OpenTelemetryAdapter, InMemorySpanExporter]:
    exporter = InMemorySpanExporter()
    provider = TracerProvider()
    provider.add_span_processor(SimpleSpanProcessor(exporter))
    return OpenTelemetryAdapter(tracer=provider.get_tracer('test.characterization')), exporter


def _event(  # noqa: PLR0913 - mirrors the public event model's optional fields.
    name: str,
    kind: _EventKind,
    *,
    timestamp: datetime | None = _START,
    status: str | None = None,
    duration_ms: float | None = None,
    tool: ToolCallTelemetry | None = None,
    session_id: str | None = 'ses-char',
    root_event_id: str | None = None,
    event_id: str | None = None,
    scope: RunTelemetryScope | None = None,
    thread_id: str | None = None,
    parent_session_id: str | None = None,
) -> RunTelemetryEvent:
    return RunTelemetryEvent(
        event_name=name,
        event_kind=kind,
        position=3,
        timestamp=timestamp,
        event_id=event_id,
        session_id=session_id,
        thread_id=thread_id,
        parent_session_id=parent_session_id,
        root_event_id=root_event_id,
        status=status,
        duration_ms=duration_ms,
        scope=scope,
        tool_call=tool,
    )


def _spans(exporter: InMemorySpanExporter) -> dict[str, list[ReadableSpan]]:
    grouped: dict[str, list[ReadableSpan]] = {}
    for span in exporter.get_finished_spans():
        grouped.setdefault(span.name, []).append(span)
    return grouped


def _attributes(span: ReadableSpan) -> Mapping[str, object]:
    assert span.attributes is not None
    return dict(span.attributes)


# --- listener registry ----------------------------------------------------------------------


@pytest.mark.parametrize('listener', [None, 'listener', 42])
def test_register_listener_refuses_non_callables(listener: object) -> None:
    """Only callables can listen; nothing is registered otherwise."""
    with pytest.raises(TypeError, match=r'^listener must be callable$'):
        _ = register_listener(listener)  # pyright: ignore[reportArgumentType]


def test_subscription_reports_whether_it_is_closed() -> None:
    """`closed` flips once on close, and a closed listener receives nothing more."""
    received: list[str] = []
    subscription = register_listener(lambda event: received.append(event.event_name))
    assert subscription.closed is False

    publish_stream_event(StreamEvent(position=1, event_type='status', data={}))
    subscription.close()
    publish_stream_event(StreamEvent(position=2, event_type='status', data={}))

    assert subscription.closed is True
    assert received == ['status']


# --- projection edge cases ------------------------------------------------------------------


def _project(event_type: str, data: dict[str, object]) -> RunTelemetryEvent:
    received: list[RunTelemetryEvent] = []
    with register_listener(received.append):
        publish_stream_event(StreamEvent(position=5, event_type=event_type, data=data))
    assert len(received) == 1
    return received[0]


@pytest.mark.parametrize('ts', ['not-a-time', '', 17, None])
def test_unparseable_timestamps_are_dropped(ts: object) -> None:
    """A timestamp that is not ISO-8601 text becomes None rather than failing the run."""
    assert _project('status', {'ts': ts}).timestamp is None


def test_z_suffixed_timestamps_are_parsed_as_utc() -> None:
    """The trailing Z form is read as UTC."""
    assert _project('status', {'ts': '2026-09-01T12:00:00Z'}).timestamp == _START


@pytest.mark.parametrize(
    ('event_type', 'payload', 'expected'),
    [
        ('status', {'duration_ms': 12}, 12),
        ('status', {'duration_ms': -1}, None),
        ('status', {'duration_ms': True}, None),
        ('status', {'outcome': {'duration_ms': 9}}, None),
        ('system_tool_complete', {'name': 'lookup', 'outcome': {'duration_ms': 9}}, 9),
        ('system_tool_complete', {'name': 'lookup', 'outcome': {'duration_ms': -9}}, None),
    ],
)
def test_duration_comes_from_payload_and_only_tools_read_the_outcome(
    event_type: str, payload: dict[str, object], expected: float | None
) -> None:
    """Negative or boolean durations are dropped; outcome timing is a tool-only source."""
    assert _project(event_type, {'payload': payload}).duration_ms == expected


def test_latency_takes_precedence_over_payload_duration() -> None:
    """Envelope latency wins over any payload duration."""
    event = _project('status', {'latency_ms': 3.5, 'payload': {'duration_ms': 12}})
    assert event.duration_ms == 3.5  # noqa: PLR2004 - the reported latency


# --- OpenTelemetry adapter ------------------------------------------------------------------


def test_events_without_any_run_identity_are_ignored() -> None:
    """With no session, root or event id there is no run to attach a span to."""
    adapter, exporter = _runtime()
    adapter(_event('final', 'run', session_id=None, status='completed'))
    assert exporter.get_finished_spans() == ()


def test_run_identity_falls_back_from_session_to_root_to_event_id() -> None:
    """Root event id, then event id, key the run when there is no session."""
    adapter, exporter = _runtime()
    adapter(_event('status', 'status', session_id=None, root_event_id='root-1'))
    adapter(_event('final', 'run', session_id=None, root_event_id='root-1', status='completed'))
    adapter(_event('final', 'run', session_id=None, event_id='evt-9', status='completed'))
    assert len(_spans(exporter)['maivn.run']) == 2  # noqa: PLR2004 - two distinct runs


def test_run_span_carries_exactly_the_metadata_attributes() -> None:
    """Identity, scope, status and run duration are the whole attribute set of a run span."""
    adapter, exporter = _runtime()
    adapter(
        _event(
            'final',
            'run',
            status='completed',
            duration_ms=250,
            root_event_id='root-1',
            thread_id='thr-1',
            parent_session_id='ses-parent',
            scope=RunTelemetryScope(agent_name='triage', swarm_name='desk'),
        )
    )
    (run,) = _spans(exporter)['maivn.run']
    assert _attributes(run) == {
        'gen_ai.operation.name': 'invoke_agent',
        'maivn.telemetry.schema_version': '1',
        'maivn.run.session_id': 'ses-char',
        'gen_ai.conversation.id': 'thr-1',
        'maivn.run.root_event_id': 'root-1',
        'maivn.run.parent_session_id': 'ses-parent',
        'maivn.scope.agent.name': 'triage',
        'maivn.scope.swarm.name': 'desk',
        'maivn.event.name': 'final',
        'maivn.event.position': 3,
        'maivn.run.status': 'completed',
        'maivn.run.duration_ms': 250,
    }
    assert run.start_time == _START_NS - 250 * _MS
    assert run.end_time == _START_NS
    assert run.status.status_code is StatusCode.OK


def test_non_run_durations_and_missing_status_are_not_recorded_on_the_run() -> None:
    """A status event's duration is not the run's duration; an unknown status is UNSET."""
    adapter, exporter = _runtime()
    adapter(_event('status', 'status', duration_ms=40, scope=RunTelemetryScope(agent_name='a')))
    adapter(_event('session_complete', 'run'))
    (run,) = _spans(exporter)['maivn.run']
    attributes = _attributes(run)
    assert 'maivn.run.duration_ms' not in attributes
    assert 'maivn.run.status' not in attributes
    assert attributes['maivn.scope.agent.name'] == 'a'
    assert 'maivn.scope.swarm.name' not in attributes
    assert attributes['maivn.event.name'] == 'session_complete'
    assert run.status.status_code is StatusCode.UNSET


@pytest.mark.parametrize(
    ('status', 'code'),
    [
        ('failed', StatusCode.ERROR),
        ('error', StatusCode.ERROR),
        ('ok', StatusCode.OK),
        ('success', StatusCode.OK),
        ('completed', StatusCode.OK),
        ('cancelled', StatusCode.UNSET),
    ],
)
def test_run_status_maps_onto_otel_status(status: str, code: StatusCode) -> None:
    """Error words map to ERROR, success words to OK, anything else to UNSET."""
    adapter, exporter = _runtime()
    adapter(_event('session.completed', 'run', status=status))
    (run,) = _spans(exporter)['maivn.run']
    assert run.status.status_code is code


def test_run_end_closes_open_tools_at_the_run_end_time() -> None:
    """A tool still open when the run ends is closed with UNSET status at the run's end."""
    adapter, exporter = _runtime()
    tool = ToolCallTelemetry(call_id='call-1', name='lookup', tool_type='function')
    adapter(_event('system_tool_start', 'tool', status='started', tool=tool))
    adapter(_event('system_tool_start', 'tool', status='started', tool=tool))
    adapter(_event('final', 'run', status='completed', timestamp=_START + timedelta(seconds=2)))

    spans = _spans(exporter)
    (span,) = spans['execute_tool lookup']
    assert span.end_time == _START_NS + 2_000 * _MS
    assert span.status.status_code is StatusCode.UNSET
    assert _attributes(span) == {
        'gen_ai.operation.name': 'execute_tool',
        'gen_ai.tool.name': 'lookup',
        'maivn.telemetry.schema_version': '1',
        'gen_ai.tool.call.id': 'call-1',
        'gen_ai.tool.type': 'function',
    }


def test_a_completed_tool_is_not_reopened_by_late_events() -> None:
    """After its terminal event a tool call ignores further events for the same call id."""
    adapter, exporter = _runtime()
    tool = ToolCallTelemetry(call_id='call-1', name='lookup', namespace='sdk', version='v2')
    adapter(_event('system_tool_error', 'tool', status='error', duration_ms=5, tool=tool))
    adapter(_event('system_tool_start', 'tool', status='started', tool=tool))
    adapter(_event('system_tool_complete', 'tool', status='completed', tool=tool))
    adapter(_event('final', 'run', status='completed'))

    (span,) = _spans(exporter)['execute_tool lookup']
    assert span.status.status_code is StatusCode.ERROR
    assert _attributes(span)['maivn.tool.status'] == 'error'
    assert _attributes(span)['maivn.tool.duration_ms'] == 5  # noqa: PLR2004 - reported duration
    assert _attributes(span)['maivn.tool.namespace'] == 'sdk'
    assert _attributes(span)['maivn.tool.version'] == 'v2'


def test_tool_status_words_alone_make_an_event_terminal() -> None:
    """A tool event with status `failed` ends the tool even under a non-terminal name."""
    adapter, exporter = _runtime()
    tool = ToolCallTelemetry(name='lookup')
    adapter(_event('tool_event', 'tool', status='failed', tool=tool, event_id='evt-1'))
    (span,) = _spans(exporter)['execute_tool lookup']
    assert span.status.status_code is StatusCode.ERROR
    assert 'gen_ai.tool.call.id' not in _attributes(span)


def test_events_without_timestamps_use_the_tracer_clock() -> None:
    """With no timestamp, start and end times come from the tracer, not the event."""
    adapter, exporter = _runtime()
    before = datetime.now(timezone.utc)
    adapter(_event('final', 'run', timestamp=None, duration_ms=1_000_000, status='completed'))
    (run,) = _spans(exporter)['maivn.run']
    assert run.start_time is not None
    assert run.start_time >= int(before.timestamp() * 1_000_000_000) - 1_000_000
    assert run.end_time is not None
    assert run.end_time >= run.start_time


def test_naive_timestamps_are_read_as_utc() -> None:
    """A timestamp without a zone is treated as UTC, not local time."""
    adapter, exporter = _runtime()
    naive = datetime(2026, 9, 1, 12, 0)  # noqa: DTZ001 - the naive input under test
    adapter(_event('final', 'run', timestamp=naive, status='completed'))
    (run,) = _spans(exporter)['maivn.run']
    assert (run.start_time, run.end_time) == (_START_NS, _START_NS)


def test_shutdown_of_an_idle_adapter_is_a_no_op_and_completed_runs_stay_closed() -> None:
    """Shutdown with nothing open exports nothing; a finished run is never reopened."""
    adapter, exporter = _runtime()
    adapter.shutdown()
    assert exporter.get_finished_spans() == ()
    adapter(_event('status', 'status'))
    adapter.shutdown()
    adapter(_event('status', 'status'))
    assert len(_spans(exporter)['maivn.run']) == 1


def test_ending_one_run_leaves_another_runs_tools_open() -> None:
    """Concurrent runs are independent: one run's end closes only its own tools."""
    adapter, exporter = _runtime()
    first = ToolCallTelemetry(call_id='call-a', name='alpha')
    second = ToolCallTelemetry(call_id='call-b', name='beta')
    adapter(_event('system_tool_start', 'tool', status='started', tool=first, session_id='ses-a'))
    adapter(_event('system_tool_start', 'tool', status='started', tool=second, session_id='ses-b'))
    adapter(_event('final', 'run', status='completed', session_id='ses-a'))

    assert set(_spans(exporter)) == {'maivn.run', 'execute_tool alpha'}
    adapter(_event('final', 'run', status='completed', session_id='ses-b'))
    assert set(_spans(exporter)) == {'maivn.run', 'execute_tool alpha', 'execute_tool beta'}
    assert len(_spans(exporter)['maivn.run']) == 2  # noqa: PLR2004 - one span per run
