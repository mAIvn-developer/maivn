# pyright: strict
from __future__ import annotations

from collections.abc import Generator
from contextlib import contextmanager
from typing import cast
from unittest.mock import MagicMock

from rich.console import Console

from maivn._internal.utils.reporting.terminal_reporter.reporters.rich_reporter import (
    reporter as rich_reporter,
)


@contextmanager
def _noop_cm() -> Generator[None, None, None]:
    yield


def _install_mock_attr(reporter: rich_reporter.RichReporter, name: str, value: object) -> None:
    """Install a mock onto a protected reporter attribute without tripping reportPrivateUsage."""
    setattr(reporter, name, value)


def _read_mock_attr(reporter: rich_reporter.RichReporter, name: str) -> object:
    """Read a protected reporter attribute without tripping reportPrivateUsage."""
    return cast(object, getattr(reporter, name))


def _child(mock: MagicMock, name: str) -> MagicMock:
    """Fetch a typed child ``MagicMock`` (avoids ``reportAny`` from ``MagicMock.__getattr__``)."""
    return cast(MagicMock, getattr(mock, name))


def test_rich_reporter_suspends_live_for_system_tool_streaming() -> None:
    reporter = rich_reporter.RichReporter(enabled=True)

    progress_manager = MagicMock()
    _child(progress_manager, "prepare_for_user_input").return_value = _noop_cm()

    tool_reporter = MagicMock()

    _install_mock_attr(reporter, "_progress_manager", progress_manager)
    _install_mock_attr(reporter, "_tool_reporter", tool_reporter)

    reporter.report_tool_start("repl", "evt-1", tool_type="system", agent_name=None, tool_args=None)
    _child(progress_manager, "suspend_live").assert_called_once()
    _child(tool_reporter, "report_tool_start").assert_called_once()
    _child(tool_reporter, "clear_streaming_state").assert_called_once()
    _child(progress_manager, "prepare_for_user_input").assert_not_called()

    reporter.report_system_tool_progress(
        event_id="evt-1",
        tool_name="repl",
        chunk_count=1,
        elapsed_seconds=0.1,
        text="print('hi')",
    )
    _child(tool_reporter, "report_system_tool_progress").assert_called_once()
    _child(progress_manager, "prepare_for_user_input").assert_not_called()

    reporter.report_tool_complete("evt-1", elapsed_ms=5, result={"ok": True})
    _child(tool_reporter, "report_tool_complete").assert_called_once()
    _child(tool_reporter, "clear_streaming_state").assert_called()
    _child(progress_manager, "resume_live").assert_called_once()
    _child(progress_manager, "prepare_for_user_input").assert_not_called()


def test_rich_reporter_only_resumes_live_after_last_system_tool_finishes() -> None:
    reporter = rich_reporter.RichReporter(enabled=True)

    progress_manager = MagicMock()
    _child(progress_manager, "prepare_for_user_input").return_value = _noop_cm()

    tool_reporter = MagicMock()

    _install_mock_attr(reporter, "_progress_manager", progress_manager)
    _install_mock_attr(reporter, "_tool_reporter", tool_reporter)

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
    _child(progress_manager, "suspend_live").assert_called_once()
    _child(tool_reporter, "clear_streaming_state").assert_called_once()

    reporter.report_tool_complete("evt-a", elapsed_ms=5, result=None)
    _child(progress_manager, "resume_live").assert_not_called()
    _child(tool_reporter, "clear_streaming_state").assert_called_once()

    reporter.report_tool_complete("evt-b", elapsed_ms=5, result=None)
    _child(progress_manager, "resume_live").assert_called_once()
    _child(tool_reporter, "clear_streaming_state").assert_called()


def test_rich_reporter_response_stream_suspends_live_and_tracks_full_text() -> None:
    reporter = rich_reporter.RichReporter(enabled=True)

    progress_manager = MagicMock()
    _install_mock_attr(reporter, "_progress_manager", progress_manager)

    console = MagicMock()
    reporter.console = cast(Console, console)

    reporter.report_response_chunk("Hello", assistant_id="assistant-1")
    reporter.report_response_chunk(" world", assistant_id="assistant-1")

    _child(progress_manager, "suspend_live").assert_called_once()
    assert _read_mock_attr(reporter, "_assistant_stream_live_suspended") is True
    assert _read_mock_attr(reporter, "_assistant_stream_text_by_id") == {
        "assistant-1": "Hello world"
    }
    assert _child(console, "print").call_count == 2


def test_rich_reporter_print_final_response_skips_duplicate_streamed_text() -> None:
    reporter = rich_reporter.RichReporter(enabled=True)

    progress_manager = MagicMock()
    _install_mock_attr(reporter, "_progress_manager", progress_manager)

    display_manager = MagicMock()
    _install_mock_attr(reporter, "_display_manager", display_manager)

    console = MagicMock()
    reporter.console = cast(Console, console)

    _install_mock_attr(reporter, "_assistant_stream_text_by_id", {"assistant": "done"})
    _install_mock_attr(reporter, "_assistant_stream_live_suspended", True)

    reporter.print_final_response("done")

    _child(console, "print").assert_called_once()
    _child(display_manager, "print_final_response").assert_not_called()
    _child(progress_manager, "resume_live").assert_called_once()
    assert _read_mock_attr(reporter, "_assistant_stream_text_by_id") == {}
    assert _read_mock_attr(reporter, "_assistant_stream_live_suspended") is False
