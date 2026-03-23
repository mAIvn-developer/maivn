"""Session lifecycle forwarding mixin for EventRouterReporter."""

from __future__ import annotations

from typing import Any

# MARK: Session Forwarding


class SessionRouterMixin:
    _forward: Any
    _reporter: Any

    def report_session_start(
        self,
        session_id: str,
        assistant_id: str,
    ) -> None:
        self._forward(
            category="lifecycle",
            event_name="session_start",
            payload={"session_id": session_id, "assistant_id": assistant_id},
            forward=lambda: self._reporter.report_session_start(session_id, assistant_id),
        )

    def report_private_data(self, private_data: dict[str, Any]) -> None:
        self._forward(
            category="lifecycle",
            event_name="private_data",
            payload={"private_data": private_data},
            forward=lambda: self._reporter.report_private_data(private_data),
        )

    def report_phase_change(self, phase: str) -> None:
        self._forward(
            category="lifecycle",
            event_name="phase_change",
            payload={"phase": phase},
            forward=lambda: self._reporter.report_phase_change(phase),
        )
