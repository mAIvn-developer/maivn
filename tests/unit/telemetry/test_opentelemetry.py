# pyright: reportPrivateUsage=false
# ruff: noqa: SLF001 - bounded-retention tests inspect private adapter state.
"""OpenTelemetry adapter mapping tests with the real in-memory SDK exporter."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Literal, TypeAlias

from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter
from opentelemetry.trace import StatusCode

from maivn.telemetry import (
    RunTelemetryEvent,
    TokenUsageTelemetry,
    ToolCallTelemetry,
)
from maivn.telemetry.opentelemetry import OpenTelemetryAdapter

_START = datetime(2026, 9, 1, 12, 0, tzinfo=timezone.utc)
_TOOL_DURATION_MS = 25
_INPUT_TOKENS = 11
_OUTPUT_TOKENS = 7
_CACHE_READ_TOKENS = 3
_REASONING_TOKENS = 2
_TOTAL_TOKENS = _INPUT_TOKENS + _OUTPUT_TOKENS
_RETENTION_LIMIT = 1_024
_EventKind: TypeAlias = Literal['run', 'tool', 'message', 'status', 'unknown']


def _runtime() -> tuple[OpenTelemetryAdapter, InMemorySpanExporter, TracerProvider]:
    exporter = InMemorySpanExporter()
    provider = TracerProvider()
    provider.add_span_processor(SimpleSpanProcessor(exporter))
    adapter = OpenTelemetryAdapter(tracer=provider.get_tracer('test.maivn.telemetry'))
    return adapter, exporter, provider


def _event(  # noqa: PLR0913 - test builder mirrors the public event model.
    event_name: str,
    event_kind: _EventKind,
    *,
    timestamp: datetime,
    status: str | None = None,
    duration_ms: int | None = None,
    tool_call: ToolCallTelemetry | None = None,
    token_usage: TokenUsageTelemetry | None = None,
    session_id: str = 'ses-otel',
) -> RunTelemetryEvent:
    return RunTelemetryEvent(
        event_name=event_name,
        event_kind=event_kind,
        position=1,
        timestamp=timestamp,
        event_id=f'evt-{event_name}',
        session_id=session_id,
        thread_id='thr-otel',
        root_event_id='evt-root',
        correlation_id=tool_call.call_id if tool_call is not None else None,
        status=status,
        duration_ms=duration_ms,
        tool_call=tool_call,
        token_usage=token_usage,
    )


def _tool() -> ToolCallTelemetry:
    return ToolCallTelemetry(
        call_id='call-lookup',
        name='lookup',
        tool_type='function',
        namespace='sdk',
        version='v1',
    )


def test_run_and_tool_events_become_one_trace_with_usage_attributes() -> None:
    """One run root parents tool spans and records timing and token metadata."""
    adapter, exporter, provider = _runtime()
    adapter(
        _event(
            'system_tool_start',
            'tool',
            timestamp=_START,
            status='started',
            tool_call=_tool(),
        )
    )
    adapter(
        _event(
            'system_tool_complete',
            'tool',
            timestamp=_START + timedelta(milliseconds=_TOOL_DURATION_MS),
            status='completed',
            duration_ms=_TOOL_DURATION_MS,
            tool_call=_tool(),
        )
    )
    adapter(
        _event(
            'final',
            'run',
            timestamp=_START + timedelta(seconds=1),
            status='completed',
            token_usage=TokenUsageTelemetry(
                input_tokens=_INPUT_TOKENS,
                output_tokens=_OUTPUT_TOKENS,
                cache_read_tokens=_CACHE_READ_TOKENS,
                reasoning_tokens=_REASONING_TOKENS,
                total_tokens=_TOTAL_TOKENS,
            ),
        )
    )
    provider.force_flush()

    spans = {span.name: span for span in exporter.get_finished_spans()}
    run = spans['maivn.run']
    tool = spans['execute_tool lookup']

    assert run.attributes is not None
    assert tool.attributes is not None
    assert tool.context is not None
    assert run.context is not None
    assert tool.context.trace_id == run.context.trace_id
    assert tool.parent is not None
    assert tool.parent.span_id == run.context.span_id
    assert tool.attributes['gen_ai.tool.name'] == 'lookup'
    assert tool.attributes['gen_ai.tool.call.id'] == 'call-lookup'
    assert tool.attributes['gen_ai.operation.name'] == 'execute_tool'
    assert tool.attributes['maivn.tool.duration_ms'] == _TOOL_DURATION_MS
    assert run.attributes['gen_ai.usage.input_tokens'] == _INPUT_TOKENS
    assert run.attributes['gen_ai.usage.output_tokens'] == _OUTPUT_TOKENS
    assert run.attributes['maivn.usage.cache_read_tokens'] == _CACHE_READ_TOKENS
    assert run.attributes['maivn.usage.reasoning_tokens'] == _REASONING_TOKENS
    assert run.attributes['maivn.usage.total_tokens'] == _TOTAL_TOKENS
    assert run.status.status_code is StatusCode.OK


def test_completion_without_start_uses_reported_tool_duration() -> None:
    """Replay can synthesize a correctly timed child span from a completion."""
    adapter, exporter, provider = _runtime()
    adapter(
        _event(
            'system_tool_complete',
            'tool',
            timestamp=_START,
            status='completed',
            duration_ms=_TOOL_DURATION_MS,
            tool_call=_tool(),
        )
    )
    adapter(_event('final', 'run', timestamp=_START, status='completed'))
    provider.force_flush()

    tool = next(
        span for span in exporter.get_finished_spans() if span.name == 'execute_tool lookup'
    )
    assert tool.end_time is not None
    assert tool.start_time is not None
    assert tool.end_time - tool.start_time == _TOOL_DURATION_MS * 1_000_000
    assert tool.attributes is not None
    assert tool.attributes['gen_ai.operation.name'] == 'execute_tool'


def test_error_and_duplicate_terminal_events_end_the_run_once() -> None:
    """Terminal replay is idempotent and errors map to OTel error status."""
    adapter, exporter, provider = _runtime()
    terminal = _event('error', 'run', timestamp=_START, status='error')

    adapter(terminal)
    adapter(terminal)
    provider.force_flush()

    runs = [span for span in exporter.get_finished_spans() if span.name == 'maivn.run']
    assert len(runs) == 1
    assert runs[0].status.status_code is StatusCode.ERROR


def test_completed_run_idempotence_bookkeeping_is_bounded() -> None:
    """A process-wide adapter does not retain every completed session forever."""
    adapter, _exporter, _provider = _runtime()

    for index in range(_RETENTION_LIMIT + 1):
        adapter(
            _event(
                'final',
                'run',
                timestamp=_START,
                status='completed',
                session_id=f'ses-{index}',
            )
        )

    assert len(adapter._completed_runs) <= _RETENTION_LIMIT


def test_completed_tool_idempotence_bookkeeping_is_bounded_to_the_active_run() -> None:
    """Tool tombstones are bounded while active and released with their run."""
    adapter, _exporter, _provider = _runtime()

    for index in range(_RETENTION_LIMIT + 1):
        tool = ToolCallTelemetry(call_id=f'call-{index}', name='lookup')
        adapter(
            _event(
                'system_tool_complete',
                'tool',
                timestamp=_START,
                status='completed',
                tool_call=tool,
            )
        )

    assert len(adapter._runs['ses-otel'].completed_tools) <= _RETENTION_LIMIT

    adapter(_event('final', 'run', timestamp=_START, status='completed'))

    assert adapter._runs == {}
    assert adapter._tools == {}


def test_run_root_respects_the_callers_active_trace_context() -> None:
    """MAIvn traces join a developer's existing trace when one is active."""
    adapter, exporter, provider = _runtime()
    tracer = provider.get_tracer('host.application')

    with tracer.start_as_current_span('host.request') as host:
        adapter(_event('progress_update', 'status', timestamp=_START, status='running'))
        adapter(_event('final', 'run', timestamp=_START, status='completed'))
        assert host.get_span_context().is_valid
    provider.force_flush()

    spans = {span.name: span for span in exporter.get_finished_spans()}
    run = spans['maivn.run']
    host_span = spans['host.request']
    assert run.parent is not None
    assert host_span.context is not None
    assert run.parent.span_id == host_span.context.span_id


def test_shutdown_ends_incomplete_tool_and_run_spans() -> None:
    """Adapter shutdown leaves no in-memory spans open after interrupted runs."""
    adapter, exporter, provider = _runtime()
    adapter(
        _event(
            'system_tool_start',
            'tool',
            timestamp=_START,
            status='started',
            tool_call=_tool(),
        )
    )

    adapter.shutdown()
    provider.force_flush()

    assert {span.name for span in exporter.get_finished_spans()} == {
        'maivn.run',
        'execute_tool lookup',
    }
