"""System tool streaming state management for RichReporter."""

from __future__ import annotations

from dataclasses import dataclass

# MARK: Panel State


@dataclass
class SystemToolStreamPanelState:
    """State for a single streaming panel."""

    tool_name: str
    elapsed_seconds: float
    chunk_count: int
    text_content: str


# MARK: Debug State


@dataclass
class SystemToolStreamDebugState:
    """Debug state for streaming text with delimiters."""

    buffer: str = ""
    last_full_text: str | None = None


# MARK: Aggregate Stream State


class SystemToolStreamState:
    """Manages all system tool streaming state."""

    def __init__(self) -> None:
        self.total_lines_printed: int = 0
        self.panel_order: list[str] = []
        self.panels: dict[str, SystemToolStreamPanelState] = {}
        self.debug_by_event_id: dict[str, SystemToolStreamDebugState] = {}
        self.last_render_time: float = 0.0
        self.last_focus_event_id: str | None = None

    def reset_panel_state(self) -> None:
        """Reset panel-related state for new streaming group."""
        self.total_lines_printed = 0
        self.panel_order = []
        self.panels = {}
        self.last_render_time = 0.0
        self.last_focus_event_id = None

    def upsert_panel(
        self,
        *,
        event_id: str,
        tool_name: str,
        elapsed_seconds: float,
        chunk_count: int,
        text_content: str,
    ) -> None:
        """Insert or update a panel state."""
        if event_id not in self.panels:
            self.panel_order.append(event_id)

        self.panels[event_id] = SystemToolStreamPanelState(
            tool_name=str(tool_name or "").strip(),
            elapsed_seconds=float(elapsed_seconds or 0.0),
            chunk_count=int(chunk_count or 0),
            text_content=str(text_content or ""),
        )

    def resolve_render_event_ids(
        self,
        stream_group_ids: set[str] | None,
    ) -> list[str]:
        """Resolve which event IDs to render."""
        if not stream_group_ids:
            return list(self.panel_order)
        return [eid for eid in self.panel_order if eid in stream_group_ids]
