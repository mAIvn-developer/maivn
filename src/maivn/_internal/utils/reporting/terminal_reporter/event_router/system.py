"""System, enrichment, and assignment forwarding mixin for EventRouterReporter."""

from __future__ import annotations

from typing import Any

from ..event_categories import forward_enrichment_with_fallback

# MARK: System Forwarding


class SystemRouterMixin:
    _forward: Any
    _reporter: Any

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
    _forward: Any
    _reporter: Any
    _is_enabled: Any
    _emit_to_sink: Any

    def report_enrichment(
        self,
        *,
        phase: str,
        message: str,
        scope_id: str | None = None,
        scope_name: str | None = None,
        scope_type: str | None = None,
        memory: dict[str, Any] | None = None,
        redaction: dict[str, Any] | None = None,
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
            ),
        )

    def report_agent_assignment(self, **kwargs: Any) -> None:
        if not self._is_enabled("assignment"):
            return
        callback = getattr(self._reporter, "report_agent_assignment", None)
        if callable(callback):
            callback(**kwargs)
        self._emit_to_sink(
            category="assignment",
            event_name="agent_assignment",
            payload=dict(kwargs),
        )
