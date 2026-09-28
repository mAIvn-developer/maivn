"""Unit tests for the v1-compatible verbose terminal reporter."""

from __future__ import annotations

import inspect
import json
import time
from io import StringIO
from typing import TYPE_CHECKING

from maivn_contracts.messages import Message
from rich.console import Console

import maivn._internal.reporting.terminal_reporter.factory as reporter_factory
import maivn._internal.reporting.terminal_reporter.reporters as reporter_implementations
from maivn._internal.models import InvokeResponse, StreamEvent
from maivn._internal.reporting import terminal_reporter
from maivn._internal.reporting.terminal_reporter.factory import (
    TIER_ENV_VAR,
    TIER_PLAIN,
    TIER_RICH,
    create_reporter,
    detect_terminal_tier,
)
from maivn._internal.reporting.terminal_reporter.reporters.rich_reporter import RichReporter
from maivn._internal.reporting.terminal_reporter.reporters.simple_reporter import (
    GUTTER_WIDTH,
    SimpleReporter,
)

if TYPE_CHECKING:
    import pytest


def _response(*, usage: dict[str, object] | None = None) -> InvokeResponse:
    """Build an invocation response with a grouped token total."""
    return InvokeResponse(
        final_message=Message(
            message_id='msg-1',
            role='assistant',
            content='Completed.',
            ts='2026-07-12T00:00:00Z',
        ),
        session_id='ses-1',
        root_event_id='evt-1',
        event_positions=[1],
        usage=usage or {'input_tokens': 1_000, 'output_tokens': 234},
        response='Completed.',
    )


def _event(
    event_type: str,
    position: int,
    payload: dict[str, object] | None = None,
) -> StreamEvent:
    """Build a minimal reporter event with a canonical payload envelope."""
    return StreamEvent(
        position=position,
        event_type=event_type,
        data={'payload': payload if payload is not None else {'tool_name': f'tool-{position}'}},
    )


def test_terminal_error_displays_stable_code_without_private_details(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Native and projected error codes remain visible without exposing causes."""
    for payload in (
        {'error_code': 'task_admission_refused'},
        {'error': 'task_admission_refused'},
    ):
        SimpleReporter(enabled=True).report_event(
            _event('error', 1, {**payload, 'details': {'cause': 'private database text'}})
        )
        output = capsys.readouterr().out
        assert 'task_admission_refused' in output
        assert 'Unknown error' not in output
        assert 'private database text' not in output


def _clear_tier_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    """Remove every environment signal the tier detector consults."""
    for name in (TIER_ENV_VAR, 'NO_COLOR', 'CI', 'TERM'):
        monkeypatch.delenv(name, raising=False)


def _captured_rich_reporter() -> tuple[RichReporter, StringIO]:
    """Build an enabled Rich reporter writing plain text into a buffer."""
    reporter = RichReporter(enabled=True)
    output = StringIO()
    reporter.console = Console(file=output, force_terminal=False)
    return reporter, output


# MARK: Summary Contract


def test_default_reporter_prints_grouped_total_tokens(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Verbose output retains the exact token-summary line parsed by frozen demos."""
    reporter = create_reporter()

    reporter.report_response(_response())

    captured = capsys.readouterr()
    assert 'Total Tokens: 1,234' in captured.out


def test_reporter_prints_elapsed_time_and_observed_tool_count(
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The summary reports monotonic event-to-terminal elapsed time and tool starts."""
    now = [10.0]
    monkeypatch.setattr(time, 'monotonic', lambda: now[0])
    reporter = create_reporter()

    reporter.report_event(_event('system_tool_start', 1))
    reporter.report_event(_event('system_tool_start', 2))
    now[0] = 12.345
    reporter.report_event(_event('final', 3))
    reporter.report_response(_response())

    captured = capsys.readouterr()
    assert 'Tools Executed: 2' in captured.out
    assert 'Total Time: 2.35s' in captured.out


def test_reporter_prints_cache_lines_only_for_nonzero_cache_usage(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Cache counters are omitted at zero and shown when terminal usage includes them."""
    reporter = create_reporter()

    reporter.report_response(
        _response(
            usage={
                'input_tokens': 1_000,
                'output_tokens': 234,
                'cache_read_tokens': 0,
                'cache_creation_tokens': 0,
            }
        )
    )
    zero_cache_output = capsys.readouterr().out
    assert 'Cache Read:' not in zero_cache_output
    assert 'Cache Creation:' not in zero_cache_output

    reporter.report_response(
        _response(
            usage={
                'input_tokens': 1_000,
                'output_tokens': 234,
                'cache_read_tokens': 12,
                'cache_creation_tokens': 34,
            }
        )
    )
    cache_output = capsys.readouterr().out
    assert 'Cache Read: 12' in cache_output
    assert 'Cache Creation: 34' in cache_output


def test_benchmark_metric_is_opt_in_and_uses_terminal_event_hierarchy(
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Benchmarks receive a content-free root record only when explicitly enabled."""
    event = StreamEvent(
        position=1,
        event_type='final',
        data={
            'session_id': 'ses-root',
            'root_event_id': 'evt-root',
            'parent_session_id': None,
            'payload': {
                'usage': {
                    'input_tokens': 100,
                    'output_tokens': 20,
                    'cache_read_input_tokens': 30,
                    'cache_creation_input_tokens': 40,
                }
            },
        },
    )
    reporter = SimpleReporter(enabled=True)

    reporter.report_event(event)
    assert 'MAIVN_BENCHMARK_INVOCATION_METRIC' not in capsys.readouterr().out

    clock = [10.0]
    monkeypatch.setattr(time, 'monotonic', lambda: clock[0])
    monkeypatch.setenv('MAIVN_BENCHMARK_METRICS', '1')
    reporter.report_event(
        StreamEvent(
            position=2,
            event_type='planning',
            data={
                'session_id': 'ses-root',
                'root_event_id': 'evt-root',
                'parent_session_id': None,
                'payload': {},
            },
        )
    )
    clock[0] = 13.5
    reporter.report_event(event)
    line = next(
        line
        for line in capsys.readouterr().out.splitlines()
        if line.startswith('MAIVN_BENCHMARK_INVOCATION_METRIC ')
    )
    metric = json.loads(line.removeprefix('MAIVN_BENCHMARK_INVOCATION_METRIC '))

    assert metric == {
        'status': 'final',
        'timing_complete': True,
        'start_monotonic_seconds': 10.0,
        'end_monotonic_seconds': 13.5,
        'usage': {
            'input_tokens': 100,
            'output_tokens': 20,
            'cache_read_input_tokens': 30,
            'cache_creation_input_tokens': 40,
        },
        'identity_complete': True,
        'session_id': 'ses-root',
        'root_event_id': 'evt-root',
        'parent_session_id': None,
    }


def test_benchmark_metric_marks_missing_hierarchy_unknown_but_preserves_error_usage(
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An error's real token usage is recorded without guessing a missing parent field."""
    monkeypatch.setenv('MAIVN_BENCHMARK_METRICS', '1')
    reporter = SimpleReporter(enabled=True)
    reporter.report_event(
        StreamEvent(
            position=1,
            event_type='error',
            data={
                'session_id': 'ses-unknown-parent',
                'root_event_id': 'evt-error',
                'payload': {'usage': {'input_tokens': 100, 'output_tokens': 20}},
            },
        )
    )
    line = next(
        line
        for line in capsys.readouterr().out.splitlines()
        if line.startswith('MAIVN_BENCHMARK_INVOCATION_METRIC ')
    )
    metric = json.loads(line.removeprefix('MAIVN_BENCHMARK_INVOCATION_METRIC '))

    assert metric['status'] == 'error'
    assert metric['identity_complete'] is False
    assert metric['usage'] == {'input_tokens': 100, 'output_tokens': 20}
    assert 'parent_session_id' not in metric


def test_benchmark_metric_handles_session_completed_terminal_event(
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The canonical completed terminal event is accounted for alongside final."""
    monkeypatch.setenv('MAIVN_BENCHMARK_METRICS', '1')
    SimpleReporter(enabled=True).report_event(
        StreamEvent(
            position=1,
            event_type='session.completed',
            data={
                'session_id': 'ses-root',
                'root_event_id': 'evt-root',
                'parent_session_id': None,
                'payload': {'usage': {'input_tokens': 11, 'output_tokens': 7}},
            },
        )
    )

    line = next(
        line
        for line in capsys.readouterr().out.splitlines()
        if line.startswith('MAIVN_BENCHMARK_INVOCATION_METRIC ')
    )
    metric = json.loads(line.removeprefix('MAIVN_BENCHMARK_INVOCATION_METRIC '))

    assert metric['status'] == 'session.completed'
    assert metric['usage'] == {'input_tokens': 11, 'output_tokens': 7}


# MARK: Tier Detection


def test_detection_prefers_plain_without_a_tty(monkeypatch: pytest.MonkeyPatch) -> None:
    """Piped output (pytest capture included) selects the plain tier."""
    _clear_tier_environment(monkeypatch)
    monkeypatch.setattr(reporter_factory, 'RICH_AVAILABLE', True)
    monkeypatch.setattr('sys.stdout', StringIO())

    assert detect_terminal_tier() == TIER_PLAIN


def test_detection_selects_rich_on_a_capable_tty(monkeypatch: pytest.MonkeyPatch) -> None:
    """A TTY with rich installed and no opt-outs selects the rich tier."""
    _clear_tier_environment(monkeypatch)
    monkeypatch.setattr(reporter_factory, 'RICH_AVAILABLE', True)
    monkeypatch.setattr('sys.stdout', _Tty())

    assert detect_terminal_tier() == TIER_RICH


def test_detection_degrades_for_no_color_ci_and_dumb_terminals(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """NO_COLOR, CI, and TERM=dumb each force the plain tier on a TTY."""
    _clear_tier_environment(monkeypatch)
    monkeypatch.setattr(reporter_factory, 'RICH_AVAILABLE', True)
    monkeypatch.setattr('sys.stdout', _Tty())

    monkeypatch.setenv('NO_COLOR', '1')
    assert detect_terminal_tier() == TIER_PLAIN
    monkeypatch.delenv('NO_COLOR')

    monkeypatch.setenv('CI', 'true')
    assert detect_terminal_tier() == TIER_PLAIN
    monkeypatch.delenv('CI')

    monkeypatch.setenv('TERM', 'dumb')
    assert detect_terminal_tier() == TIER_PLAIN


def test_detection_degrades_without_rich_even_when_forced(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Forcing the rich tier without the rich package degrades instead of raising."""
    _clear_tier_environment(monkeypatch)
    monkeypatch.setattr(reporter_factory, 'RICH_AVAILABLE', False)
    monkeypatch.setenv(TIER_ENV_VAR, 'rich')

    assert detect_terminal_tier() == TIER_PLAIN


def test_explicit_override_wins_in_both_directions(monkeypatch: pytest.MonkeyPatch) -> None:
    """The override selects rich without a TTY and plain on a capable terminal."""
    _clear_tier_environment(monkeypatch)
    monkeypatch.setattr(reporter_factory, 'RICH_AVAILABLE', True)

    monkeypatch.setattr('sys.stdout', StringIO())
    monkeypatch.setenv(TIER_ENV_VAR, 'rich')
    assert detect_terminal_tier() == TIER_RICH

    monkeypatch.setattr('sys.stdout', _Tty())
    monkeypatch.setenv(TIER_ENV_VAR, 'plain')
    assert detect_terminal_tier() == TIER_PLAIN


def test_factory_selects_rich_reporter_when_forced(monkeypatch: pytest.MonkeyPatch) -> None:
    """The rich override produces the Rich reporter even with captured output."""
    _clear_tier_environment(monkeypatch)
    monkeypatch.setenv(TIER_ENV_VAR, 'rich')

    reporter = create_reporter()

    assert type(reporter).__name__ == 'RichReporter'
    assert type(reporter).__module__.endswith('.rich_reporter.reporter')


def test_factory_uses_disabled_simple_reporter_when_reporting_is_off() -> None:
    """Disabled reporting remains safe without initializing Rich output."""
    reporter = create_reporter(enabled=False)

    assert isinstance(reporter, SimpleReporter)
    assert reporter.enabled is False


def test_factory_uses_enabled_simple_reporter_when_rich_is_unavailable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An incomplete installation retains usable enabled verbose reporting."""
    monkeypatch.setattr(reporter_factory, 'RICH_AVAILABLE', False)

    reporter = reporter_factory.create_reporter()

    assert isinstance(reporter, SimpleReporter)
    assert reporter.enabled is True


def test_reporter_packages_resolve_rich_reporter_lazily() -> None:
    """Both package surfaces expose RichReporter without importing it eagerly."""
    assert 'RichReporter' in terminal_reporter.__all__
    assert 'RichReporter' in reporter_implementations.__all__
    assert terminal_reporter.RichReporter is reporter_implementations.RichReporter
    assert terminal_reporter.RichReporter is RichReporter


# MARK: Event Elevation


def test_tool_lifecycle_lines_dedupe_the_duplicated_completion(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """The wire's duplicate tool completion renders exactly one Completed line."""
    reporter = SimpleReporter(enabled=True)
    payload: dict[str, object] = {'tool_name': 'hardware_scanner', 'turn_index': 0}
    completed = dict(payload, outcome={'duration_ms': 1500, 'status': 'ok'})

    reporter.report_event(_event('system_tool_start', 1, payload))
    reporter.report_event(_event('system_tool_start', 2, dict(payload)))
    reporter.report_event(_event('system_tool_complete', 3, completed))
    reporter.report_event(_event('system_tool_complete', 4, dict(completed)))

    lines = [line for line in capsys.readouterr().out.splitlines() if line]
    running_line, completed_line = lines
    assert 'Running hardware_scanner' in running_line
    assert 'Completed hardware_scanner' in completed_line
    assert '(1.5s)' in completed_line  # runtime-measured duration from the outcome


def test_event_lines_share_one_aligned_gutter(capsys: pytest.CaptureFixture[str]) -> None:
    """Every event family aligns its verb to the same column."""
    reporter = SimpleReporter(enabled=True)

    reporter.report_event(_event('system_tool_start', 1, {'tool_name': 'scan'}))
    reporter.report_event(
        _event('enrichment', 2, {'phase': 'planning', 'message': 'Planning actions...'})
    )
    reporter.report_event(
        _event('model_routing', 3, {'mode': 'auto', 'model': 'model-x', 'complexity': 'simple'})
    )
    reporter.report_event(_event('status_message', 4, {'message': 'Dispatching 2 agents'}))
    reporter.report_event(_event('error', 5, {'message': 'boom'}))

    lines = [line for line in capsys.readouterr().out.splitlines() if line]
    tool_line, phase_line, model_line, status_line, error_line = lines
    gutter_ends = {len(line) - len(line.lstrip()) + len(line.split()[0]) for line in lines}
    assert gutter_ends == {GUTTER_WIDTH}
    assert 'Running scan' in tool_line
    assert 'Planning actions...' in phase_line
    assert 'auto -> model-x' in model_line
    assert '(simple)' in model_line
    assert 'Dispatching 2 agents' in status_line
    assert 'boom' in error_line


def test_tool_failure_line_carries_the_error_message(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """A failed tool renders one Failed line including the failure message."""
    reporter = SimpleReporter(enabled=True)
    payload: dict[str, object] = {'tool_name': 'scan', 'turn_index': 1, 'message': 'timeout'}

    reporter.report_event(_event('system_tool_error', 1, payload))
    reporter.report_event(_event('system_tool_error', 2, dict(payload)))

    lines = [line for line in capsys.readouterr().out.splitlines() if line]
    assert lines == [f'{"Failed".rjust(12)} scan: timeout']


def test_rich_reporter_renders_the_same_content_as_plain(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Tier parity: the Rich rendering carries every fact the plain rendering does."""
    events = [
        _event('system_tool_start', 1, {'tool_name': 'scan', 'agent_name': 'Agent A'}),
        _event('system_tool_complete', 2, {'tool_name': 'scan', 'agent_name': 'Agent A'}),
        _event('enrichment', 3, {'phase': 'planning', 'message': 'Planning actions...'}),
        _event('model_routing', 4, {'mode': 'auto', 'model': 'model-x', 'complexity': 'simple'}),
        _event('error', 5, {'message': 'boom'}),
    ]

    plain = SimpleReporter(enabled=True)
    for event in events:
        plain.report_event(event)
    plain_out = capsys.readouterr().out

    rich_reporter, rich_buffer = _captured_rich_reporter()
    for event in events:
        rich_reporter.report_event(event)

    for fact in (
        'Running',
        'scan',
        '(Agent A)',
        'Completed',
        'Planning actions...',
        'auto -> model-x',
        '(simple)',
        'Error',
        'boom',
    ):
        assert fact in plain_out
        assert fact in rich_buffer.getvalue()


def test_structured_final_response_pretty_prints_json(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """A JSON final response renders indented instead of as a single-line blob."""
    reporter = SimpleReporter(enabled=True)

    reporter.print_final_response('{"a": {"b": 1}}')

    output = capsys.readouterr().out
    assert '"a": {' in output
    assert '"b": 1' in output


# MARK: Interactive Input


def test_rich_reporter_retains_terminal_input_hooks() -> None:
    """The Rich reporter retains the duck-typed input surface used by interrupts."""
    reporter = RichReporter(enabled=True)

    with reporter.prepare_for_user_input():
        pass
    assert callable(reporter.get_input)


def test_simple_reporter_collects_input_without_rich(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Plain installs answer interrupt and follow-up prompts through builtin input."""
    reporter = SimpleReporter(enabled=True)
    observed_prompts: list[str] = []

    def capture_input(prompt: str) -> str:
        observed_prompts.append(prompt)
        return 'ACCT-290'

    monkeypatch.setattr('builtins.input', capture_input)

    with reporter.prepare_for_user_input():
        answer = reporter.get_input('What account ID should I use?')

    assert answer == 'ACCT-290'
    assert observed_prompts == ['What account ID should I use? ']


def test_rich_input_adds_one_separator_after_a_prompt_without_whitespace(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Terminal answers do not visually run into an authored follow-up question."""
    reporter = RichReporter(enabled=True)
    observed_prompts: list[str] = []

    def capture_input(prompt: str) -> str:
        observed_prompts.append(prompt)
        return 'ACCT-290'

    monkeypatch.setattr(reporter.console, 'input', capture_input)

    assert reporter.get_input('What account ID should I use?') == 'ACCT-290'
    assert observed_prompts == ['What account ID should I use? ']


def test_rich_input_preserves_a_prompt_that_already_ends_in_whitespace(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Existing prompt spacing remains byte-for-byte stable."""
    reporter = RichReporter(enabled=True)
    observed_prompts: list[str] = []

    def capture_input(prompt: str) -> str:
        observed_prompts.append(prompt)
        return 'ACCT-290'

    monkeypatch.setattr(reporter.console, 'input', capture_input)

    assert reporter.get_input('What account ID should I use?\n') == 'ACCT-290'
    assert observed_prompts == ['What account ID should I use?\n']


# MARK: Streaming Behavior


def test_rich_response_replacement_starts_a_new_visible_stream() -> None:
    """Replacement chunks preserve cumulative state without mashing distinct responses."""
    reporter, output = _captured_rich_reporter()

    assert 'replace_content' in inspect.signature(reporter.report_response_chunk).parameters
    reporter.report_response_chunk('first', assistant_id='assistant-1')
    reporter.report_response_chunk(
        'second',
        assistant_id='assistant-1',
        full_text='second',
        replace_content=True,
    )

    assert output.getvalue() == 'first\nsecond'
    assert reporter.streamed_text_by_assistant == {'assistant-1': 'second'}


def test_event_line_breaks_an_open_stream_before_printing(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """A tool line arriving mid-stream starts on its own line, never mid-sentence."""
    reporter = SimpleReporter(enabled=True)

    reporter.report_response_chunk('Thinking about it', assistant_id='a-1')
    reporter.report_event(_event('system_tool_start', 1, {'tool_name': 'scan'}))

    output = capsys.readouterr().out
    lines = output.splitlines()
    assert lines[0] == 'Thinking about it'
    assert 'Running scan' in lines[1]


class _Tty(StringIO):
    """Stand-in stdout that reports as an interactive terminal."""

    def isatty(self) -> bool:
        """Report interactive terminal capability."""
        return True
