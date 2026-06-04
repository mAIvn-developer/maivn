# pyright: strict
from __future__ import annotations

from collections.abc import Sequence

from rich import box
from rich.align import Align
from rich.console import Console, RenderableType
from rich.padding import Padding
from rich.panel import Panel
from rich.text import Text

from ...config import SECTION_BORDER_STYLE

# MARK: Stream Line Formatting


def _normalize_stream_lines(text_content: str, *, max_lines: int) -> list[str]:
    if not text_content:
        return [""]

    raw_lines: list[str] = []
    for line in text_content.split("\n"):
        clean = line.replace("\r", "")
        raw_lines.append(clean)

    if max_lines > 0 and len(raw_lines) > max_lines:
        return raw_lines[-max_lines:]

    return raw_lines


def _format_tool_stream_label(tool_name: str) -> str:
    """Format a user-friendly system-tool label for streaming panels."""
    normalized = str(tool_name or "").strip().lower()
    if normalized == "think":
        return "THINKING"
    if normalized == "repl":
        return "REPL"
    return str(tool_name or "").strip().upper() or "STREAM"


# MARK: Panel Rendering


def _build_streaming_panel(
    *,
    event_id: str | None,
    tool_name: str,
    elapsed_seconds: float,
    chunk_count: int,
    lines: Sequence[str],
) -> Panel:
    event_suffix = ""
    if event_id:
        clean_id = str(event_id).strip()
        if clean_id:
            event_suffix = f" ({clean_id[:8]})"

    tool_label = _format_tool_stream_label(tool_name)
    title = Text()
    _ = title.append(tool_label, style="bold cyan")
    if event_suffix:
        _ = title.append(event_suffix, style="dim")
    _ = title.append(f"  {elapsed_seconds:.0f}s  chunks={chunk_count}", style="dim")

    body = Text()
    for idx, line in enumerate(lines):
        if idx > 0:
            _ = body.append("\n")
        _ = body.append(line, style="dim")

    return Panel(
        Align.left(body),
        title=title,
        border_style=SECTION_BORDER_STYLE,
        box=box.SIMPLE,
        padding=(0, 2),
    )


def render_streaming_panel_lines(
    *,
    console: Console,
    event_id: str | None,
    tool_name: str,
    elapsed_seconds: float,
    chunk_count: int,
    text_content: str,
    max_lines: int,
    indent: int = 0,
) -> list[str]:
    lines = _normalize_stream_lines(text_content, max_lines=max_lines)
    panel = _build_streaming_panel(
        event_id=event_id,
        tool_name=tool_name,
        elapsed_seconds=elapsed_seconds,
        chunk_count=chunk_count,
        lines=lines,
    )
    renderable: RenderableType = panel
    if indent > 0:
        renderable = Padding(panel, (0, 0, 0, indent))

    try:
        with console.capture() as capture:
            console.print(renderable)
        rendered = str(capture.get())
    except Exception:  # noqa: BLE001 - Rich capture failures fall back to a blank frame.
        rendered = ""

    rendered_lines: list[str] = rendered.splitlines()
    return rendered_lines if rendered_lines else [""]
