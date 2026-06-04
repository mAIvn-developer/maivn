# pyright: strict
"""Reporter wrapper for selective event forwarding and external payload routing."""

from __future__ import annotations

import logging
import threading
from collections.abc import Callable, Iterable

from typing_extensions import override

from ..base import BaseReporter
from ..event_categories import normalize_event_categories
from .display import DisplayRouterMixin, ProgressRouterMixin
from .session import SessionRouterMixin
from .system import EnrichmentRouterMixin, SystemRouterMixin
from .tools import AssistantRouterMixin, ToolRouterMixin

# MARK: Types


EventPayload = dict[str, object]
EventPayloadSink = Callable[[EventPayload], None]


# MARK: Configuration


LOGGER = logging.getLogger(__name__)


# MARK: Event Router Reporter


class EventRouterReporter(
    DisplayRouterMixin,
    ProgressRouterMixin,
    SessionRouterMixin,
    ToolRouterMixin,
    AssistantRouterMixin,
    SystemRouterMixin,
    EnrichmentRouterMixin,
    BaseReporter,
):
    """Reporter adapter that filters event categories and forwards to a sink."""

    def __init__(
        self,
        reporter: BaseReporter,
        *,
        include: Iterable[str] | str | None = None,
        exclude: Iterable[str] | str | None = None,
        event_sink: EventPayloadSink | None = None,
    ) -> None:
        enabled = bool(getattr(reporter, "enabled", True))
        super().__init__(enabled=enabled)
        self._reporter: BaseReporter = reporter
        self._include_categories: set[str] | None = normalize_event_categories(include)
        self._exclude_categories: set[str] = normalize_event_categories(exclude) or set()
        self._event_sink: EventPayloadSink | None = event_sink
        self._event_sink_lock: threading.RLock = threading.RLock()
        self._tool_category_by_event_id: dict[str, str] = {}
        self.enabled: bool = enabled

    @override
    def _forward(
        self,
        *,
        category: str,
        event_name: str,
        payload: EventPayload,
        forward: Callable[[], None],
    ) -> None:
        if not self._is_enabled(category):
            return
        forward()
        self._emit_to_sink(
            category=category,
            event_name=event_name,
            payload=payload,
        )

    @override
    def _emit_to_sink(
        self,
        *,
        category: str,
        event_name: str,
        payload: EventPayload,
    ) -> None:
        if self._event_sink is None:
            return
        try:
            with self._event_sink_lock:
                self._event_sink(
                    {
                        "category": category,
                        "event": event_name,
                        "payload": payload,
                    }
                )
        except Exception:  # noqa: BLE001
            # Sink failures must not break terminal reporting.
            LOGGER.exception("Event payload sink raised an exception")

    @override
    def _is_enabled(self, category: str) -> bool:
        if category in self._exclude_categories:
            return False
        if self._include_categories is None:
            return True
        return category in self._include_categories


__all__ = [
    "EventPayloadSink",
    "EventRouterReporter",
]
