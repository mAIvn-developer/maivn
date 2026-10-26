"""Characterization tests for reporter context variables, tier fallbacks, and final projection."""

from __future__ import annotations

import contextvars
import sys
from io import StringIO

import pytest

from maivn._internal.models import StreamEvent
from maivn._internal.reporting import StreamEventProjector
from maivn._internal.reporting.context import (
    allow_nested_response_stream,
    current_reporter,
    current_sdk_delivery_mode,
    current_stream_event_forwarder,
    get_current_reporter,
    inside_orchestrator,
    set_current_reporter,
)
from maivn._internal.reporting.terminal_reporter import (
    RICH_AVAILABLE,
    SimpleReporter,
    detect_terminal_tier,
)

# Section: Context variables.


def test_context_variable_defaults() -> None:
    """A fresh context has no reporter, invoke delivery, and no nesting flags."""

    def read_defaults() -> tuple[object, ...]:
        return (
            current_reporter.get(),
            get_current_reporter(),
            inside_orchestrator.get(),
            allow_nested_response_stream.get(),
            current_sdk_delivery_mode.get(),
            current_stream_event_forwarder.get(),
        )

    assert contextvars.Context().run(read_defaults) == (None, None, False, False, 'invoke', None)


def test_set_current_reporter_is_visible_through_both_accessors() -> None:
    """Setting the reporter is visible through the getter and the variable, and clears."""
    reporter = SimpleReporter(enabled=False)

    def set_and_clear() -> tuple[object, object, object]:
        set_current_reporter(reporter)
        seen = (get_current_reporter(), current_reporter.get())
        set_current_reporter(None)
        return (*seen, get_current_reporter())

    assert contextvars.Context().run(set_and_clear) == (reporter, reporter, None)


def test_reporter_set_in_a_copied_context_does_not_leak() -> None:
    """A reporter set inside a copied context stays out of the caller's context."""
    reporter = SimpleReporter(enabled=False)
    outer = contextvars.Context()

    def inner_sets() -> None:
        set_current_reporter(reporter)

    outer.run(lambda: contextvars.copy_context().run(inner_sets))

    assert outer.run(get_current_reporter) is None


# Section: Terminal tier fallbacks.


class _NoIsatty:
    """A stdout stand-in without an ``isatty`` method."""

    encoding = 'utf-8'

    def write(self, text: str) -> int:
        """Accept and discard writes."""
        return len(text)


@pytest.mark.skipif(not RICH_AVAILABLE, reason='tier fallbacks only matter when rich is installed')
def test_stdout_without_isatty_selects_the_plain_tier(monkeypatch: pytest.MonkeyPatch) -> None:
    """A stdout that cannot report TTY state degrades to plain output."""
    for name in ('MAIVN_TERMINAL_TIER', 'NO_COLOR', 'CI', 'TERM'):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setattr(sys, 'stdout', _NoIsatty())

    assert detect_terminal_tier() == 0


@pytest.mark.skipif(not RICH_AVAILABLE, reason='tier fallbacks only matter when rich is installed')
def test_closed_stdout_selects_the_plain_tier(monkeypatch: pytest.MonkeyPatch) -> None:
    """A closed stdout (``isatty`` raising ``ValueError``) degrades to plain output."""
    for name in ('MAIVN_TERMINAL_TIER', 'NO_COLOR', 'CI', 'TERM'):
        monkeypatch.delenv(name, raising=False)
    closed = StringIO()
    closed.close()
    monkeypatch.setattr(sys, 'stdout', closed)

    assert detect_terminal_tier() == 0


class _Tty(StringIO):
    """A stdout stand-in that reports an interactive terminal."""

    def isatty(self) -> bool:
        """Report interactive terminal capability."""
        return True


@pytest.mark.skipif(not RICH_AVAILABLE, reason='tier overrides only matter when rich is installed')
@pytest.mark.parametrize('value', [' PLAIN ', 'simple', '0'])
def test_plain_overrides_are_trimmed_and_case_folded(
    monkeypatch: pytest.MonkeyPatch, value: str
) -> None:
    """Every plain override spelling selects plain output on an otherwise rich-capable TTY."""
    for name in ('NO_COLOR', 'CI', 'TERM'):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setattr(sys, 'stdout', _Tty())
    monkeypatch.delenv('MAIVN_TERMINAL_TIER', raising=False)
    assert detect_terminal_tier() == 1

    monkeypatch.setenv('MAIVN_TERMINAL_TIER', value)

    assert detect_terminal_tier() == 0


# Section: Final event projection.


def test_final_event_without_text_content_projects_an_empty_response() -> None:
    """A final message whose content is not text projects an empty response list."""
    event = StreamEvent(
        position=9,
        event_type='final',
        data={'payload': {'message': {'role': 'assistant', 'content': [{'type': 'image'}]}}},
    )

    projected = StreamEventProjector().project(event)

    assert projected.name == 'final'
    assert projected.payload == {
        'message': {'role': 'assistant', 'content': [{'type': 'image'}]},
        'response': '',
        'responses': [],
    }
    assert projected.data == event.data
