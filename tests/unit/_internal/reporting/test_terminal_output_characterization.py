"""Characterization tests for the exact terminal output of the Tier 0 and Tier 1 reporters.

Every assertion compares complete output text, so a refactor that moves a
space, renames a verb, or reorders a detail fails here.
"""

from __future__ import annotations

import json
import time
from io import StringIO
from typing import TYPE_CHECKING

import pytest
from maivn_contracts.messages import Message
from rich.console import Console

from maivn._internal.models import InvokeResponse, StreamEvent
from maivn._internal.reporting.terminal_reporter import RichReporter, SimpleReporter
from maivn._internal.reporting.terminal_reporter.reporters.simple_reporter import (
    GUTTER_WIDTH,
    display_response,
)

if TYPE_CHECKING:
    from collections.abc import Callable

_BORDER = '=' * 60


def _line(verb: str, text: str, detail: str = '') -> str:
    """Render the expected aligned line for one event."""
    rendered = f'{verb.rjust(GUTTER_WIDTH)} {text}'
    return f'{rendered}  {detail}' if detail else rendered


def _event(
    name: str,
    payload: dict[str, object],
    *,
    position: int = 1,
    data: dict[str, object] | None = None,
) -> StreamEvent:
    """Build a stream event with a canonical payload envelope."""
    envelope: dict[str, object] = {'payload': payload}
    if data is not None:
        envelope.update(data)
    return StreamEvent(position=position, event_type=name, data=envelope)


def _response(
    *, usage: dict[str, object] | None = None, text: str = 'Completed.'
) -> InvokeResponse:
    """Build an invocation response."""
    return InvokeResponse(
        final_message=Message(
            message_id='msg-1',
            role='assistant',
            content=text,
            ts='2026-07-12T00:00:00Z',
        ),
        session_id='ses-1',
        root_event_id='evt-1',
        event_positions=[1],
        usage=usage or {},
        response=text,
    )


def _frozen_clock(monkeypatch: pytest.MonkeyPatch, start: float = 100.0) -> list[float]:
    """Pin ``time.monotonic`` to a mutable value and return it."""
    now = [start]
    monkeypatch.setattr(time, 'monotonic', lambda: now[0])
    return now


def _report_lines(capsys: pytest.CaptureFixture[str], *events: StreamEvent) -> list[str]:
    """Report events through a fresh plain reporter and return the output lines."""
    reporter = SimpleReporter(enabled=True)
    for event in events:
        reporter.report_event(event)
    return capsys.readouterr().out.splitlines()


# Section: Disabled reporting.


def test_disabled_simple_reporter_prints_nothing(capsys: pytest.CaptureFixture[str]) -> None:
    """A disabled reporter is silent for every rendering callback."""
    reporter = SimpleReporter(enabled=False)

    reporter.report_event(_event('system_tool_start', {'tool_name': 'scan'}))
    reporter.report_response_chunk('text')
    reporter.print_final_response('text')
    reporter.report_system_tool_progress(
        event_id='e', tool_name='scan', chunk_count=1, elapsed_seconds=1
    )
    reporter.report_response(_response(usage={'input_tokens': 1}))
    reporter.print_summary({'total_tokens': 1})
    reporter.print_final_result('result')
    reporter.print_event('info', 'message')

    assert capsys.readouterr().out == ''
    assert reporter.streamed_text_by_assistant == {}


def test_disabled_rich_reporter_prints_nothing() -> None:
    """A disabled Rich reporter is silent for its panel callbacks."""
    reporter = RichReporter(enabled=False)
    buffer = StringIO()
    reporter.console = Console(file=buffer, force_terminal=False, width=60)

    reporter.print_final_response('text')
    reporter.print_summary({'total_tokens': 1})
    reporter.print_final_result('result')

    assert buffer.getvalue() == ''


@pytest.mark.parametrize('reporter_type', [SimpleReporter, RichReporter])
def test_disabled_reporter_asks_for_input_with_the_raw_prompt(
    monkeypatch: pytest.MonkeyPatch, reporter_type: Callable[..., SimpleReporter]
) -> None:
    """Disabled reporters still collect input, without adding a separator."""
    prompts: list[str] = []

    def capture(prompt: str) -> str:
        prompts.append(prompt)
        return 'yes'

    monkeypatch.setattr('builtins.input', capture)

    assert reporter_type(enabled=False).get_input('Continue?') == 'yes'
    assert prompts == ['Continue?']


# Section: Event lines.


def test_unrendered_events_print_nothing(capsys: pytest.CaptureFixture[str]) -> None:
    """Events without a line builder, or without content, are silent."""
    lines = _report_lines(
        capsys,
        _event('session_start', {'session_id': 'ses-1'}),
        _event('status_message', {'message': ''}),
        _event('enrichment', {}),
        _event('model_routing', {'mode': 'auto'}),
        _event('hook_fired', {'stage': 'before'}),
        _event('update', {'assistant_id': 'a'}),
        _event('system_tool_chunk', {'tool_name': 'scan'}),
    )

    assert lines == []


def test_enrichment_line_humanizes_a_phase_without_a_message(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """A phase id stands in for a missing message, with underscores as spaces."""
    lines = _report_lines(
        capsys,
        _event('enrichment', {'phase': 'memory_retrieval'}),
        _event('enrichment', {'phase': 'x', 'message': 'Recalling'}),
    )

    assert lines == [_line('Phase', 'memory retrieval'), _line('Phase', 'Recalling')]


def test_model_routing_lines(capsys: pytest.CaptureFixture[str]) -> None:
    """Routing lines show the mode, chosen model, complexity, and non-off reasoning."""
    lines = _report_lines(
        capsys,
        _event('model_routing', {'model': 'model-x'}),
        _event('model_routing', {'model': 'model-x', 'mode': 'model-x'}),
        _event(
            'model_routing',
            {'model': 'model-x', 'mode': 'auto', 'complexity': 'hard', 'reasoning': 'high'},
        ),
        _event('model_routing', {'model': 'model-x', 'mode': 'auto', 'reasoning': 'off'}),
        _event('model_routing', {'model': 'model-x', 'reasoning': 'low'}),
    )

    assert lines == [
        _line('Model', 'model-x'),
        _line('Model', 'model-x'),
        _line('Model', 'auto -> model-x', '(hard, reasoning high)'),
        _line('Model', 'auto -> model-x'),
        _line('Model', 'model-x', '(reasoning low)'),
    ]


def test_model_routing_hop_lines_show_only_structural_facts(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Provider-hop lines use fixed labels and only bounded diagnostic facts."""
    facts: dict[str, object] = {
        'error_code': 'rate_limited',
        'call_id': 'call-1',
        'attempt_index': 2,
        'provider_message': 'secret provider text',
    }

    lines = _report_lines(
        capsys,
        _event('model_routing', {'model': 'm-1', 'mode': 'hop_failure', **facts}),
        _event('model_routing', {'model': 'm-2', 'mode': 'hop_recovered'}),
        _event('model_routing', {'model': 'm-3', 'mode': 'hop_timing', 'attempt_index': 0}),
        _event('model_routing', {'model': 'm-4', 'mode': 'hop_usage'}),
    )

    assert lines == [
        _line(
            'Model',
            'Provider attempt failed for m-1',
            'error_code=rate_limited call_id=call-1 attempt_index=2',
        ),
        _line('Model', 'Recovered using m-2'),
        _line('Model', 'Provider attempt timing for m-3', 'attempt_index=0'),
        _line('Model', 'Provider usage reported for m-4'),
    ]


def test_assignment_lines(capsys: pytest.CaptureFixture[str]) -> None:
    """Assignment lines strip the session prefix and count messages."""
    lines = _report_lines(
        capsys,
        _event('assignment_received', {}),
        _event('assignment_received', {'stage': 'session_started', 'message_count': 1}),
        _event(
            'assignment_received', {'stage': 'planning', 'message_count': 3, 'model': 'model-x'}
        ),
        _event('assignment_received', {'stage': 'session_', 'message_count': True}),
    )

    assert lines == [
        _line('Session', 'received'),
        _line('Session', 'started', '(1 message)'),
        _line('Session', 'planning', '(3 messages, model model-x)'),
        _line('Session', 'session_'),
    ]


def test_hook_lines(capsys: pytest.CaptureFixture[str]) -> None:
    """Hook lines show stage and target context, timing, and failure."""
    lines = _report_lines(
        capsys,
        _event(
            'hook_fired',
            {'name': 'audit', 'stage': 'before', 'target_type': 'tool', 'elapsed_ms': 5},
        ),
        _event('hook_fired', {'name': 'audit', 'target_type': 'agent', 'elapsed_ms': True}),
        _event('hook_fired', {'name': 'audit', 'elapsed_ms': 1.5}),
        _event('hook_fired', {'name': 'audit', 'stage': 'after', 'status': 'failed'}),
    )

    assert lines == [
        _line('Hook', 'audit (before tool)', '(5ms)'),
        _line('Hook', 'audit (agent)'),
        _line('Hook', 'audit'),
        _line('Hook', 'audit (after)', 'failed'),
    ]


@pytest.mark.parametrize(
    ('payload', 'text'),
    [
        ({'message': 'Request failed', 'error_code': 'x'}, 'Request failed'),
        ({'error_code': 'permission_denied'}, 'permission_denied'),
        ({'error': 'boom'}, 'boom'),
        ({}, 'Unknown error'),
    ],
)
def test_error_line_message_fallbacks(
    capsys: pytest.CaptureFixture[str], payload: dict[str, object], text: str
) -> None:
    """Error lines prefer message, then code, then error, then a fixed default."""
    lines = _report_lines(capsys, _event('error', payload))

    code = payload.get('error_code')
    if code == 'permission_denied':
        hint = 'Check configuration and permissions.'
    else:
        hint = 'Inspect the failed session before submitting more work.'
    detail = f'error_code={code}' if isinstance(code, str) else ''
    assert lines == [_line('Error', f'{text}. {hint}', detail)]


def test_error_line_takes_facts_from_details_and_sanitizes_the_message(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Facts come from details too, and internal paths are never printed."""
    lines = _report_lines(
        capsys,
        _event(
            'error',
            {
                'message': r'Failed at C:\srv\app\worker.py line 3',
                'details': {'error_code': 'cancelled', 'session_id': 'ses-9', 'cause': 'x y'},
            },
        ),
    )

    assert len(lines) == 1
    assert lines[0].startswith(f'{"Error".rjust(GUTTER_WIDTH)} ')
    assert 'worker.py' not in lines[0]
    assert lines[0].endswith(
        'Inspect the session for work completed before cancellation.'
        '  error_code=cancelled session_id=ses-9'
    )


# Section: Tool lines.


def test_tool_durations_prefer_the_runtime_measurement(
    capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    """Outcome durations render in ms below one second and in seconds from one second."""
    _frozen_clock(monkeypatch)
    lines = _report_lines(
        capsys,
        _event('system_tool_complete', {'tool_name': 'a', 'outcome': {'duration_ms': 999}}),
        _event('system_tool_complete', {'tool_name': 'b', 'outcome': {'duration_ms': 1000}}),
        _event('system_tool_complete', {'tool_name': 'c', 'outcome': {'duration_ms': 42.4}}),
        _event('system_tool_complete', {'tool_name': 'd', 'outcome': {'duration_ms': True}}),
        _event('system_tool_complete', {'tool_name': 'e', 'outcome': 'done'}),
        _event('system_tool_complete', {}),
    )

    assert lines == [
        _line('Completed', 'a', '(999ms)'),
        _line('Completed', 'b', '(1.0s)'),
        _line('Completed', 'c', '(42ms)'),
        _line('Completed', 'd'),
        _line('Completed', 'e'),
        _line('Completed', 'tool'),
    ]


def test_tool_duration_falls_back_to_the_measured_gap(
    capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    """Without an outcome duration, a gap of 100ms or more is shown; smaller gaps are not."""
    now = _frozen_clock(monkeypatch)
    reporter = SimpleReporter(enabled=True)

    reporter.report_event(_event('system_tool_start', {'tool_name': 'fast', 'turn_index': 0}))
    reporter.report_event(_event('system_tool_start', {'tool_name': 'edge', 'turn_index': 0}))
    reporter.report_event(_event('system_tool_start', {'tool_name': 'slow', 'turn_index': 0}))
    now[0] += 0.0999
    reporter.report_event(_event('system_tool_complete', {'tool_name': 'fast', 'turn_index': 0}))
    now[0] += 0.0001
    reporter.report_event(_event('system_tool_complete', {'tool_name': 'edge', 'turn_index': 0}))
    now[0] += 2.4
    reporter.report_event(_event('system_tool_complete', {'tool_name': 'slow', 'turn_index': 0}))

    assert capsys.readouterr().out.splitlines() == [
        _line('Running', 'fast'),
        _line('Running', 'edge'),
        _line('Running', 'slow'),
        _line('Completed', 'fast'),
        _line('Completed', 'edge', '(100ms)'),
        _line('Completed', 'slow', '(2.5s)'),
    ]


def test_tool_start_line_names_the_agent(capsys: pytest.CaptureFixture[str]) -> None:
    """A tool start shows its agent in parentheses and defaults its name."""
    lines = _report_lines(
        capsys,
        _event('system_tool_start', {'tool_name': 'scan', 'agent_name': 'Planner'}),
        _event('system_tool_start', {'turn_index': 2}),
    )

    assert lines == [_line('Running', 'scan', '(Planner)'), _line('Running', 'tool')]


def test_call_ids_separate_same_turn_calls_and_pair_their_completions(
    capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    """Two same-turn calls with distinct call ids both render; echoes are dropped."""
    _frozen_clock(monkeypatch)
    reporter = SimpleReporter(enabled=True)
    first: dict[str, object] = {
        'tool_name': 'scan',
        'turn_index': 0,
        'tool_call': {'call_id': 'c-1'},
    }
    second: dict[str, object] = {
        'tool_name': 'scan',
        'turn_index': 0,
        'tool_call': {'call_id': 'c-2'},
    }

    reporter.report_event(_event('system_tool_start', first))
    reporter.report_event(_event('system_tool_start', second))
    reporter.report_event(_event('system_tool_start', dict(second)))
    reporter.report_event(
        _event(
            'system_tool_complete',
            {'tool_name': 'scan', 'outcome': {'call_id': 'c-2', 'duration_ms': 5}},
        )
    )
    reporter.report_event(
        _event('system_tool_complete', {'tool_name': 'scan', 'tool_call': {'call_id': 'c-2'}})
    )
    reporter.print_summary()

    assert capsys.readouterr().out.splitlines() == [
        _line('Running', 'scan'),
        _line('Running', 'scan'),
        _line('Completed', 'scan', '(5ms)'),
        '',
        _BORDER,
        'EXECUTION SUMMARY',
        _BORDER,
        'Tools Executed: 2',
        'Total Time: 0.00s',
        _BORDER,
        '',
    ]


def test_tool_failure_message_sources(capsys: pytest.CaptureFixture[str]) -> None:
    """Failures prefer the outcome error message, then error, then message."""
    lines = _report_lines(
        capsys,
        _event(
            'system_tool_error',
            {
                'tool_name': 'a',
                'outcome': {'error': {'message': 'quota exceeded'}},
                'error': 'ignored',
            },
        ),
        _event(
            'system_tool_error',
            {'tool_name': 'b', 'outcome': {'error': {'message': ''}}, 'error': 'refused'},
        ),
        _event('system_tool_error', {'tool_name': 'c', 'outcome': {'error': 'flat'}}),
        _event('system_tool_error', {'tool_name': 'd'}),
    )

    assert lines == [
        _line('Failed', 'a: quota exceeded'),
        _line('Failed', 'b: refused'),
        _line('Failed', 'c'),
        _line('Failed', 'd'),
    ]


_LEAKY_ERROR = 'Traceback (most recent call last):\n  File "x.py", line 3\n' + r'C:\srv\x.py'


def test_tool_and_hook_failure_text_is_sanitized(capsys: pytest.CaptureFixture[str]) -> None:
    """Tool and hook failure text never prints internal paths or stack text (U05-D3)."""
    lines = _report_lines(
        capsys,
        _event(
            'system_tool_error', {'tool_name': 'a', 'outcome': {'error': {'message': _LEAKY_ERROR}}}
        ),
        _event('system_tool_error', {'tool_name': 'b', 'error': _LEAKY_ERROR}),
        _event('hook_fired', {'name': 'audit', 'status': 'failed', 'error': _LEAKY_ERROR}),
    )

    internal = 'An internal error occurred. Please try again.'
    assert lines == [
        _line('Failed', f'a: {internal}'),
        _line('Failed', f'b: {internal}'),
        _line('Hook', 'audit', internal),
    ]


def test_tool_and_hook_failure_text_is_bounded(capsys: pytest.CaptureFixture[str]) -> None:
    """Long failure text is cut to the shared observability bound (U05-D3)."""
    long_error = 'x' * 600
    lines = _report_lines(
        capsys,
        _event('system_tool_error', {'tool_name': 'a', 'error': long_error}),
        _event('hook_fired', {'name': 'audit', 'status': 'failed', 'error': long_error}),
    )

    bounded = 'x' * 509 + '...'
    assert lines == [_line('Failed', f'a: {bounded}'), _line('Hook', 'audit', bounded)]


# Section: Streaming.


def test_update_events_stream_cumulative_content_as_deltas(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Projected ``update`` events print only the new suffix per assistant."""
    reporter = SimpleReporter(enabled=True)

    reporter.report_event(_event('update', {'streaming_content': 'Hel', 'assistant_id': 'a'}))
    reporter.report_event(_event('update', {'streaming_content': 'Hello', 'assistant_id': 'a'}))
    reporter.report_event(_event('update', {'streaming_content': 'Hello'}))

    assert capsys.readouterr().out == 'HelloHello'
    assert reporter.streamed_text_by_assistant == {'a': 'Hello', 'assistant': 'Hello'}


def test_system_tool_chunks_stream_per_event_id(capsys: pytest.CaptureFixture[str]) -> None:
    """System-tool chunk text prints as deltas keyed by event id, else by position."""
    reporter = SimpleReporter(enabled=True)

    reporter.report_event(
        _event('system_tool_chunk', {'text': 'Look'}, position=1, data={'event_id': 'st-1'})
    )
    reporter.report_event(
        _event('system_tool_chunk', {'text': 'Looking'}, position=2, data={'event_id': 'st-1'})
    )
    reporter.report_event(_event('system_tool_chunk', {'text': 'Other'}, position=3))
    reporter.report_event(_event('system_tool_chunk', {'text': 'Other'}, position=4))

    assert capsys.readouterr().out == 'LookingOtherOther'


def test_system_tool_progress_without_text_prints_a_working_line(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Text-free progress renders elapsed seconds and chunk count; repeats print nothing."""
    reporter = SimpleReporter(enabled=True)

    reporter.report_system_tool_progress(
        event_id='e', tool_name='think', chunk_count=3, elapsed_seconds=2.6
    )
    reporter.report_system_tool_progress(
        event_id='e', tool_name='think', chunk_count=1, elapsed_seconds=0, text='abc'
    )
    reporter.report_system_tool_progress(
        event_id='e', tool_name='think', chunk_count=2, elapsed_seconds=0, text='abc'
    )

    assert capsys.readouterr().out == f'{_line("Working", "think", "(3s, 3 chunks)")}\nabc'


def test_empty_chunk_prints_nothing(capsys: pytest.CaptureFixture[str]) -> None:
    """An empty response chunk changes nothing."""
    reporter = SimpleReporter(enabled=True)

    reporter.report_response_chunk('', assistant_id='a')

    assert capsys.readouterr().out == ''
    assert reporter.streamed_text_by_assistant == {}


def test_replacement_chunk_restarts_the_visible_stream(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Replaced content starts on a new line instead of mashing into the old answer."""
    reporter = SimpleReporter(enabled=True)

    reporter.report_response_chunk('first', assistant_id='a')
    reporter.report_response_chunk('second', assistant_id='a', replace_content=True)
    reporter.report_response_chunk(' part', assistant_id='a')

    assert capsys.readouterr().out == 'first\nsecond part'
    assert reporter.streamed_text_by_assistant == {'a': 'second part'}


def test_streamed_final_response_closes_the_line_once(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """A final response equal to the streamed text only terminates the line."""
    reporter = SimpleReporter(enabled=True)

    reporter.report_response_chunk('All done', assistant_id='a')
    reporter.print_final_response('All done')
    reporter.report_response_chunk('Next', assistant_id='a')

    assert capsys.readouterr().out == 'All done\nNext'


def test_get_input_breaks_an_open_stream_first(
    capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    """Input prompts start on their own line after streamed text."""
    monkeypatch.setattr('builtins.input', _bracket)
    reporter = SimpleReporter(enabled=True)

    reporter.report_response_chunk('Thinking', assistant_id='a')
    answer = reporter.get_input('Name? ')

    assert answer == '<Name? >'
    assert capsys.readouterr().out == 'Thinking\n'


# Section: Terminal blocks.


def test_unstreamed_final_response_prints_a_bordered_block(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """A final response that never streamed prints inside the bordered block."""
    reporter = SimpleReporter(enabled=True)

    reporter.print_final_response('Plain answer')

    assert capsys.readouterr().out == (
        f'\n{_BORDER}\nFINAL RESPONSE\n{_BORDER}\nPlain answer\n{_BORDER}\n'
    )


def test_final_result_block(capsys: pytest.CaptureFixture[str]) -> None:
    """Results print in a RESULT block; JSON strings are indented and ``None`` is skipped."""
    reporter = SimpleReporter(enabled=True)

    reporter.print_final_result(None)
    reporter.print_final_result('[1, 2]')
    reporter.print_final_result({'a': 1})

    assert capsys.readouterr().out == (
        f'\n{_BORDER}\nRESULT\n{_BORDER}\n[\n  1,\n  2\n]\n{_BORDER}\n'
        f"\n{_BORDER}\nRESULT\n{_BORDER}\n{{'a': 1}}\n{_BORDER}\n"
    )


def test_print_event_lines(capsys: pytest.CaptureFixture[str]) -> None:
    """Legacy display events capitalize and truncate their verb and append details."""
    reporter = SimpleReporter(enabled=True)

    reporter.print_event('success', 'Agent execution completed successfully!')
    reporter.print_event('error', 'Agent execution failed', {'error_code': 'x'})
    reporter.print_event('verylongeventtype', 'msg')
    reporter.print_event('', 'msg', {})

    assert capsys.readouterr().out.splitlines() == [
        _line('Success', 'Agent execution completed successfully!'),
        _line('Error', 'Agent execution failed', "{'error_code': 'x'}"),
        _line('Verylongeven', 'msg'),
        _line('Event', 'msg'),
    ]


def test_summary_block_with_every_token_line(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """The summary prints optional token lines only when nonzero, with grouping."""
    reporter = SimpleReporter(enabled=True)

    reporter.print_summary(
        {
            'total_tokens': 12_345,
            'input_tokens': 10_000,
            'output_tokens': True,
            'reasoning_tokens': 2_000,
            'cache_read_tokens': 0,
            'cache_creation_tokens': 7,
        }
    )
    reporter.print_summary('not a mapping')

    assert capsys.readouterr().out == (
        f'\n{_BORDER}\nEXECUTION SUMMARY\n{_BORDER}\n'
        'Tools Executed: 0\nTotal Time: 0.00s\n'
        f'{_BORDER}\nTOKEN USAGE\n'
        '  Total Tokens: 12,345\n  Input Tokens: 10,000\n  Output Tokens: 0\n'
        '  Reasoning Tokens: 2,000\n  Cache Creation: 7\n'
        f'{_BORDER}\n\n'
        f'\n{_BORDER}\nEXECUTION SUMMARY\n{_BORDER}\n'
        'Tools Executed: 0\nTotal Time: 0.00s\n'
        f'{_BORDER}\n\n'
    )


@pytest.mark.parametrize(
    ('provider', 'line'),
    [
        (
            {'provider_counts_complete': True, 'settled': True},
            'Provider counts complete; settled.',
        ),
        (
            {'provider_counts_complete': False, 'settled': False},
            'Provider counts incomplete; settlement pending.',
        ),
    ],
)
def test_response_reports_provider_usage_status(
    capsys: pytest.CaptureFixture[str], provider: dict[str, object], line: str
) -> None:
    """A response with provider usage ends with its completeness and settlement status."""
    usage: dict[str, object] = {
        'input_tokens': 1,
        'output_tokens': 2,
        'provider': {
            'scope': 'run',
            'calls': 1,
            'input_tokens': 1,
            'output_tokens': 2,
            'cache_read_input_tokens': 0,
            'cache_creation_input_tokens': 0,
            **provider,
        },
    }

    SimpleReporter(enabled=True).report_response(_response(usage=usage, text='Done'))

    assert capsys.readouterr().out.splitlines()[-1] == _line('Usage', line)


def test_response_without_provider_usage_ends_with_the_summary(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Without provider usage the output ends with the summary border."""
    SimpleReporter(enabled=True).report_response(_response(text='Done'))

    assert capsys.readouterr().out == (
        f'\n{_BORDER}\nFINAL RESPONSE\n{_BORDER}\nDone\n{_BORDER}\n'
        f'\n{_BORDER}\nEXECUTION SUMMARY\n{_BORDER}\n'
        'Tools Executed: 0\nTotal Time: 0.00s\n'
        f'{_BORDER}\n\n'
    )


@pytest.mark.parametrize('text', ['{not json', 'plain text', '  [1, 2', ''])
def test_display_response_returns_non_json_unchanged(text: str) -> None:
    """Text that is not a JSON document is returned exactly as given."""
    assert display_response(text) == text


def test_display_response_indents_json_and_keeps_unicode() -> None:
    """JSON documents are re-rendered with two-space indentation and raw unicode."""
    assert display_response(' {"name": "caf\\u00e9", "n": [1]} ') == (
        '{\n  "name": "café",\n  "n": [\n    1\n  ]\n}'
    )


# Section: Benchmark metrics.


def test_benchmark_metric_rejects_a_non_string_parent_and_non_dict_usage(
    capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    """A malformed parent id makes the identity incomplete; malformed usage is empty."""
    monkeypatch.setenv('MAIVN_BENCHMARK_METRICS', '1')
    reporter = SimpleReporter(enabled=True)

    reporter.report_event(
        _event(
            'final',
            {'usage': 'lots'},
            data={'session_id': 'ses-1', 'root_event_id': 'evt-1', 'parent_session_id': 5},
        )
    )

    assert capsys.readouterr().out.splitlines() == [
        'MAIVN_BENCHMARK_INVOCATION_METRIC '
        + json.dumps(
            {
                'status': 'final',
                'usage': {},
                'identity_complete': False,
                'timing_complete': False,
            },
            separators=(',', ':'),
        )
    ]


# Section: Rich tier.


def _rich() -> tuple[RichReporter, StringIO]:
    """Build an enabled Rich reporter writing uncolored text to a 40-column buffer."""
    reporter = RichReporter(enabled=True)
    buffer = StringIO()
    reporter.console = Console(file=buffer, force_terminal=False, width=40)
    return reporter, buffer


def _panels(output: str) -> list[tuple[str, list[str]]]:
    """Split Rich panel output into (title, body lines), independent of box glyphs.

    Rich picks rounded or square corners per platform, so only the 40-column
    frame, the title, and the padded body text are compared.
    """
    lines = output.splitlines()
    assert all(len(line) == 40 for line in lines)  # noqa: PLR2004 - the console width.
    panels: list[tuple[str, list[str]]] = []
    body: list[str] = []
    title = ''
    for line in lines:
        inner = line[1:-1]
        if inner.startswith(' ') and inner.endswith(' '):
            body.append(inner[1:].rstrip())
        elif not body and not title:
            title = inner.strip(inner[0]).strip()
        else:
            panels.append((title, body))
            title, body = '', []
    return panels


def test_rich_final_response_panel() -> None:
    """An unstreamed final response renders indented JSON in a titled panel."""
    reporter, buffer = _rich()

    reporter.print_final_response('{"a": 1}')

    assert _panels(buffer.getvalue()) == [('FINAL RESPONSE', ['{', '  "a": 1', '}'])]


def test_rich_streamed_final_response_only_ends_the_line() -> None:
    """A final response equal to the streamed text only terminates the line."""
    reporter, buffer = _rich()

    reporter.report_response_chunk('Done', assistant_id='a')
    reporter.print_final_response('Done')

    assert buffer.getvalue() == 'Done\n'
    assert reporter.streamed_text_by_assistant == {}


def test_rich_streamed_final_response_flushes_the_held_back_tail() -> None:
    """A tail held back as a possible placeholder prints with the final response (U05-D2)."""
    reporter, buffer = _rich()

    reporter.report_response_chunk('Use the set {', assistant_id='a')
    reporter.print_final_response('Use the set {')

    assert buffer.getvalue() == 'Use the set {\n'


def test_rich_second_stream_on_the_same_assistant_starts_clean() -> None:
    """After a final response, a new stream on the same id prints no stray blank line (U05-D2)."""
    reporter, buffer = _rich()

    reporter.report_response_chunk('first', assistant_id='a')
    reporter.print_final_response('first')
    reporter.report_response_chunk('second', assistant_id='a')

    assert buffer.getvalue() == 'first\nsecond'


def test_rich_summary_panel() -> None:
    """The summary renders in a panel with token lines after a blank line."""
    reporter, buffer = _rich()

    reporter.print_summary({'total_tokens': 1_234, 'input_tokens': 1_000, 'output_tokens': 234})
    reporter.print_summary()

    assert _panels(buffer.getvalue()) == [
        (
            'EXECUTION SUMMARY',
            [
                'Tools Executed: 0',
                'Total Time: 0.00s',
                '',
                'TOKEN USAGE',
                '  Total Tokens: 1,234',
                '  Input Tokens: 1,000',
                '  Output Tokens: 234',
            ],
        ),
        ('EXECUTION SUMMARY', ['Tools Executed: 0', 'Total Time: 0.00s']),
    ]


def test_rich_result_panel() -> None:
    """Results render in a RESULT panel; ``None`` is skipped and objects use ``str``."""
    reporter, buffer = _rich()

    reporter.print_final_result(None)
    reporter.print_final_result({'a': 1})
    reporter.print_final_result('[1]')

    assert _panels(buffer.getvalue()) == [
        ('RESULT', ["{'a': 1}"]),
        ('RESULT', ['[', '  1', ']']),
    ]


def test_rich_event_line_breaks_an_open_stream() -> None:
    """An event line after streamed text starts on a new line, with the gutter aligned."""
    reporter, buffer = _rich()

    reporter.report_response_chunk('Thinking', assistant_id='a')
    reporter.report_event(_event('system_tool_error', {'tool_name': 'scan', 'error': 'timeout'}))
    reporter.report_event(_event('status_message', {'message': 'Waiting'}))

    assert buffer.getvalue() == (
        f'Thinking\n{_line("Failed", "scan: timeout")}\n{_line("Status", "Waiting")}\n'
    )


def _bracket(prompt: str) -> str:
    """Answer an input prompt by echoing it in angle brackets."""
    return f'<{prompt}>'
