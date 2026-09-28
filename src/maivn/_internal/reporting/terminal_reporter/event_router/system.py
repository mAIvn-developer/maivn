"""System, enrichment, and assignment forwarding mixins for the event router."""

from __future__ import annotations

from typing import TYPE_CHECKING

from maivn._internal.reporting.terminal_reporter.event_categories import (
    forward_enrichment_with_fallback,
)

if TYPE_CHECKING:
    from collections.abc import Callable

    from maivn._internal.reporting.terminal_reporter.event_router._protocols import RoutedReporter


RouterPayload = dict[str, object]


class SystemRouterMixin:
    """Route legacy system-tool callbacks."""

    _reporter: RoutedReporter

    def _forward(
        self,
        *,
        category: str,
        event_name: str,
        payload: RouterPayload,
        forward: Callable[[], None],
    ) -> None:
        """Forward a category-filtered callback through the concrete router."""
        _ = (category, event_name, payload, forward)
        message = 'EventRouterReporter must implement _forward'
        raise NotImplementedError(message)

    def report_system_tool_start(self, tool_name: str, assignment_id: str) -> None:
        """Forward a system-tool start notification."""
        self._forward(
            category='system',
            event_name='system_tool_start',
            payload={'tool_name': tool_name, 'assignment_id': assignment_id},
            forward=lambda: self._reporter.report_system_tool_start(tool_name, assignment_id),
        )

    def report_system_tool_progress(
        self,
        event_id: str,
        tool_name: str,
        chunk_count: int,
        elapsed_seconds: float,
        text: str | None = None,
    ) -> None:
        """Forward a system-tool progress notification."""
        self._forward(
            category='system',
            event_name='system_tool_progress',
            payload={
                'event_id': event_id,
                'tool_name': tool_name,
                'chunk_count': chunk_count,
                'elapsed_seconds': elapsed_seconds,
                'text': text,
            },
            forward=lambda: self._reporter.report_system_tool_progress(
                event_id=event_id,
                tool_name=tool_name,
                chunk_count=chunk_count,
                elapsed_seconds=elapsed_seconds,
                text=text,
            ),
        )

    def report_system_tool_complete(self, tool_name: str, assignment_id: str) -> None:
        """Forward a system-tool completion notification."""
        self._forward(
            category='system',
            event_name='system_tool_complete',
            payload={'tool_name': tool_name, 'assignment_id': assignment_id},
            forward=lambda: self._reporter.report_system_tool_complete(tool_name, assignment_id),
        )


class EnrichmentRouterMixin:
    """Route enrichment and agent-assignment callbacks."""

    _reporter: RoutedReporter

    def _forward(
        self,
        *,
        category: str,
        event_name: str,
        payload: RouterPayload,
        forward: Callable[[], None],
    ) -> None:
        """Forward a category-filtered callback through the concrete router."""
        _ = (category, event_name, payload, forward)
        message = 'EventRouterReporter must implement _forward'
        raise NotImplementedError(message)

    def _is_enabled(self, category: str) -> bool:
        """Return whether the concrete router enables a category."""
        _ = category
        message = 'EventRouterReporter must implement _is_enabled'
        raise NotImplementedError(message)

    def _emit_to_sink(self, *, category: str, event_name: str, payload: RouterPayload) -> None:
        """Emit a forwarded event through the concrete router's external sink."""
        _ = (category, event_name, payload)
        message = 'EventRouterReporter must implement _emit_to_sink'
        raise NotImplementedError(message)

    def report_enrichment(  # noqa: PLR0913 - one parameter per enrichment field.
        self,
        *,
        phase: str,
        message: str,
        scope_id: str | None = None,
        scope_name: str | None = None,
        scope_type: str | None = None,
        memory: dict[str, object] | None = None,
        redaction: dict[str, object] | None = None,
        source: str | None = None,
        trigger_tool: str | None = None,
        target_tool: str | None = None,
        reevaluate_count: int | None = None,
        collected_count: int | None = None,
    ) -> None:
        """Forward enrichment while retaining backward-compatible reporter calls."""
        payload: RouterPayload = {
            'phase': phase,
            'message': message,
            'scope_id': scope_id,
            'scope_name': scope_name,
            'scope_type': scope_type,
            'memory': memory,
            'redaction': redaction,
            'source': source,
            'trigger_tool': trigger_tool,
            'target_tool': target_tool,
            'reevaluate_count': reevaluate_count,
            'collected_count': collected_count,
        }
        self._forward(
            category='enrichment',
            event_name='enrichment',
            payload=payload,
            forward=lambda: forward_enrichment_with_fallback(
                self._reporter,
                phase=phase,
                message=message,
                scope_id=scope_id,
                scope_name=scope_name,
                scope_type=scope_type,
                memory=memory,
                redaction=redaction,
                source=source,
                trigger_tool=trigger_tool,
                target_tool=target_tool,
                reevaluate_count=reevaluate_count,
                collected_count=collected_count,
            ),
        )

    def report_agent_assignment(self, **kwargs: object) -> None:
        """Forward a supported assignment callback and always emit its sink payload."""
        if not self._is_enabled('assignment'):
            return
        callback = getattr(self._reporter, 'report_agent_assignment', None)
        if callable(callback):
            callback(**kwargs)
        self._emit_to_sink(
            category='assignment',
            event_name='agent_assignment',
            payload=dict(kwargs),
        )


__all__ = ['EnrichmentRouterMixin', 'SystemRouterMixin']
