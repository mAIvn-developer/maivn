from __future__ import annotations

from maivn._internal.utils.reporting.terminal_reporter.reporters.simple_reporter import (
    reporter as simple_reporter,
)


def test_simple_reporter_response_stream_accumulates_text(capsys) -> None:
    reporter = simple_reporter.SimpleReporter(enabled=True)

    reporter.report_response_chunk("Hello", assistant_id="assistant-1")
    reporter.report_response_chunk(" world", assistant_id="assistant-1")

    captured = capsys.readouterr()

    assert captured.out == "Hello world"
    assert reporter._assistant_stream_text_by_id == {"assistant-1": "Hello world"}
    assert reporter._assistant_stream_active is True


def test_simple_reporter_print_final_response_skips_duplicate_streamed_text(capsys) -> None:
    reporter = simple_reporter.SimpleReporter(enabled=True)
    reporter._assistant_stream_text_by_id = {"assistant": "done"}  # type: ignore[attr-defined]
    reporter._assistant_stream_active = True  # type: ignore[attr-defined]

    reporter.print_final_response("done")

    captured = capsys.readouterr()

    assert captured.out == "\n"
    assert reporter._assistant_stream_text_by_id == {}
    assert reporter._assistant_stream_active is False


def test_simple_reporter_system_tool_progress_prints_text_delta_only(capsys) -> None:
    reporter = simple_reporter.SimpleReporter(enabled=True)

    reporter.report_system_tool_progress(
        event_id="evt-1",
        tool_name="repl",
        chunk_count=1,
        elapsed_seconds=0.1,
        text="line 1",
    )
    reporter.report_system_tool_progress(
        event_id="evt-1",
        tool_name="repl",
        chunk_count=2,
        elapsed_seconds=0.2,
        text="line 1\nline 2",
    )

    captured = capsys.readouterr()

    assert [line for line in captured.out.splitlines() if line] == ["line 1", "line 2"]
