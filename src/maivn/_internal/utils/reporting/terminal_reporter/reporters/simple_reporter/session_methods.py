"""Session and tool lifecycle methods for ``SimpleReporter``."""

from __future__ import annotations

from typing import Any

from ..._formatters import format_session_id
from ..reporter_base import is_reevaluate_system_tool
from .display_helpers import (
    build_tool_start_details_simple,
    print_boxed_footer,
    print_boxed_header,
    print_boxed_row,
)

# MARK: Session Methods


class SimpleReporterSessionMixin:
    enabled: bool
    tracker: Any
    _box_horizontal: str
    _box_vertical: str
    _box_corners: Any
    print_event: Any
    print_section: Any

    def report_session_start(self, session_id: str, assistant_id: str) -> None:
        """Report session start."""
        formatted_id = format_session_id(session_id)
        self.print_event("INFO", f"Session started: {formatted_id} ({assistant_id})")

    def report_private_data(self, private_data: dict[str, Any]) -> None:
        """Report private data parameters."""
        if not self.enabled or not private_data:
            return

        box_width = 50
        print_boxed_header(
            title="Private Data Parameters",
            width=box_width,
            horizontal_char=self._box_horizontal,
            vertical_char=self._box_vertical,
            corners=self._box_corners,
        )

        for key, value in private_data.items():
            key_str = f"{key}:"
            content = f"  {key_str:<30} [REDACTED] ({type(value).__name__})"
            print_boxed_row(content=content, width=box_width, vertical_char=self._box_vertical)

        print_boxed_footer(
            width=box_width,
            horizontal_char=self._box_horizontal,
            corners=self._box_corners,
        )

    def report_phase_change(self, phase: str) -> None:
        """Report phase change."""
        self.tracker.set_phase(phase)
        self.print_section(f"Phase: {phase}")


# MARK: Tool Methods


class SimpleReporterToolMixin:
    enabled: bool
    tracker: Any
    print_event: Any

    def report_tool_start(
        self,
        tool_name: str,
        event_id: str,
        tool_type: str | None = None,
        agent_name: str | None = None,
        tool_args: dict[str, Any] | None = None,
        swarm_name: str | None = None,
    ) -> None:
        """Report tool execution start."""
        _ = swarm_name
        self.tracker.record_tool_start(
            tool_name,
            event_id,
            tool_type,
            agent_name,
            tool_args,
        )

        if is_reevaluate_system_tool(tool_name, tool_type):
            return

        details = build_tool_start_details_simple(tool_type, agent_name, tool_args)
        self.print_event("TOOL", f"Executing: {tool_name}", details)

    def report_model_tool_complete(
        self,
        tool_name: str,
        event_id: str | None = None,
        agent_name: str | None = None,
        swarm_name: str | None = None,
        result: Any | None = None,
    ) -> None:
        """Report MODEL tool execution completion."""
        _ = (event_id, agent_name, swarm_name)
        if not self.enabled:
            return

        self.tracker.record_model_tool()
        print(f"[MODEL] [SUCCESS] Complete: {tool_name}")
        if result is not None:
            from ..._formatters import truncate_result
            from .display_helpers import print_tool_child_lines

            print_tool_child_lines(result=result, truncate_fn=truncate_result)
