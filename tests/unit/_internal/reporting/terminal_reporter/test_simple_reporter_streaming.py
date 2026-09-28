"""Copied v1 simple-reporter streaming behavior tests."""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    import pytest

from maivn._internal.reporting.terminal_reporter.reporters.simple_reporter import SimpleReporter
from maivn._internal.reporting.terminal_reporter.reporters.simple_reporter import (
    reporter as reporter_module,
)


def test_simple_reporter_response_stream_accumulates_text(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Response chunks print immediately and accumulate by assistant."""
    reporter = SimpleReporter(enabled=True)

    reporter.report_response_chunk('Hello', assistant_id='assistant-1')
    reporter.report_response_chunk(' world', assistant_id='assistant-1')

    captured = capsys.readouterr()

    assert captured.out == 'Hello world'
    assert reporter.streamed_text_by_assistant == {'assistant-1': 'Hello world'}


def test_simple_reporter_print_final_response_skips_duplicate_streamed_text(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """A final response already streamed is not printed a second time."""
    reporter = SimpleReporter(enabled=True)
    reporter.report_response_chunk('done')

    reporter.print_final_response('done')

    captured = capsys.readouterr()

    assert captured.out == 'done\n'
    assert reporter.streamed_text_by_assistant == {}


def test_partial_private_token_does_not_reprint_the_answer_when_restored(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Hold only the unfinished marker while ordinary response text stays live."""
    reporter = SimpleReporter(enabled=True)
    for snapshot in ('Email: {', 'Email: {_', 'Email: {_{email}_'):
        reporter.report_response_chunk(snapshot, full_text=snapshot)
    assert capsys.readouterr().out == 'Email: '
    reporter.report_response_chunk(
        'Email: demo@example.com', full_text='Email: demo@example.com', replace_content=True
    )
    reporter.print_final_response('Email: demo@example.com')
    assert capsys.readouterr().out == 'demo@example.com\n'


def test_literal_incomplete_marker_is_flushed_at_completion(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """A literal brace may be delayed by one chunk but is never lost or duplicated."""
    reporter = SimpleReporter(enabled=True)
    reporter.report_response_chunk('Syntax {')
    assert capsys.readouterr().out == 'Syntax '
    reporter.print_final_response('Syntax {')
    assert capsys.readouterr().out == '{\n'


def test_simple_reporter_system_tool_progress_prints_text_delta_only(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """System-tool progress prints only the newly received text suffix."""
    reporter = SimpleReporter(enabled=True)

    reporter.report_system_tool_progress(
        event_id='evt-1',
        tool_name='repl',
        chunk_count=1,
        elapsed_seconds=0.1,
        text='line 1',
    )
    reporter.report_system_tool_progress(
        event_id='evt-1',
        tool_name='repl',
        chunk_count=2,
        elapsed_seconds=0.2,
        text='line 1\nline 2',
    )

    captured = capsys.readouterr()

    assert captured.out == 'line 1\nline 2'


def test_response_chunk_survives_a_console_that_cannot_encode_the_text(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Model text a cp1252 console cannot represent is replaced, never raised.

    Windows consoles default to cp1252. A single em-dash or emoji in a model response
    used to raise UnicodeEncodeError mid-stream and abort the whole invocation, so a
    run could die on the very content it had successfully produced.
    """
    monkeypatch.setattr(reporter_module, 'sys', _Cp1252Console())
    reporter = SimpleReporter(enabled=True)

    reporter.report_response_chunk('cost — 100€ 😀')

    assert '?' in capsys.readouterr().out


class _Cp1252Stdout:
    """Stand-in for a Windows console stream that cannot encode beyond cp1252."""

    encoding = 'cp1252'


class _Cp1252Console:
    """Stand-in for the reporter module's sys handle on a cp1252 console."""

    stdout = _Cp1252Stdout()
