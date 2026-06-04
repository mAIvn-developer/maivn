"""Terminal configuration and input handling for RichReporter."""

# pyright: strict
from __future__ import annotations

import shutil
import struct
import sys
from typing import Protocol, cast

from prompt_toolkit import prompt as pt_prompt
from prompt_toolkit.formatted_text import HTML
from prompt_toolkit.styles import Style
from rich.console import Console
from rich.live import Live


class _Reconfigure(Protocol):
    def __call__(self, *, errors: str) -> object: ...


class _Kernel32(Protocol):
    def GetStdHandle(self, n_std_handle: int) -> int: ...

    def GetConsoleScreenBufferInfo(
        self,
        h_console_output: int,
        lp_console_screen_buffer_info: object,
    ) -> int: ...


class _WinDLL(Protocol):
    kernel32: _Kernel32


# MARK: Terminal Configuration


def configure_stdout_stderr_for_windows() -> None:
    """Configure stdout/stderr for Windows to handle encoding errors."""
    try:
        stdout_reconfigure = getattr(sys.stdout, "reconfigure", None)
        stderr_reconfigure = getattr(sys.stderr, "reconfigure", None)
        if callable(stdout_reconfigure):
            _ = cast(_Reconfigure, stdout_reconfigure)(errors="replace")
        if callable(stderr_reconfigure):
            _ = cast(_Reconfigure, stderr_reconfigure)(errors="replace")
    except Exception:  # noqa: BLE001 - Stream reconfigure is a best-effort Windows probe.
        pass


def get_terminal_width() -> int:
    """Get terminal width with multiple fallback strategies."""
    import os

    terminal_width: int | None = None

    try:
        terminal_width = shutil.get_terminal_size(fallback=(120, 24)).columns
    except Exception:  # noqa: BLE001 - Terminal-size probing falls back below.
        pass

    try:
        if terminal_width is None or terminal_width <= 0:
            columns = os.environ.get("COLUMNS")
            if columns:
                terminal_width = int(columns)
    except Exception:  # noqa: BLE001 - Invalid COLUMNS values fall back below.
        pass

    try:
        if terminal_width is None or terminal_width <= 0:
            if os.name == "nt":
                import ctypes

                STD_OUTPUT_HANDLE = -11
                windll = cast(_WinDLL, cast(object, ctypes.windll))
                h_console = windll.kernel32.GetStdHandle(
                    STD_OUTPUT_HANDLE,
                )
                csbi = ctypes.create_string_buffer(22)
                _ = windll.kernel32.GetConsoleScreenBufferInfo(
                    h_console,
                    ctypes.byref(csbi),
                )
                unpacked = cast(
                    tuple[int, int, int, int, int, int, int, int, int, int, int],
                    cast(object, struct.unpack("<hhhhHhhhhhh", csbi.raw)),
                )
                left = unpacked[5]
                right = unpacked[7]
                terminal_width = int(right) - int(left) + 1
    except Exception:  # noqa: BLE001 - Windows console API probing falls back below.
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
        self.console: Console = console

    def get_input(self, prompt: str, live: Live | None = None) -> str:
        """Collect input from the terminal using prompt_toolkit."""
        if live:
            _ = live.update("")
            _ = live.refresh()
            live.stop()
            self.console.print()

        _ = sys.stdout.flush()
        _ = sys.stderr.flush()

        print()

        try:
            styled_prompt = HTML(f"<ansicyan><b>></b></ansicyan> {prompt}")

            prompt_style = Style.from_dict(
                {
                    "prompt": "cyan bold",
                    "bottom-toolbar": "bg:#222222 #888888",
                }
            )

            def get_bottom_toolbar() -> HTML:
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
            except Exception:  # noqa: BLE001 - prompt_toolkit may fail in plain terminals.
                return input(prompt)
        finally:
            if live:
                live.start(refresh=True)
