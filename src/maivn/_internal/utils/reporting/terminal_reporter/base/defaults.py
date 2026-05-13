"""Shared default event implementations for terminal reporters."""

from __future__ import annotations

from abc import abstractmethod
from typing import Any


class ReporterDefaultEventsMixin:
    """Provide shared default implementations for optional reporter hooks."""

    @abstractmethod
    def print_event(
        self,
        event_type: str,
        message: str,
        details: dict[str, Any] | None = None,
    ) -> None:
        """Render a reporter event."""

    # MARK: - Assistant Streaming

    def report_response_chunk(
        self,
        text: str,
        *,
        assistant_id: str | None = None,
        full_text: str | None = None,
    ) -> None:
        """Report incremental assistant response text."""
        _ = (text, assistant_id, full_text)

    # MARK: - Status Messages

    def report_status_message(
        self,
        message: str,
        *,
        assistant_id: str | None = None,
    ) -> None:
        """Report a standalone status message."""
        _ = (message, assistant_id)

    # MARK: - Agent Assignment

    def report_agent_assignment(
        self,
        *,
        agent_name: str,
        status: str,
        assignment_id: str,
        swarm_name: str | None = None,
        error: str | None = None,
        result: Any | None = None,
    ) -> None:
        """Report an agent assignment lifecycle event.

        Default no-op. Implementations that surface per-agent assignment cards
        (Studio, custom UIs) override this. Terminal reporters intentionally
        leave it as a no-op.
        """
        _ = (agent_name, status, assignment_id, swarm_name, error, result)

    # MARK: - Hook Firing

    def report_hook_fired(
        self,
        *,
        name: str,
        stage: str,
        status: str,
        target_type: str,
        target_id: str | None = None,
        target_name: str | None = None,
        error: str | None = None,
        elapsed_ms: int | None = None,
    ) -> None:
        """Report a single developer-registered hook callback firing.

        Default no-op. Implementations that surface per-hook indicators on
        the appropriate target card (Studio's persistent header/footer
        markers, for example) override this. Terminal reporters intentionally
        leave it as a no-op.

        Args:
            name: Display name of the hook callable (usually ``__name__``).
            stage: ``"before"`` or ``"after"``.
            status: ``"completed"`` or ``"failed"``.
            target_type: ``"tool"`` / ``"agent"`` / ``"swarm"``.
            target_id: Per-invocation event id (tool target) or
                agent_id / swarm_name (scope target).
            target_name: Display name of the target card.
            error: Error message when ``status == "failed"``.
            elapsed_ms: Hook callable runtime in milliseconds.
        """
        _ = (name, stage, status, target_type, target_id, target_name, error, elapsed_ms)

    # MARK: - System Tools

    def report_system_tool_start(
        self,
        tool_name: str,
        assignment_id: str,
    ) -> None:
        """Report system tool execution start."""
        _ = assignment_id
        self.print_event("system", f"System tool started: {tool_name}")

    def report_system_tool_progress(
        self,
        event_id: str,
        tool_name: str,
        chunk_count: int,
        elapsed_seconds: float,
        text: str | None = None,
    ) -> None:
        """Report system tool execution progress."""
        _ = (event_id, chunk_count)
        if text:
            display_text = text[:100] + "..." if len(text) > 100 else text
            self.print_event("info", f"[{tool_name}] {display_text}")
            return

        self.print_event("info", f"[{tool_name}] Processing... {elapsed_seconds:.0f}s")

    def report_system_tool_complete(
        self,
        tool_name: str,
        assignment_id: str,
    ) -> None:
        """Report system tool execution completion."""
        _ = assignment_id
        self.print_event("success", f"System tool completed: {tool_name}")

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
        """Report an enrichment phase change event."""
        _ = (scope_id, scope_name, scope_type)
        suffix = ""
        if isinstance(memory, dict):
            metrics: list[str] = []
            for key, label in (
                ("registered_count", "registered"),
                ("reused_count", "reused"),
                ("skipped_count", "skipped"),
                ("chunk_count", "chunks"),
                ("discovery_count", "discovered"),
                ("selected_count", "selected"),
                ("version_superseded_count", "superseded"),
            ):
                value = memory.get(key)
                if isinstance(value, int):
                    metrics.append(f"{label}={value}")
            if metrics:
                suffix = f" ({', '.join(metrics)})"
        elif isinstance(redaction, dict):
            redaction_metrics: list[str] = []
            inserted_keys = redaction.get("inserted_keys")
            if isinstance(inserted_keys, list):
                redaction_metrics.append(f"keys={len(inserted_keys)}")
            for key, label in (
                ("redacted_message_count", "messages"),
                ("redacted_value_count", "values"),
            ):
                value = redaction.get(key)
                if isinstance(value, int):
                    redaction_metrics.append(f"{label}={value}")
            if redaction_metrics:
                suffix = f" ({', '.join(redaction_metrics)})"

        self.print_event("info", f"[{phase}] {message}{suffix}")
