"""Listener lifecycle and synchronous delivery contract tests."""

from __future__ import annotations

import logging
import threading

from maivn._internal.models import StreamEvent
from maivn.telemetry import RunTelemetryEvent, register_listener
from maivn.telemetry._registry import publish_stream_event, reset_listeners_for_testing


def setup_function() -> None:
    """Start each test with no process-wide listeners."""
    reset_listeners_for_testing()


def teardown_function() -> None:
    """Remove process-wide listeners after each test."""
    reset_listeners_for_testing()


def _event(position: int = 1) -> StreamEvent:
    return StreamEvent(
        position=position,
        event_type='progress_update',
        data={
            'event_id': f'evt-{position}',
            'session_id': 'ses-1',
            'payload': {'status': 'running'},
        },
    )


def test_listeners_run_inline_in_registration_order() -> None:
    """Delivery happens on the caller's thread and preserves registration order."""
    calls: list[tuple[str, int]] = []
    caller_thread = threading.get_ident()
    first = register_listener(lambda _event: calls.append(('first', threading.get_ident())))
    second = register_listener(lambda _event: calls.append(('second', threading.get_ident())))

    publish_stream_event(_event())

    assert calls == [('first', caller_thread), ('second', caller_thread)]
    first.close()
    second.close()


def test_listener_failure_isolated_without_logging_sensitive_values(caplog: object) -> None:
    """One listener cannot break runs or leak its exception text into SDK logs."""
    calls: list[str] = []

    def broken(_event: object) -> None:
        message = 'RESOLVED-PRIVATE-VALUE-MUST-NOT-BE-LOGGED'
        raise RuntimeError(message)

    register_listener(broken)
    register_listener(lambda _event: calls.append('later'))

    with caplog.at_level(logging.WARNING, logger='maivn.telemetry'):  # type: ignore[attr-defined]
        publish_stream_event(_event())

    assert calls == ['later']
    assert 'listener_type=function' in caplog.text  # type: ignore[attr-defined]
    assert 'event_type=progress_update' in caplog.text  # type: ignore[attr-defined]
    assert 'RESOLVED-PRIVATE-VALUE' not in caplog.text  # type: ignore[attr-defined]


def test_subscription_close_is_idempotent_and_context_managed() -> None:
    """A subscription owns one removable registration."""
    calls: list[int] = []
    subscription = register_listener(lambda event: calls.append(event.position))

    publish_stream_event(_event(1))
    subscription.close()
    subscription.close()
    publish_stream_event(_event(2))
    with register_listener(lambda event: calls.append(event.position * 10)):
        publish_stream_event(_event(3))
    publish_stream_event(_event(4))

    assert calls == [1, 30]


def test_dispatch_uses_a_snapshot_when_listener_mutates_registry() -> None:
    """Registry mutation affects the next event, never the current iteration."""
    calls: list[str] = []
    subscriptions: list[object] = []

    def first(_event: object) -> None:
        calls.append('first')
        subscriptions.append(register_listener(lambda _next: calls.append('late')))

    register_listener(first)
    publish_stream_event(_event(1))
    publish_stream_event(_event(2))

    assert calls == ['first', 'first', 'late']


def test_listener_events_have_no_content_surface() -> None:
    """Arrival-form message content is absent from every listener object."""
    observed: list[RunTelemetryEvent] = []
    event = StreamEvent(
        position=1,
        event_type='status_message_chunk',
        data={
            'session_id': 'ses-1',
            'payload': {'content_delta': 'PLACEHOLDER {_{private}_}'},
        },
    )
    register_listener(observed.append)

    publish_stream_event(event)

    assert len(observed) == 1
    assert not hasattr(observed[0], 'content')
    assert 'PLACEHOLDER' not in observed[0].model_dump_json()
