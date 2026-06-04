# pyright: strict
"""System, enrichment, and assignment forwarding mixin for EventRouterReporter."""

from __future__ import annotations

from collections.abc import Callable
from typing import Protocol, cast

from ..base import BaseReporter
from ..event_categories import forward_enrichment_with_fallback

# MARK: Types

RouterPayload = dict[str, object]


class AssignmentCallback(Protocol):
    def __call__(self, **kwargs: object) -> None: ...


# MARK: System Forwarding


class SystemRouterMixin:
    _reporter: BaseReporter = cast(BaseReporter, cast(object, None))

    def _forward(
        self,
        *,
        category: str,
        event_name: str,
        payload: RouterPayload,
        forward: Callable[[], None],
    ) -> None:
        _ = (category, event_name, payload, forward)
        raise NotImplementedError

    def report_system_tool_start(
        self,
        tool_name: str,
        assignment_id: str,
    ) -> None:
        self._forward(
            category="system",
            event_name="system_tool_start",
            payload={
                "tool_name": tool_name,
                "assignment_id": assignment_id,
            },
            forward=lambda: self._reporter.report_system_tool_start(
                tool_name,
                assignment_id,
            ),
        )

    def report_system_tool_progress(
        self,
        event_id: str,
        tool_name: str,
        chunk_count: int,
        elapsed_seconds: float,
        text: str | None = None,
    ) -> None:
        self._forward(
            category="system",
            event_name="system_tool_progress",
            payload={
                "event_id": event_id,
                "tool_name": tool_name,
                "chunk_count": chunk_count,
                "elapsed_seconds": elapsed_seconds,
                "text": text,
            },
            forward=lambda: self._reporter.report_system_tool_progress(
                event_id=event_id,
                tool_name=tool_name,
                chunk_count=chunk_count,
                elapsed_seconds=elapsed_seconds,
                text=text,
            ),
        )

    def report_system_tool_complete(
        self,
        tool_name: str,
        assignment_id: str,
    ) -> None:
        self._forward(
            category="system",
            event_name="system_tool_complete",
            payload={
                "tool_name": tool_name,
                "assignment_id": assignment_id,
            },
            forward=lambda: self._reporter.report_system_tool_complete(
                tool_name,
                assignment_id,
            ),
        )


# MARK: Enrichment and Assignment


class EnrichmentRouterMixin:
    _reporter: BaseReporter = cast(BaseReporter, cast(object, None))

    def _forward(
        self,
        *,
        category: str,
        event_name: str,
        payload: RouterPayload,
        forward: Callable[[], None],
    ) -> None:
        _ = (category, event_name, payload, forward)
        raise NotImplementedError

    def _is_enabled(self, category: str) -> bool:
        _ = category
        raise NotImplementedError

    def _emit_to_sink(
        self,
        *,
        category: str,
        event_name: str,
        payload: RouterPayload,
    ) -> None:
        _ = (category, event_name, payload)
        raise NotImplementedError

    def report_enrichment(
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
        self._forward(
            category="enrichment",
            event_name="enrichment",
            payload={
                "phase": phase,
                "message": message,
                "scope_id": scope_id,
                "scope_name": scope_name,
                "scope_type": scope_type,
                "memory": memory,
                "redaction": redaction,
                "source": source,
                "trigger_tool": trigger_tool,
                "target_tool": target_tool,
                "reevaluate_count": reevaluate_count,
                "collected_count": collected_count,
            },
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
        if not self._is_enabled("assignment"):
            return
        callback = getattr(self._reporter, "report_agent_assignment", None)
        if callable(callback):
            cast(AssignmentCallback, callback)(**kwargs)
        self._emit_to_sink(
            category="assignment",
            event_name="agent_assignment",
            payload=dict(kwargs),
        )
