"""Process-wide listener registry for public SDK run telemetry."""

from __future__ import annotations

import itertools
import logging
from dataclasses import dataclass
from threading import RLock
from typing import TYPE_CHECKING

from ._projection import project_stream_event

if TYPE_CHECKING:
    from types import TracebackType

    from typing_extensions import Self

    from maivn._internal.models import StreamEvent

    from ._models import RunTelemetryEvent, TelemetryListener

_LOGGER = logging.getLogger('maivn.telemetry')
_LOCK = RLock()
_TOKENS = itertools.count(1)


@dataclass(frozen=True, slots=True)
class _Registration:
    listener: TelemetryListener


_LISTENERS: dict[int, _Registration] = {}


class TelemetrySubscription:
    """A removable listener registration returned by :func:`register_listener`."""

    def __init__(self, token: int) -> None:
        self._token = token
        self._closed = False

    @property
    def closed(self) -> bool:
        """Whether this subscription has been removed."""
        with _LOCK:
            return self._closed

    def close(self) -> None:
        """Remove the listener; repeated calls are safe."""
        with _LOCK:
            if self._closed:
                return
            _LISTENERS.pop(self._token, None)
            self._closed = True

    def __enter__(self) -> Self:
        return self

    def __exit__(
        self,
        _exc_type: type[BaseException] | None,
        _exc_value: BaseException | None,
        _traceback: TracebackType | None,
    ) -> None:
        self.close()


def register_listener(listener: TelemetryListener) -> TelemetrySubscription:
    """Register a process-wide listener for customer-visible run events.

    Listeners execute synchronously inline, in registration order, on the SDK
    stream's current thread or async task. They must return promptly because a
    slow listener directly delays the run. Heavy processing must be handed to
    the listener's own bounded queue or worker. The SDK creates no implicit
    worker, performs no buffering or silent dropping, and does not retry
    delivery. Listener exceptions are isolated so later listeners still run.

    The public schema is metadata-only and contains no content field. Message
    bodies, tool arguments/results, final results, and raw event payloads are
    never projected to listeners, regardless of their arrival form.
    """
    if not callable(listener):
        message = 'listener must be callable'
        raise TypeError(message)
    with _LOCK:
        token = next(_TOKENS)
        _LISTENERS[token] = _Registration(listener=listener)
    return TelemetrySubscription(token)


def publish_stream_event(event: StreamEvent) -> None:
    """Publish one event to a stable listener snapshot without leaking failures."""
    with _LOCK:
        listeners = tuple(_LISTENERS.values())
    projected = project_stream_event(event)
    for registration in listeners:
        _notify_listener(registration, projected, event_type=event.event_type)


def _notify_listener(
    registration: _Registration,
    event: RunTelemetryEvent,
    *,
    event_type: str,
) -> None:
    """Isolate one listener failure without logging its data or exception."""
    try:
        registration.listener(event)
    except Exception:  # noqa: BLE001 - listener bugs must not break customer runs.
        _LOGGER.warning(
            'Telemetry listener failed; listener_type=%s event_type=%s',
            type(registration.listener).__name__,
            event_type,
        )


def reset_listeners_for_testing() -> None:
    """Remove process-wide registrations between tests."""
    with _LOCK:
        _LISTENERS.clear()


__all__ = ['TelemetrySubscription', 'publish_stream_event', 'register_listener']
