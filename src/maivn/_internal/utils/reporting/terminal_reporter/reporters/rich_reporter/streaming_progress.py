from __future__ import annotations

import shutil
from typing import Any

from rich.text import Text


def report_system_tool_progress(
    *,
    console: Any,
    print_to_console: Any,
    event_id: str,
    tool_name: str,
    streaming_max_lines: int,
    previous_event_id: str | None,
    previous_lines_printed: int,
    elapsed_seconds: float,
    text_content: str | None,
) -> tuple[str | None, int]:
    supports_in_place_updates = bool(getattr(console.file, "isatty", lambda: False)()) and getattr(
        console, "is_terminal", False
    )

    is_same_event = previous_event_id == event_id

    if text_content:
        if not supports_in_place_updates:
            # Print the full streaming delta so the entire stream is preserved in the terminal.
            # (In non-TTY environments we can't do in-place updates anyway.)
            clean = text_content.replace("\r", "")
            short_id = str(event_id).strip()[:8]
            for line in clean.split("\n"):
                text = Text()
                text.append("[SYSTEM] ", style="bold blue")
                text.append(f"[{tool_name}:{short_id}] ", style="cyan")
                text.append(line if line else "...", style="dim")
                print_to_console(text)
            return previous_event_id, previous_lines_printed

        if is_same_event and previous_lines_printed > 0:
            console.file.write(f"\033[{previous_lines_printed}A")
            for _ in range(previous_lines_printed):
                console.file.write("\033[2K\033[1B")
            console.file.write(f"\033[{previous_lines_printed}A")
            console.file.flush()

        terminal_width = shutil.get_terminal_size(fallback=(120, 24)).columns
        max_content_width = max(40, terminal_width - 20)

        wrapped_lines: list[str] = []
        for line in text_content.split("\n"):
            if len(line) <= max_content_width:
                wrapped_lines.append(line)
            else:
                for i in range(0, len(line), max_content_width):
                    wrapped_lines.append(line[i : i + max_content_width])

        display_lines = (
            wrapped_lines if streaming_max_lines <= 0 else wrapped_lines[-streaming_max_lines:]
        )

        lines_printed = 0
        for line in display_lines:
            text = Text()
            text.append("[SYSTEM] ", style="bold blue")
            short_id = str(event_id).strip()[:8]
            text.append(f"[{tool_name}:{short_id}] ", style="cyan")
            line_text = line.replace("\r", "").strip()
            text.append(line_text if line_text else "...", style="dim italic")
            print_to_console(text)
            lines_printed += 1

        return event_id, lines_printed

    if not is_same_event:
        previous_event_id = event_id
        previous_lines_printed = 0

    if supports_in_place_updates and previous_lines_printed > 0:
        console.file.write(f"\033[{previous_lines_printed}A")
        for _ in range(previous_lines_printed):
            console.file.write("\033[2K\033[1B")
        console.file.write(f"\033[{previous_lines_printed}A")
        console.file.flush()

    text = Text()
    text.append("[SYSTEM] ", style="bold blue")
    text.append(f"[{tool_name}] ", style="cyan")
    text.append(f"Processing... {elapsed_seconds:.0f}s elapsed", style="dim")
    print_to_console(text)
    return previous_event_id, 1
