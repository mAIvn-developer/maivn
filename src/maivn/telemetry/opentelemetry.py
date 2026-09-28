"""Optional OpenTelemetry adapter for the public metadata-only event contract."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from threading import RLock
from typing import TYPE_CHECKING

try:
    from opentelemetry import trace
    from opentelemetry.trace import SpanKind, Status, StatusCode, set_span_in_context
except ModuleNotFoundError as exc:
    if exc.name == 'opentelemetry' or (exc.name or '').startswith('opentelemetry.'):
        message = (
            'The mAIvn OpenTelemetry adapter requires the optional OpenTelemetry API. '
            'Install it with: pip install maivn[otel]'
        )
        raise ModuleNotFoundError(message, name=exc.name) from exc
    raise

if TYPE_CHECKING:
    from opentelemetry.trace import Span, Tracer

    from ._models import RunTelemetryEvent, TokenUsageTelemetry, ToolCallTelemetry

_INSTRUMENTATION_NAME = 'maivn.telemetry'
_COMPLETED_RETENTION_LIMIT = 1_024


@dataclass(slots=True)
class _RunState:
    span: Span
    completed_tools: dict[str, None] = field(default_factory=dict)


@dataclass(slots=True)
class _ToolState:
    span: Span
    run_key: str


class OpenTelemetryAdapter:
    """Map metadata-only mAIvn run events to ordinary OpenTelemetry spans.

    The adapter performs only in-memory tracer API calls. It does not install a
    provider, span processor, exporter, worker, or buffering policy. Configure
    those in the application; use a batch span processor when exporter I/O must
    stay off the synchronous mAIvn listener path.
    """

    def __init__(self, *, tracer: Tracer | None = None) -> None:
        """Create an adapter using an application tracer or the global provider."""
        self._tracer = tracer or trace.get_tracer(_INSTRUMENTATION_NAME)
        self._runs: dict[str, _RunState] = {}
        self._tools: dict[tuple[str, str], _ToolState] = {}
        self._completed_runs: dict[str, None] = {}
        self._lock = RLock()

    def __call__(self, event: RunTelemetryEvent) -> None:
        """Consume one event synchronously as a :class:`TelemetryListener`."""
        run_key = _run_key(event)
        if run_key is None:
            return
        with self._lock:
            if run_key in self._completed_runs:
                return
            run = self._ensure_run(run_key, event)
            self._apply_run_metadata(run.span, event)
            if event.tool_call is not None:
                self._apply_tool_event(run_key, run, event)
            if event.token_usage is not None:
                _apply_usage(run.span, event.token_usage)
            if _is_run_terminal(event):
                self._end_run(run_key, event)

    def shutdown(self) -> None:
        """End every incomplete tool and run span without owning exporter shutdown."""
        with self._lock:
            for tool_key in tuple(self._tools):
                self._end_tool(tool_key, status=None, end_time=None, duration_ms=None)
            for run_key, run in tuple(self._runs.items()):
                run.span.end()
                self._runs.pop(run_key, None)
                self._remember_completed_run(run_key)

    def _ensure_run(self, run_key: str, event: RunTelemetryEvent) -> _RunState:
        existing = self._runs.get(run_key)
        if existing is not None:
            return existing
        start_time = _start_time_ns(event)
        span = self._tracer.start_span(
            'maivn.run',
            kind=SpanKind.INTERNAL,
            start_time=start_time,
            attributes=_run_attributes(event),
        )
        state = _RunState(span=span)
        self._runs[run_key] = state
        return state

    def _apply_run_metadata(self, span: Span, event: RunTelemetryEvent) -> None:
        span.set_attribute('maivn.event.name', event.event_name)
        span.set_attribute('maivn.event.position', event.position)
        if event.status is not None:
            span.set_attribute('maivn.run.status', event.status)
        if event.duration_ms is not None and event.event_kind == 'run':
            span.set_attribute('maivn.run.duration_ms', event.duration_ms)

    def _apply_tool_event(
        self,
        run_key: str,
        run: _RunState,
        event: RunTelemetryEvent,
    ) -> None:
        tool = event.tool_call
        if tool is None:
            return
        tool_id = _tool_key(event, tool)
        tool_key = (run_key, tool_id)
        if tool_id in run.completed_tools:
            return
        if _is_tool_terminal(event):
            if tool_key not in self._tools:
                self._start_tool(tool_key, run, event, tool)
            self._end_tool(
                tool_key,
                status=event.status,
                end_time=_timestamp_ns(event.timestamp),
                duration_ms=event.duration_ms,
            )
            return
        if tool_key not in self._tools:
            self._start_tool(tool_key, run, event, tool)

    def _start_tool(
        self,
        tool_key: tuple[str, str],
        run: _RunState,
        event: RunTelemetryEvent,
        tool: ToolCallTelemetry,
    ) -> None:
        span = self._tracer.start_span(
            f'execute_tool {tool.name}',
            context=set_span_in_context(run.span),
            kind=SpanKind.INTERNAL,
            start_time=_start_time_ns(event),
            attributes=_tool_attributes(event, tool),
        )
        self._tools[tool_key] = _ToolState(span=span, run_key=tool_key[0])

    def _end_tool(
        self,
        tool_key: tuple[str, str],
        *,
        status: str | None,
        end_time: int | None,
        duration_ms: float | None,
    ) -> None:
        state = self._tools.pop(tool_key, None)
        if state is None:
            return
        if status is not None:
            state.span.set_attribute('maivn.tool.status', status)
        if duration_ms is not None:
            state.span.set_attribute('maivn.tool.duration_ms', duration_ms)
        state.span.set_status(_otel_status(status))
        state.span.end(end_time=end_time)
        run = self._runs.get(state.run_key)
        if run is not None:
            _remember_recent(run.completed_tools, tool_key[1])

    def _end_run(self, run_key: str, event: RunTelemetryEvent) -> None:
        end_time = _timestamp_ns(event.timestamp)
        for tool_key in tuple(self._tools):
            if tool_key[0] == run_key:
                self._end_tool(
                    tool_key,
                    status=None,
                    end_time=end_time,
                    duration_ms=None,
                )
        run = self._runs.pop(run_key, None)
        if run is None:
            return
        run.span.set_status(_otel_status(event.status))
        run.span.end(end_time=end_time)
        self._remember_completed_run(run_key)

    def _remember_completed_run(self, run_key: str) -> None:
        _remember_recent(self._completed_runs, run_key)


def _run_key(event: RunTelemetryEvent) -> str | None:
    return event.session_id or event.root_event_id or event.event_id


def _tool_key(event: RunTelemetryEvent, tool: ToolCallTelemetry) -> str:
    return tool.call_id or event.correlation_id or event.event_id or tool.name


def _run_attributes(event: RunTelemetryEvent) -> dict[str, str | int | float]:
    attributes: dict[str, str | int | float] = {
        'gen_ai.operation.name': 'invoke_agent',
        'maivn.telemetry.schema_version': event.schema_version,
    }
    _set_if_present(attributes, 'maivn.run.session_id', event.session_id)
    _set_if_present(attributes, 'gen_ai.conversation.id', event.thread_id)
    _set_if_present(attributes, 'maivn.run.root_event_id', event.root_event_id)
    _set_if_present(attributes, 'maivn.run.parent_session_id', event.parent_session_id)
    if event.scope is not None:
        _set_if_present(attributes, 'maivn.scope.agent.name', event.scope.agent_name)
        _set_if_present(attributes, 'maivn.scope.swarm.name', event.scope.swarm_name)
    return attributes


def _tool_attributes(
    event: RunTelemetryEvent,
    tool: ToolCallTelemetry,
) -> dict[str, str | int | float]:
    attributes: dict[str, str | int | float] = {
        'gen_ai.operation.name': 'execute_tool',
        'gen_ai.tool.name': tool.name,
        'maivn.telemetry.schema_version': event.schema_version,
    }
    _set_if_present(attributes, 'gen_ai.tool.call.id', tool.call_id)
    _set_if_present(attributes, 'gen_ai.tool.type', tool.tool_type)
    _set_if_present(attributes, 'maivn.tool.namespace', tool.namespace)
    _set_if_present(attributes, 'maivn.tool.version', tool.version)
    return attributes


def _apply_usage(span: Span, usage: TokenUsageTelemetry) -> None:
    span.set_attribute('gen_ai.usage.input_tokens', usage.input_tokens)
    span.set_attribute('gen_ai.usage.output_tokens', usage.output_tokens)
    span.set_attribute('maivn.usage.cache_read_tokens', usage.cache_read_tokens)
    span.set_attribute('maivn.usage.cache_creation_tokens', usage.cache_creation_tokens)
    span.set_attribute('maivn.usage.reasoning_tokens', usage.reasoning_tokens)
    span.set_attribute('maivn.usage.total_tokens', usage.total_tokens)


def _set_if_present(
    attributes: dict[str, str | int | float],
    name: str,
    value: str | float | None,
) -> None:
    if value is not None:
        attributes[name] = value


def _is_tool_terminal(event: RunTelemetryEvent) -> bool:
    return event.event_name in {
        'model_tool_complete',
        'system_tool_complete',
        'system_tool_error',
        'tool.dispatch_completed',
    } or event.status in {
        'completed',
        'error',
        'failed',
    }


def _is_run_terminal(event: RunTelemetryEvent) -> bool:
    return event.event_name in {'final', 'error', 'session_complete', 'session.completed'}


def _remember_recent(values: dict[str, None], key: str) -> None:
    values[key] = None
    if len(values) > _COMPLETED_RETENTION_LIMIT:
        values.pop(next(iter(values)))


def _otel_status(status: str | None) -> Status:
    if status in {'error', 'failed'}:
        return Status(StatusCode.ERROR)
    if status in {'completed', 'ok', 'success'}:
        return Status(StatusCode.OK)
    return Status(StatusCode.UNSET)


def _start_time_ns(event: RunTelemetryEvent) -> int | None:
    timestamp = event.timestamp
    if timestamp is None:
        return None
    if event.duration_ms is not None:
        timestamp -= timedelta(milliseconds=event.duration_ms)
    return _timestamp_ns(timestamp)


def _timestamp_ns(value: datetime | None) -> int | None:
    if value is None:
        return None
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    epoch = datetime(1970, 1, 1, tzinfo=timezone.utc)
    delta = value.astimezone(timezone.utc) - epoch
    return (delta.days * 86_400 + delta.seconds) * 1_000_000_000 + delta.microseconds * 1_000


__all__ = ['OpenTelemetryAdapter']
