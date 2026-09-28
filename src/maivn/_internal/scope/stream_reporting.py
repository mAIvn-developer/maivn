"""Verbose terminal reporting wrapped around a scope's event stream."""

from __future__ import annotations

from typing import TYPE_CHECKING

from maivn._internal.compat.invocation import response_from_stream_events

if TYPE_CHECKING:
    from collections.abc import Generator, Iterator

    from maivn._internal.models import StreamEvent
    from maivn._internal.reporting.terminal_reporter.base import BaseReporter


def reported_stream(
    events: Iterator[StreamEvent],
    reporter: BaseReporter,
) -> Generator[StreamEvent, None, None]:
    """Yield an event stream while rendering its v1-visible verbose report."""
    try:
        for event in events:
            yield report_event(event, reporter)
    finally:
        close = getattr(events, 'close', None)
        if callable(close):
            close()


def report_event(event: StreamEvent, reporter: BaseReporter | None) -> StreamEvent:
    """Render one event and finalize the report without mutating canonical event fields."""
    if reporter is None:
        return event
    reporter.report_event(event)
    if event.event_type == 'final':
        reporter.report_response(response_from_stream_events((event,)))
    return event
