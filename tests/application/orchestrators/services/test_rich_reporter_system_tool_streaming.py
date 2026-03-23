from __future__ import annotations

from contextlib import contextmanager
from unittest.mock import MagicMock

from maivn._internal.utils.reporting.terminal_reporter.reporters.rich_reporter import (
    reporter as rich_reporter,
)


@contextmanager
def _noop_cm():
    yield


def test_rich_reporter_suspends_live_for_system_tool_streaming() -> None:
    reporter = rich_reporter.RichReporter(enabled=True)

    progress_manager = MagicMock()
    progress_manager.suspend_live = MagicMock()
    progress_manager.resume_live = MagicMock()
    progress_manager.prepare_for_user_input = MagicMock(return_value=_noop_cm())

    tool_reporter = MagicMock()
    tool_reporter.report_tool_start = MagicMock()
    tool_reporter.report_system_tool_progress = MagicMock()
    tool_reporter.report_tool_complete = MagicMock()
    tool_reporter.report_tool_error = MagicMock()
    tool_reporter.clear_streaming_state = MagicMock()

    reporter._progress_manager = progress_manager  # type: ignore[attr-defined]
    reporter._tool_reporter = tool_reporter  # type: ignore[attr-defined]

    reporter.report_tool_start("repl", "evt-1", tool_type="system", agent_name=None, tool_args=None)
    progress_manager.suspend_live.assert_called_once()
    tool_reporter.report_tool_start.assert_called_once()
    tool_reporter.clear_streaming_state.assert_called_once()
    progress_manager.prepare_for_user_input.assert_not_called()

    reporter.report_system_tool_progress(
        event_id="evt-1",
        tool_name="repl",
        chunk_count=1,
        elapsed_seconds=0.1,
        text="print('hi')",
    )
    tool_reporter.report_system_tool_progress.assert_called_once()
    progress_manager.prepare_for_user_input.assert_not_called()

    reporter.report_tool_complete("evt-1", elapsed_ms=5, result={"ok": True})
    tool_reporter.report_tool_complete.assert_called_once()
    tool_reporter.clear_streaming_state.assert_called()
    progress_manager.resume_live.assert_called_once()
    progress_manager.prepare_for_user_input.assert_not_called()


def test_rich_reporter_only_resumes_live_after_last_system_tool_finishes() -> None:
    reporter = rich_reporter.RichReporter(enabled=True)

    progress_manager = MagicMock()
    progress_manager.suspend_live = MagicMock()
    progress_manager.resume_live = MagicMock()
    progress_manager.prepare_for_user_input = MagicMock(return_value=_noop_cm())

    tool_reporter = MagicMock()
    tool_reporter.report_tool_start = MagicMock()
    tool_reporter.report_tool_complete = MagicMock()
    tool_reporter.report_tool_error = MagicMock()
    tool_reporter.clear_streaming_state = MagicMock()

    reporter._progress_manager = progress_manager  # type: ignore[attr-defined]
    reporter._tool_reporter = tool_reporter  # type: ignore[attr-defined]

    reporter.report_tool_start(
        "think",
        "evt-a",
        tool_type="system",
        agent_name=None,
        tool_args=None,
    )
    reporter.report_tool_start(
        "repl",
        "evt-b",
        tool_type="system",
        agent_name=None,
        tool_args=None,
    )

    # Only the first system tool should suspend Live.
    progress_manager.suspend_live.assert_called_once()
    tool_reporter.clear_streaming_state.assert_called_once()

    reporter.report_tool_complete("evt-a", elapsed_ms=5, result=None)
    progress_manager.resume_live.assert_not_called()
    tool_reporter.clear_streaming_state.assert_called_once()

    reporter.report_tool_complete("evt-b", elapsed_ms=5, result=None)
    progress_manager.resume_live.assert_called_once()
    tool_reporter.clear_streaming_state.assert_called()


def test_rich_reporter_response_stream_suspends_live_and_tracks_full_text() -> None:
    reporter = rich_reporter.RichReporter(enabled=True)

    progress_manager = MagicMock()
    progress_manager.suspend_live = MagicMock()
    progress_manager.resume_live = MagicMock()
    reporter._progress_manager = progress_manager  # type: ignore[attr-defined]

    console = MagicMock()
    console.print = MagicMock()
    reporter.console = console  # type: ignore[attr-defined]

    reporter.report_response_chunk("Hello", assistant_id="assistant-1")
    reporter.report_response_chunk(" world", assistant_id="assistant-1")

    progress_manager.suspend_live.assert_called_once()
    assert reporter._assistant_stream_live_suspended is True
    assert reporter._assistant_stream_text_by_id == {"assistant-1": "Hello world"}
    assert console.print.call_count == 2


def test_rich_reporter_print_final_response_skips_duplicate_streamed_text() -> None:
    reporter = rich_reporter.RichReporter(enabled=True)

    progress_manager = MagicMock()
    progress_manager.resume_live = MagicMock()
    reporter._progress_manager = progress_manager  # type: ignore[attr-defined]

    display_manager = MagicMock()
    display_manager.print_final_response = MagicMock()
    reporter._display_manager = display_manager  # type: ignore[attr-defined]

    console = MagicMock()
    console.print = MagicMock()
    reporter.console = console  # type: ignore[attr-defined]

    reporter._assistant_stream_text_by_id = {"assistant": "done"}  # type: ignore[attr-defined]
    reporter._assistant_stream_live_suspended = True  # type: ignore[attr-defined]

    reporter.print_final_response("done")

    console.print.assert_called_once()
    display_manager.print_final_response.assert_not_called()
    progress_manager.resume_live.assert_called_once()
    assert reporter._assistant_stream_text_by_id == {}
    assert reporter._assistant_stream_live_suspended is False
