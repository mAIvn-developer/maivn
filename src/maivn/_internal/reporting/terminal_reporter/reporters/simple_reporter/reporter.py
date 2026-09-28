"""Plain-text terminal reporter: the SDK's default (Tier 0) verbose output.

With ``rich`` an optional extra, this reporter is the primary experience for
most installs - not a degraded fallback. It renders every event family the
stream carries (tools, phases, model routing, sessions, hooks, status, errors)
in an aligned verb-gutter layout, and it owns the shared content logic: the
Rich reporter subclasses this one and restyles the same lines, so the two
tiers cannot drift apart on information.
"""

from __future__ import annotations

import json
import os
import re
import sys
import time
from contextlib import contextmanager
from dataclasses import dataclass
from typing import TYPE_CHECKING, cast

from maivn._internal.error_diagnostics import (
    diagnostic_facts,
    error_action_hint,
    safe_error_message,
)
from maivn._internal.reporting.terminal_reporter.base import BaseReporter

if TYPE_CHECKING:
    from collections.abc import Generator

    from maivn._internal.models import InvokeResponse, StreamEvent

_BORDER = '=' * 60
_BENCHMARK_METRICS_ENV = 'MAIVN_BENCHMARK_METRICS'
_BENCHMARK_METRIC_PREFIX = 'MAIVN_BENCHMARK_INVOCATION_METRIC '
_BENCHMARK_TERMINAL_EVENTS = frozenset(
    {'final', 'session.completed', 'error', 'cancelled', 'session.cancelled'}
)
# A local placeholder can arrive over several chunks. Printing its unfinished
# suffix makes later hydration look like an answer rewrite in a plain terminal.
_INCOMPLETE_PRIVATE_TOKEN = re.compile(r'\{(?:_(?:\{[^{}]*(?:\}_?)?)?)?$')

GUTTER_WIDTH = 12
"""Width of the right-aligned verb column every event line shares."""


def _emit(text: str = '', *, end: str = '\n', flush: bool = False) -> None:
    """Write text through the console encoding, replacing what it cannot represent.

    Windows consoles default to cp1252, so a single character outside it - an em-dash,
    a curly quote, an emoji - raised UnicodeEncodeError mid-stream and killed the run.
    Reporting is observation: printing a model's text must never be able to fail the
    invocation that produced it.
    """
    encoding = getattr(sys.stdout, 'encoding', None) or 'utf-8'
    safe = text.encode(encoding, errors='replace').decode(encoding, errors='replace')
    print(safe, end=end, flush=flush)


@dataclass(frozen=True, slots=True)
class EventLine:
    """One aligned reporter line: a right-justified verb gutter plus content.

    ``tone`` is semantic, mirroring the Studio state colors: ``active`` (signal),
    ``success`` (nominal), ``error`` (alert), ``dim`` for secondary telemetry,
    and ``info`` for anything else. Tier 0 ignores tone; Tier 1 maps it to color.
    """

    verb: str
    text: str
    tone: str = 'info'
    detail: str = ''


def display_response(response: str) -> str:
    """Return the final response, pretty-printing it when it is a JSON document.

    Structured-output runs return a single-line JSON blob; two-space indentation
    makes the terminal rendering legible without altering the response itself.
    """
    stripped = response.strip()
    if not stripped.startswith(('{', '[')):
        return response
    try:
        parsed: object = json.loads(stripped)
    except ValueError:
        return response
    return json.dumps(parsed, indent=2, ensure_ascii=False, default=str)


# MARK: Simple Reporter


class SimpleReporter(BaseReporter):
    """Render every v1-visible stream event and the v1-compatible execution summary."""

    def __init__(self, *, enabled: bool = True) -> None:
        """Initialize the per-invocation streaming, tool, and layout state."""
        super().__init__(enabled=enabled)
        self._streamed_text_by_assistant: dict[str, str] = {}
        self._printed_text_by_assistant: dict[str, str] = {}
        self._system_tool_text_by_event: dict[str, str] = {}
        self._tool_count = 0
        self._start_time: float | None = None
        self._terminal_time: float | None = None
        self._benchmark_start_times: dict[tuple[str, str], float] = {}
        self._tool_started_at: dict[str, float] = {}
        self._finished_tool_keys: set[str] = set()
        self._stream_open = False

    @property
    def streamed_text_by_assistant(self) -> dict[str, str]:
        """Return streamed response text keyed by its assistant identifier."""
        return dict(self._streamed_text_by_assistant)

    # MARK: Event Routing

    def report_event(self, event: StreamEvent) -> None:
        """Render a v1-visible event while retaining v2 event envelopes unchanged."""
        if not self.enabled:
            return
        event_time = time.monotonic()
        if self._start_time is None:
            self._start_time = event_time
        benchmark_identity = _benchmark_identity(event.data)
        if benchmark_identity is not None and event.event_type not in _BENCHMARK_TERMINAL_EVENTS:
            self._benchmark_start_times.setdefault(benchmark_identity[:2], event_time)
        if event.event_type in _BENCHMARK_TERMINAL_EVENTS:
            self._terminal_time = event_time
        payload = event.payload
        if event.name == 'update':
            self._report_update(payload)
        elif event.name == 'system_tool_chunk':
            self._report_system_tool_chunk(event, payload)
        else:
            line = self._event_line_for(event.name, payload)
            if line is not None:
                self._render_event_line(line)
        if event.event_type in _BENCHMARK_TERMINAL_EVENTS:
            start_time = (
                self._benchmark_start_times.get(benchmark_identity[:2])
                if benchmark_identity is not None
                else None
            )
            self._emit_benchmark_terminal_metric(
                event,
                payload,
                start_time=start_time,
                end_time=event_time,
            )

    def _event_line_for(  # noqa: PLR0911 - one return per event family reads clearest.
        self, name: str, payload: dict[str, object]
    ) -> EventLine | None:
        """Build the aligned line for one event, or ``None`` for silent events."""
        if name == 'system_tool_start':
            return self._tool_start_line(payload)
        if name == 'system_tool_complete':
            return self._tool_finish_line(payload, failed=False)
        if name == 'system_tool_error':
            return self._tool_finish_line(payload, failed=True)
        if name == 'enrichment':
            return _enrichment_line(payload)
        if name == 'model_routing':
            return _model_routing_line(payload)
        if name == 'assignment_received':
            return _assignment_line(payload)
        if name == 'status_message':
            message = _string(payload.get('message'))
            return EventLine(verb='Status', text=message, tone='dim') if message else None
        if name == 'hook_fired':
            return _hook_line(payload)
        if name == 'error':
            message = (
                _string(payload.get('message'))
                or _string(payload.get('error_code'))
                or _string(payload.get('error'))
                or 'Unknown error'
            )
            details = payload.get('details')
            facts = diagnostic_facts(
                payload,
                cast('dict[str, object]', details) if isinstance(details, dict) else {},
            )
            code = _string(facts.get('error_code'))
            detail = ' '.join(f'{key}={value}' for key, value in facts.items())
            return EventLine(
                verb='Error',
                text=f'{safe_error_message(message)}. {error_action_hint(code)}',
                tone='error',
                detail=detail,
            )
        return None

    def _tool_start_line(self, payload: dict[str, object]) -> EventLine | None:
        """Record a deduplicated tool start and build its ``Running`` line.

        Swarm runs deliver each start twice - once from the local tool runtime
        and once from the server echo - so the first occurrence per
        (agent, tool, turn) renders and counts; the echo is dropped.
        """
        key = _tool_key(payload)
        if key in self._tool_started_at:
            return None
        self._tool_started_at[key] = time.monotonic()
        self._tool_count += 1
        tool_name = _string(payload.get('tool_name')) or 'tool'
        agent_name = _string(payload.get('agent_name'))
        detail = f'({agent_name})' if agent_name else ''
        return EventLine(verb='Running', text=tool_name, tone='active', detail=detail)

    def _tool_finish_line(self, payload: dict[str, object], *, failed: bool) -> EventLine | None:
        """Build a deduplicated completion or failure line with its duration.

        Completions arrive twice as well; only the first per (agent, tool,
        turn) renders. The duration prefers the runtime-measured
        ``outcome.duration_ms``; the client-side event gap is a fallback and
        is dropped entirely when too small to be honest about.
        """
        key = _tool_key(payload)
        if key in self._finished_tool_keys:
            return None
        self._finished_tool_keys.add(key)
        tool_name = _string(payload.get('tool_name')) or 'tool'
        started_at = self._tool_started_at.get(key)
        outcome = payload.get('outcome')
        typed_outcome = cast('dict[str, object]', outcome) if isinstance(outcome, dict) else None
        duration = _format_duration(typed_outcome, started_at=started_at)
        if failed:
            message = _failure_message(payload)
            text = f'{tool_name}: {message}' if message else tool_name
            return EventLine(verb='Failed', text=text, tone='error', detail=duration)
        return EventLine(verb='Completed', text=tool_name, tone='success', detail=duration)

    # MARK: Line Rendering

    def _render_event_line(self, line: EventLine) -> None:
        """Print one aligned event line; Tier 1 overrides this to add color."""
        self._break_stream()
        rendered = f'{line.verb.rjust(GUTTER_WIDTH)} {line.text}'
        if line.detail:
            rendered = f'{rendered}  {line.detail}'
        _emit(rendered)

    def _render_stream_text(self, text: str) -> None:
        """Print raw streamed content and track whether the line is still open."""
        _emit(text, end='', flush=True)
        self._stream_open = not text.endswith('\n')

    def _break_stream(self) -> None:
        """Terminate an in-flight streamed line before printing a block or event line."""
        if self._stream_open:
            _emit()
            self._stream_open = False

    # MARK: Streaming Output

    def report_response_chunk(
        self,
        text: str,
        *,
        assistant_id: str | None = None,
        full_text: str | None = None,
        replace_content: bool = False,
    ) -> None:
        """Print one response delta and retain its cumulative text like v1's simple reporter."""
        if not self.enabled or not text:
            return
        stream_id = assistant_id or 'assistant'
        previous = self._streamed_text_by_assistant.get(stream_id, '')
        current = (
            full_text if full_text is not None else ('' if replace_content else previous) + text
        )
        self._streamed_text_by_assistant[stream_id] = current
        incomplete = _INCOMPLETE_PRIVATE_TOKEN.search(current)
        stable = current[: incomplete.start()] if incomplete is not None else current
        printed = self._printed_text_by_assistant.get(stream_id, '')
        if stable.startswith(printed):
            delta = stable[len(printed) :]
        else:
            if printed:
                self._render_stream_text('\n')
            delta = stable
        self._printed_text_by_assistant[stream_id] = stable
        if delta:
            self._render_stream_text(delta)

    def print_final_response(self, response: str) -> None:
        """Print a terminal response unless its exact text already streamed."""
        if not self.enabled:
            return
        if any(text == response for text in self._streamed_text_by_assistant.values()):
            stream_id = next(
                key for key, text in self._streamed_text_by_assistant.items() if text == response
            )
            printed = self._printed_text_by_assistant.get(stream_id, '')
            if response.startswith(printed):
                self._render_stream_text(response[len(printed) :])
            _emit()
            self._stream_open = False
        else:
            self._break_stream()
            rendered = display_response(response)
            _emit(f'\n{_BORDER}\nFINAL RESPONSE\n{_BORDER}\n{rendered}\n{_BORDER}')
        self._streamed_text_by_assistant.clear()
        self._printed_text_by_assistant.clear()

    def report_system_tool_progress(
        self,
        *,
        event_id: str,
        tool_name: str,
        chunk_count: int,
        elapsed_seconds: float,
        text: str | None = None,
    ) -> None:
        """Print only a newly received system-tool text suffix, matching v1 behavior."""
        if not self.enabled:
            return
        previous = self._system_tool_text_by_event.get(event_id, '')
        if text:
            delta = text.removeprefix(previous)
            self._system_tool_text_by_event[event_id] = text
            if delta:
                self._render_stream_text(delta)
            return
        self._render_event_line(
            EventLine(
                verb='Working',
                text=tool_name,
                tone='dim',
                detail=f'({elapsed_seconds:.0f}s, {chunk_count} chunks)',
            )
        )

    # MARK: Terminal Blocks

    def report_response(self, response: InvokeResponse) -> None:
        """Print the final response and stable execution summary."""
        if not self.enabled:
            return
        self.print_final_response(response.response)
        self.print_summary(response.token_usage)
        provider_usage = response.provider_usage
        if provider_usage is not None:
            counts = 'complete' if provider_usage.provider_counts_complete else 'incomplete'
            settlement = 'settled' if provider_usage.settled else 'settlement pending'
            self.print_event('usage', f'Provider counts {counts}; {settlement}.')

    def print_summary(self, token_usage: object | None = None) -> None:
        """Print the stable tool, duration, and token telemetry block."""
        if not self.enabled:
            return
        self._break_stream()
        _emit(f'\n{_BORDER}\nEXECUTION SUMMARY\n{_BORDER}')
        _emit(f'Tools Executed: {self._tool_count}')
        _emit(f'Total Time: {_format_total_time(self._elapsed_seconds())}')
        token_lines = token_usage_lines(token_usage)
        if token_lines:
            _emit(_BORDER)
            _emit('TOKEN USAGE')
            for token_line in token_lines:
                _emit(token_line)
        _emit(f'{_BORDER}\n')

    def print_final_result(self, result: object) -> None:
        """Print a structured result block when the event stream provides one."""
        if not self.enabled or result is None:
            return
        self._break_stream()
        rendered = display_response(result) if isinstance(result, str) else result
        _emit(f'\n{_BORDER}\nRESULT\n{_BORDER}\n{rendered}\n{_BORDER}')

    def print_event(
        self,
        event_type: str,
        message: str,
        details: dict[str, object] | None = None,
    ) -> None:
        """Render a legacy display event as one aligned line."""
        if not self.enabled:
            return
        tone = (
            'error' if event_type == 'error' else 'success' if event_type == 'success' else 'info'
        )
        verb = (event_type or 'event').capitalize()[:GUTTER_WIDTH]
        detail = f'{details}' if details else ''
        self._render_event_line(EventLine(verb=verb, text=message, tone=tone, detail=detail))

    # MARK: Interactive Input

    @contextmanager
    def prepare_for_user_input(self) -> Generator[None, None, None]:
        """Keep the established input context-manager surface for interrupts."""
        yield

    def get_input(  # noqa: PLR0913 - preserves the reporter compatibility surface.
        self,
        prompt: str,
        *,
        input_type: str = 'text',
        choices: list[str] | None = None,
        data_key: str | None = None,
        arg_name: str | None = None,
        tool_name: str | None = None,
    ) -> str:
        """Collect terminal input without narrowing dependency-interrupt metadata.

        Plain installs must be able to answer agent follow-up and interrupt
        prompts: headless HITL cannot require the ``rich`` extra.
        """
        _ = (input_type, choices, data_key, arg_name, tool_name)
        if not self.enabled:
            return input(prompt)
        self._break_stream()
        return input(_prompt_with_separator(prompt))

    # MARK: Reporter Internals

    def _elapsed_seconds(self) -> float:
        """Return elapsed monotonic time from the first event to the terminal event."""
        if self._start_time is None:
            return 0.0
        end_time = self._terminal_time if self._terminal_time is not None else time.monotonic()
        return end_time - self._start_time

    def _emit_benchmark_terminal_metric(
        self,
        event: StreamEvent,
        payload: dict[str, object],
        *,
        start_time: float | None,
        end_time: float,
    ) -> None:
        """Emit one opt-in, content-free terminal record for benchmark accounting."""
        if os.getenv(_BENCHMARK_METRICS_ENV) != '1':
            return
        identity = _benchmark_identity(event.data)
        identity_complete = identity is not None
        metric: dict[str, object] = {
            'status': event.event_type,
            'usage': _benchmark_usage(payload.get('usage')),
            'identity_complete': identity_complete,
            'timing_complete': identity_complete and start_time is not None,
        }
        if identity_complete:
            session_id, root_event_id, parent_session_id = identity
            metric.update(
                {
                    'session_id': session_id,
                    'root_event_id': root_event_id,
                    'parent_session_id': parent_session_id,
                }
            )
        if identity_complete and start_time is not None:
            metric.update(
                {
                    'start_monotonic_seconds': start_time,
                    'end_monotonic_seconds': end_time,
                }
            )
        _emit(f'{_BENCHMARK_METRIC_PREFIX}{json.dumps(metric, separators=(",", ":"))}')

    def _report_update(self, payload: dict[str, object]) -> None:
        """Turn cumulative v1 streaming content into a terminal-safe delta."""
        content = _string(payload.get('streaming_content'))
        if content is None:
            return
        assistant_id = _string(payload.get('assistant_id')) or 'assistant'
        previous = self._streamed_text_by_assistant.get(assistant_id, '')
        delta = content.removeprefix(previous)
        self.report_response_chunk(delta, assistant_id=assistant_id, full_text=content)

    def _report_system_tool_chunk(self, event: StreamEvent, payload: dict[str, object]) -> None:
        """Forward any text-bearing v2 system-tool chunk to the v1 progress renderer."""
        text = _string(payload.get('text'))
        if text is None:
            return
        event_id = _string(event.data.get('event_id')) or str(event.position)
        tool_name = _string(payload.get('tool_name')) or 'tool'
        self.report_system_tool_progress(
            event_id=event_id,
            tool_name=tool_name,
            chunk_count=1,
            elapsed_seconds=0,
            text=text,
        )


# MARK: Line Builders


def _enrichment_line(payload: dict[str, object]) -> EventLine | None:
    """Describe an execution-phase transition, humanizing unmapped phase ids."""
    message = _string(payload.get('message'))
    if message is None:
        phase = _string(payload.get('phase'))
        message = phase.replace('_', ' ') if phase else None
    return EventLine(verb='Phase', text=message, tone='dim') if message else None


def _model_routing_line(payload: dict[str, object]) -> EventLine | None:
    """Describe an auto-router decision: requested mode, chosen model, and why."""
    model = _string(payload.get('model'))
    if model is None:
        return None
    mode = _string(payload.get('mode'))
    if mode in {'hop_failure', 'hop_recovered', 'hop_timing', 'hop_usage'}:
        labels = {
            'hop_failure': f'Provider attempt failed for {model}',
            'hop_recovered': f'Recovered using {model}',
            'hop_timing': f'Provider attempt timing for {model}',
            'hop_usage': f'Provider usage reported for {model}',
        }
        facts = diagnostic_facts(payload)
        detail = ' '.join(f'{key}={value}' for key, value in facts.items())
        return EventLine(
            verb='Model',
            text=labels[mode],
            tone='success' if mode == 'hop_recovered' else 'dim',
            detail=detail,
        )
    text = f'{mode} -> {model}' if mode and mode != model else model
    parts: list[str] = []
    complexity = _string(payload.get('complexity'))
    if complexity:
        parts.append(complexity)
    reasoning = _string(payload.get('reasoning'))
    if reasoning and reasoning != 'off':
        parts.append(f'reasoning {reasoning}')
    detail = f'({", ".join(parts)})' if parts else ''
    return EventLine(verb='Model', text=text, tone='dim', detail=detail)


def _assignment_line(payload: dict[str, object]) -> EventLine:
    """Describe a received assignment: stage plus message and model context."""
    stage = _string(payload.get('stage')) or 'received'
    text = stage.replace('_', ' ').removeprefix('session ') or stage
    parts: list[str] = []
    message_count = payload.get('message_count')
    if isinstance(message_count, int) and not isinstance(message_count, bool):
        noun = 'message' if message_count == 1 else 'messages'
        parts.append(f'{message_count} {noun}')
    model = _string(payload.get('model'))
    if model:
        parts.append(f'model {model}')
    detail = f'({", ".join(parts)})' if parts else ''
    return EventLine(verb='Session', text=text, tone='dim', detail=detail)


def _hook_line(payload: dict[str, object]) -> EventLine | None:
    """Describe one hook firing with its stage, target, outcome, and timing."""
    name = _string(payload.get('name'))
    if name is None:
        return None
    stage = _string(payload.get('stage')) or ''
    target_type = _string(payload.get('target_type')) or ''
    context = ' '.join(part for part in (stage, target_type) if part)
    text = f'{name} ({context})' if context else name
    status = _string(payload.get('status'))
    if status == 'failed':
        error = _string(payload.get('error'))
        detail = error or 'failed'
        return EventLine(verb='Hook', text=text, tone='error', detail=detail)
    elapsed_ms = payload.get('elapsed_ms')
    detail = (
        f'({elapsed_ms}ms)'
        if isinstance(elapsed_ms, int) and not isinstance(elapsed_ms, bool)
        else ''
    )
    return EventLine(verb='Hook', text=text, tone='dim', detail=detail)


# MARK: Shared Helpers


_MEASURED_GAP_FLOOR_MS = 100
"""Below this, a client-measured start-to-complete gap says nothing real."""

_SECONDS_THRESHOLD_MS = 1000
"""Durations at or above one second render in seconds instead of milliseconds."""


def _format_duration(outcome: dict[str, object] | None, *, started_at: float | None) -> str:
    """Format a tool duration, preferring the runtime-measured outcome value.

    The client-side event gap is only honest for locally executed tools; for
    server-side tools both events arrive together, so a tiny measured gap is
    omitted rather than rendered as a misleading ``(0.0s)``.
    """
    elapsed_ms: float | None = None
    if outcome is not None:
        candidate = outcome.get('duration_ms')
        if isinstance(candidate, (int, float)) and not isinstance(candidate, bool):
            elapsed_ms = float(candidate)
    if elapsed_ms is None and started_at is not None:
        measured_ms = (time.monotonic() - started_at) * 1000
        if measured_ms < _MEASURED_GAP_FLOOR_MS:
            return ''
        elapsed_ms = measured_ms
    if elapsed_ms is None:
        return ''
    if elapsed_ms >= _SECONDS_THRESHOLD_MS:
        return f'({elapsed_ms / 1000:.1f}s)'
    return f'({elapsed_ms:.0f}ms)'


def _failure_message(payload: dict[str, object]) -> str | None:
    """Extract the human failure message from a tool-error payload."""
    outcome = payload.get('outcome')
    if isinstance(outcome, dict):
        error = cast('dict[str, object]', outcome).get('error')
        if isinstance(error, dict):
            message = _string(cast('dict[str, object]', error).get('message'))
            if message:
                return message
    return _string(payload.get('error')) or _string(payload.get('message'))


def _tool_key(payload: dict[str, object]) -> str:
    """Build the identity used to pair tool starts with their completions.

    The wire's call id is preferred: it separates two genuine same-turn calls
    to one tool (same tool, different arguments) while still collapsing the
    duplicate local/server echoes of a single call, both of which carry the
    id. Events without an id fall back to (agent, tool, turn).
    """
    for carrier_key in ('tool_call', 'outcome'):
        carrier = payload.get(carrier_key)
        if isinstance(carrier, dict):
            call_id = _string(cast('dict[str, object]', carrier).get('call_id'))
            if call_id:
                return f'call\x00{call_id}'
    agent = _string(payload.get('agent_name')) or ''
    tool = _string(payload.get('tool_name')) or 'tool'
    turn = payload.get('turn_index')
    return f'{agent}\x00{tool}\x00{turn}'


def _string(value: object) -> str | None:
    """Return a non-empty string value."""
    return value if isinstance(value, str) and value else None


def _benchmark_identity(
    data: dict[str, object],
) -> tuple[str, str, str | None] | None:
    """Return a complete invocation identity without treating absent parent as root."""
    session_id = _string(data.get('session_id'))
    root_event_id = _string(data.get('root_event_id'))
    if session_id is None or root_event_id is None or 'parent_session_id' not in data:
        return None
    parent_value = data.get('parent_session_id')
    parent_session_id = _string(parent_value)
    if parent_value is not None and parent_session_id is None:
        return None
    return session_id, root_event_id, parent_session_id


def _integer(value: object) -> int:
    """Return an integer count, treating anything else (including bools) as zero."""
    return value if isinstance(value, int) and not isinstance(value, bool) else 0


def _benchmark_usage(value: object) -> dict[str, int]:
    """Project only supplied token counters into an opt-in benchmark record."""
    if not isinstance(value, dict):
        return {}
    usage = cast('dict[str, object]', value)
    counters: dict[str, int] = {}
    for key in (
        'input_tokens',
        'output_tokens',
        'total_tokens',
        'cache_read_tokens',
        'cache_read_input_tokens',
        'cache_creation_tokens',
        'cache_creation_input_tokens',
    ):
        candidate = usage.get(key)
        if isinstance(candidate, int) and not isinstance(candidate, bool) and candidate >= 0:
            counters[key] = candidate
    return counters


def token_usage_lines(token_usage: object | None) -> list[str]:
    """Format the v1 token block, keeping the exact line shapes consumers parse."""
    if not isinstance(token_usage, dict):
        return []
    usage = cast('dict[str, object]', token_usage)
    lines = [
        f'  Total Tokens: {_integer(usage.get("total_tokens")):,}',
        f'  Input Tokens: {_integer(usage.get("input_tokens")):,}',
        f'  Output Tokens: {_integer(usage.get("output_tokens")):,}',
    ]
    for label, key in (
        ('Reasoning Tokens', 'reasoning_tokens'),
        ('Cache Read', 'cache_read_tokens'),
        ('Cache Creation', 'cache_creation_tokens'),
    ):
        count = _integer(usage.get(key))
        if count:
            lines.append(f'  {label}: {count:,}')
    return lines


def _format_total_time(elapsed_seconds: float) -> str:
    """Format total elapsed time with the v1 reporter's two-decimal suffix."""
    return f'{elapsed_seconds:.2f}s'


def _prompt_with_separator(prompt: str) -> str:
    """Add the separator terminals do not insert between a prompt and user input."""
    return prompt if prompt[-1:].isspace() else f'{prompt} '


__all__ = [
    'GUTTER_WIDTH',
    'EventLine',
    'SimpleReporter',
    'display_response',
    'token_usage_lines',
]
