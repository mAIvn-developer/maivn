"""Selective legacy-event forwarding and external payload routing."""

from __future__ import annotations

import logging
import threading
from collections.abc import Callable, Iterable
from typing import TYPE_CHECKING, cast

from typing_extensions import override

from maivn._internal.reporting.terminal_reporter.base import BaseReporter
from maivn._internal.reporting.terminal_reporter.event_categories import normalize_event_categories

from .display import DisplayRouterMixin, ProgressRouterMixin
from .session import SessionRouterMixin
from .system import EnrichmentRouterMixin, SystemRouterMixin
from .tools import AssistantRouterMixin, ToolRouterMixin

if TYPE_CHECKING:
    from maivn._internal.models import InvokeResponse, StreamEvent
    from maivn._internal.reporting.terminal_reporter.event_router._protocols import RoutedReporter


EventPayload = dict[str, object]
EventPayloadSink = Callable[[EventPayload], None]

LOGGER = logging.getLogger(__name__)


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
    """Filter legacy reporter callbacks and optionally duplicate them to a payload sink."""

    def __init__(
        self,
        reporter: BaseReporter,
        *,
        include: Iterable[str] | str | None = None,
        exclude: Iterable[str] | str | None = None,
        event_sink: EventPayloadSink | None = None,
    ) -> None:
        """Wrap a reporter while preserving its enabled state and legacy callback behavior."""
        enabled = reporter.enabled
        super().__init__(enabled=enabled)
        self._reporter = cast('RoutedReporter', reporter)
        self._include_categories = normalize_event_categories(include)
        self._exclude_categories = normalize_event_categories(exclude) or set()
        self._event_sink = event_sink
        self._event_sink_lock = threading.RLock()
        self._tool_category_by_event_id: dict[str, str] = {}

    @override
    def report_event(self, event: StreamEvent) -> None:
        """Forward a v2 event while retaining router filtering and sink delivery."""
        payload: EventPayload = {
            'event_type': event.event_type,
            'name': event.name,
            'payload': event.payload,
            'data': event.data,
            'position': event.position,
        }
        self._forward(
            category=_category_for_stream_event(event.name),
            event_name='report_event',
            payload=payload,
            forward=lambda: self._reporter.report_event(event),
        )

    @override
    def report_response(self, response: InvokeResponse) -> None:
        """Forward a v2 invocation response as a response-category event."""
        self._forward(
            category='response',
            event_name='report_response',
            payload={'response': response},
            forward=lambda: self._reporter.report_response(response),
        )

    def _forward(
        self,
        *,
        category: str,
        event_name: str,
        payload: EventPayload,
        forward: Callable[[], None],
    ) -> None:
        """Forward and sink an event only when its category is enabled."""
        if not self._is_enabled(category):
            return
        forward()
        self._emit_to_sink(category=category, event_name=event_name, payload=payload)

    def _emit_to_sink(self, *, category: str, event_name: str, payload: EventPayload) -> None:
        """Deliver a normalized payload without letting sink failures disrupt reporting."""
        if self._event_sink is None:
            return
        try:
            with self._event_sink_lock:
                self._event_sink({'category': category, 'event': event_name, 'payload': payload})
        except Exception:
            LOGGER.exception('Event payload sink raised an exception')

    def _is_enabled(self, category: str) -> bool:
        """Return whether a category passes the configured include and exclude filters."""
        if category in self._exclude_categories:
            return False
        return self._include_categories is None or category in self._include_categories


def _category_for_stream_event(event_name: str) -> str:
    """Map v2 event names into the router's legacy filtering categories."""
    if event_name in {'update', 'assistant_chunk', 'response_chunk'}:
        return 'response'
    if event_name.startswith('system_tool_'):
        return 'system'
    if event_name.startswith('tool_'):
        return 'func'
    if event_name in {'enrichment', 'agent_assignment'}:
        return 'enrichment' if event_name == 'enrichment' else 'assignment'
    return 'lifecycle'


__all__ = ['EventPayloadSink', 'EventRouterReporter']
