"""Terminal configuration and input handling for RichReporter."""

from __future__ import annotations

import shutil
import sys
from typing import Any

from prompt_toolkit import prompt as pt_prompt
from prompt_toolkit.formatted_text import HTML
from prompt_toolkit.styles import Style
from rich.console import Console
from rich.live import Live

# MARK: Terminal Configuration


def configure_stdout_stderr_for_windows() -> None:
    """Configure stdout/stderr for Windows to handle encoding errors."""
    try:
        stdout_reconfigure: Any = getattr(sys.stdout, "reconfigure", None)
        stderr_reconfigure: Any = getattr(sys.stderr, "reconfigure", None)
        if callable(stdout_reconfigure):
            stdout_reconfigure(errors="replace")
        if callable(stderr_reconfigure):
            stderr_reconfigure(errors="replace")
    except Exception:
        pass


def get_terminal_width() -> int:
    """Get terminal width with multiple fallback strategies."""
    import os

    terminal_width: int | None = None

    try:
        terminal_width = shutil.get_terminal_size(fallback=(120, 24)).columns
    except Exception:
        pass

    try:
        if terminal_width is None or terminal_width <= 0:
            columns = os.environ.get("COLUMNS")
            if columns:
                terminal_width = int(columns)
    except Exception:
        pass

    try:
        if terminal_width is None or terminal_width <= 0:
            if os.name == "nt":
                import ctypes
                from ctypes import wintypes

                class _COORD(ctypes.Structure):
                    _fields_ = [("X", wintypes.SHORT), ("Y", wintypes.SHORT)]

                class _SMALL_RECT(ctypes.Structure):
                    _fields_ = [
                        ("Left", wintypes.SHORT),
                        ("Top", wintypes.SHORT),
                        ("Right", wintypes.SHORT),
                        ("Bottom", wintypes.SHORT),
                    ]

                class _CONSOLE_SCREEN_BUFFER_INFO(ctypes.Structure):
                    _fields_ = [
                        ("dwSize", _COORD),
                        ("dwCursorPosition", _COORD),
                        ("wAttributes", wintypes.WORD),
                        ("srWindow", _SMALL_RECT),
                        ("dwMaximumWindowSize", _COORD),
                    ]

                STD_OUTPUT_HANDLE = -11
                h_console = ctypes.windll.kernel32.GetStdHandle(
                    STD_OUTPUT_HANDLE,
                )
                csbi = _CONSOLE_SCREEN_BUFFER_INFO()
                ctypes.windll.kernel32.GetConsoleScreenBufferInfo(
                    h_console,
                    ctypes.byref(csbi),
                )
                terminal_width = csbi.srWindow.Right - csbi.srWindow.Left + 1
    except Exception:
        pass

    if terminal_width is None or terminal_width <= 40:
        return 120

    return terminal_width


def create_console() -> Console:
    """Create a Rich console configured for terminal output."""
    return Console(
        force_terminal=True,
        legacy_windows=False,
        no_color=False,
        file=sys.stdout,
    )


# MARK: Input Handler


class InputHandler:
    """Handles user input collection with styled prompts."""

    def __init__(self, console: Console) -> None:
        self.console = console

    def get_input(self, prompt: str, live: Live | None = None) -> str:
        """Collect input from the terminal using prompt_toolkit."""
        if live:
            live.update("")
            live.refresh()
            live.stop()
            self.console.print()

        sys.stdout.flush()
        sys.stderr.flush()

        print()

        try:
            styled_prompt = HTML(f"<ansicyan><b>></b></ansicyan> {prompt}")

            prompt_style = Style.from_dict(
                {
                    "prompt": "cyan bold",
                    "bottom-toolbar": "bg:#222222 #888888",
                }
            )

            def get_bottom_toolbar():
                return HTML("<dim>Press Ctrl+C to cancel</dim>")

            try:
                response = pt_prompt(
                    styled_prompt,
                    style=prompt_style,
                    bottom_toolbar=get_bottom_toolbar,
                )
                return response
            except KeyboardInterrupt:
                raise
            except Exception:
                return input(prompt)
        finally:
            if live:
                live.start(refresh=True)
