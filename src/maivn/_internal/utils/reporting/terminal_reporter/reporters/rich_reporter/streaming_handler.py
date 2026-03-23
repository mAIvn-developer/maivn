"""Streaming display handler for system tool progress in RichReporter.
Manages in-place terminal updates, panel rendering, and debug overlays.
"""

from __future__ import annotations

import time
from typing import TYPE_CHECKING

from ...config import (
    DEBUG_SYSTEM_TOOL_STREAM_BUFFER_MAX_CHARS,
    DEBUG_SYSTEM_TOOL_STREAM_DELIMITER,
    DEBUG_SYSTEM_TOOL_STREAM_DELIMITERS_ENABLED,
    TERMINAL_SYSTEM_TOOL_STREAM_MAX_FPS,
    TERMINAL_SYSTEM_TOOL_STREAM_MODE,
)
from .stream_state import SystemToolStreamDebugState, SystemToolStreamState
from .streaming_panel import render_streaming_panel_lines

if TYPE_CHECKING:
    from rich.console import Console


# MARK: Streaming Handler


class StreamingHandler:
    """Manages in-place system tool streaming display."""

    STREAMING_MAX_LINES = 50
    SYSTEM_TOOL_STREAM_INDENT = "  "

    def __init__(self, console: Console) -> None:
        self.console = console
        self._stream_state = SystemToolStreamState()

    @property
    def state(self) -> SystemToolStreamState:
        """Access the underlying stream state."""
        return self._stream_state

    # MARK: - Public Methods

    def get_display_text(
        self,
        event_id: str,
        chunk_count: int,
        text_content: str | None,
    ) -> str | None:
        """Get text content for display, with optional debug delimiters."""
        if not DEBUG_SYSTEM_TOOL_STREAM_DELIMITERS_ENABLED:
            return text_content
        return self._append_debug_block(event_id, chunk_count, text_content)

    def supports_in_place_updates(self) -> bool:
        """Check if console supports in-place updates."""
        return bool(getattr(self.console.file, "isatty", lambda: False)()) and getattr(
            self.console, "is_terminal", False
        )

    def handle_panel_progress(
        self,
        *,
        event_id: str,
        tool_name: str,
        elapsed_seconds: float,
        chunk_count: int,
        text_content: str,
        stream_group_event_ids: set[str] | None,
    ) -> None:
        """Handle panel-based streaming progress."""
        self._stream_state.upsert_panel(
            event_id=event_id,
            tool_name=tool_name,
            elapsed_seconds=elapsed_seconds,
            chunk_count=chunk_count,
            text_content=text_content,
        )

        if not self._should_render_frame():
            return

        self._stream_state.last_focus_event_id = event_id
        render_ids = self._stream_state.resolve_render_event_ids(
            stream_group_event_ids,
        )

        stream_mode = (
            str(
                TERMINAL_SYSTEM_TOOL_STREAM_MODE or "",
            )
            .strip()
            .lower()
        )
        if stream_mode not in {"stack", "focus"}:
            stream_mode = "focus"

        focus_id = self._stream_state.last_focus_event_id
        if stream_mode == "focus" and focus_id:
            render_ids = [focus_id]

        all_lines = self._render_panels(render_ids)

        if stream_mode == "focus" and focus_id:
            all_lines.extend(
                self._render_other_panels_summary(
                    focus_id,
                    stream_group_event_ids,
                ),
            )

        self._update_streaming_display(all_lines)

    def clear_state(self) -> None:
        """Clear streaming state. Call when tool execution completes."""
        self.finalize_stream_group()
        self._stream_state.debug_by_event_id = {}

    def finalize_stream_group(self) -> None:
        """Finalize the current in-place streaming region."""
        if self._stream_state.panels:
            render_ids = list(self._stream_state.panel_order)
            all_lines = self._render_panels(render_ids)
            if all_lines:
                self._update_streaming_display(all_lines)
                self.console.file.write("\n")
                self.console.file.flush()

        self._stream_state.reset_panel_state()

    # MARK: - Rendering

    def _should_render_frame(self) -> bool:
        """Check if we should render based on FPS limiting."""
        max_fps = int(TERMINAL_SYSTEM_TOOL_STREAM_MAX_FPS)
        if max_fps <= 0:
            return True

        now = time.monotonic()
        min_interval = 1.0 / float(max_fps)
        if (now - self._stream_state.last_render_time) < min_interval:
            return False

        self._stream_state.last_render_time = now
        return True

    def _render_panels(self, render_ids: list[str]) -> list[str]:
        """Render streaming panels for given event IDs."""
        all_lines: list[str] = []

        for idx, eid in enumerate(render_ids):
            panel_state = self._stream_state.panels.get(eid)
            if not panel_state:
                continue

            panel_lines = render_streaming_panel_lines(
                console=self.console,
                event_id=eid,
                tool_name=panel_state.tool_name,
                elapsed_seconds=panel_state.elapsed_seconds,
                chunk_count=panel_state.chunk_count,
                text_content=panel_state.text_content,
                max_lines=self.STREAMING_MAX_LINES,
                indent=len(self.SYSTEM_TOOL_STREAM_INDENT),
            )
            if idx > 0:
                all_lines.append("")
            all_lines.extend(panel_lines)

        return all_lines

    def _render_other_panels_summary(
        self,
        focus_id: str,
        stream_group_ids: set[str] | None,
    ) -> list[str]:
        """Render summary lines for non-focused panels."""
        other_ids = [eid for eid in self._stream_state.panel_order if eid != focus_id]
        if stream_group_ids:
            other_ids = [eid for eid in other_ids if eid in stream_group_ids]

        if not other_ids:
            return []

        lines = ["", "  Other streaming tools:"]
        for eid in other_ids[:10]:
            panel_state = self._stream_state.panels.get(eid)
            if not panel_state:
                continue

            short_id = str(eid).strip()[:8]
            label = str(panel_state.tool_name or "").strip().upper() or "STREAM"
            summary = (
                f"  - {label} ({short_id})"
                f" {panel_state.elapsed_seconds:.0f}s"
                f" chunks={panel_state.chunk_count}"
            )
            lines.append(summary)

        return lines

    def _update_streaming_display(self, lines: list[str]) -> None:
        """Update the streaming display with new content."""
        if self._stream_state.total_lines_printed > 0:
            self._clear_previous_lines(
                self._stream_state.total_lines_printed,
            )

        for line in lines:
            self.console.file.write(line + "\n")
        self.console.file.flush()
        self._stream_state.total_lines_printed = len(lines)

    def _clear_previous_lines(self, lines: int) -> None:
        """Clear previous streaming lines from terminal."""
        self.console.file.write(f"\033[{lines}A")
        for _ in range(lines):
            self.console.file.write("\033[2K\033[1B")
        self.console.file.write(f"\033[{lines}A")
        self.console.file.flush()

    # MARK: - Debug Helpers

    def _append_debug_block(
        self,
        event_id: str,
        chunk_count: int,
        text_content: str | None,
    ) -> str:
        """Append debug delimiter block to streaming text."""
        state = self._stream_state.debug_by_event_id.get(event_id)
        if state is None:
            state = SystemToolStreamDebugState()
            self._stream_state.debug_by_event_id[event_id] = state

        header = f"{DEBUG_SYSTEM_TOOL_STREAM_DELIMITER} chunk={chunk_count}"

        if text_content is None:
            body = "(no text)"
        else:
            clean = str(text_content).replace("\r", "")
            prev = state.last_full_text
            if isinstance(prev, str) and prev and clean.startswith(prev):
                delta = clean[len(prev) :]
                body = delta if delta.strip() else "(no delta)"
            else:
                body = clean if clean.strip() else "(empty text)"
            state.last_full_text = clean

        block = f"{header}\n{body}"
        if state.buffer:
            state.buffer += "\n" + block
        else:
            state.buffer = block

        max_chars = DEBUG_SYSTEM_TOOL_STREAM_BUFFER_MAX_CHARS
        if len(state.buffer) > max_chars:
            state.buffer = state.buffer[-max_chars:]

        return state.buffer
