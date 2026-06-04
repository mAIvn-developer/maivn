# pyright: strict
from __future__ import annotations

from typing import cast

import pytest

from maivn._internal.utils.reporting.terminal_reporter.reporters.simple_reporter import (
    reporter as simple_reporter,
)


def _install_attr(reporter: simple_reporter.SimpleReporter, name: str, value: object) -> None:
    """Install a value onto a protected reporter attribute without tripping reportPrivateUsage."""
    setattr(reporter, name, value)


def _read_attr(reporter: simple_reporter.SimpleReporter, name: str) -> object:
    """Read a protected reporter attribute without tripping reportPrivateUsage."""
    return cast(object, getattr(reporter, name))


def test_simple_reporter_response_stream_accumulates_text(
    capsys: pytest.CaptureFixture[str],
) -> None:
    reporter = simple_reporter.SimpleReporter(enabled=True)

    reporter.report_response_chunk("Hello", assistant_id="assistant-1")
    reporter.report_response_chunk(" world", assistant_id="assistant-1")

    captured = capsys.readouterr()

    assert captured.out == "Hello world"
    assert _read_attr(reporter, "_assistant_stream_text_by_id") == {"assistant-1": "Hello world"}
    assert _read_attr(reporter, "_assistant_stream_active") is True


def test_simple_reporter_print_final_response_skips_duplicate_streamed_text(
    capsys: pytest.CaptureFixture[str],
) -> None:
    reporter = simple_reporter.SimpleReporter(enabled=True)
    _install_attr(reporter, "_assistant_stream_text_by_id", {"assistant": "done"})
    _install_attr(reporter, "_assistant_stream_active", True)

    reporter.print_final_response("done")

    captured = capsys.readouterr()

    assert captured.out == "\n"
    assert _read_attr(reporter, "_assistant_stream_text_by_id") == {}
    assert _read_attr(reporter, "_assistant_stream_active") is False


def test_simple_reporter_system_tool_progress_prints_text_delta_only(
    capsys: pytest.CaptureFixture[str],
) -> None:
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
