"""MAIVN CLI - Unified command-line interface."""

# pyright: strict
from __future__ import annotations

import subprocess
import sys
from importlib.util import find_spec
from typing import Final, TextIO

from maivn.__version__ import __version__

# MARK: Configuration

COMMAND_STUDIO: Final = "studio"
COMMAND_VERSION: Final = "version"
HELP_FLAGS: Final[frozenset[str]] = frozenset({"-h", "--help"})
COMMAND_HELP: Final[tuple[tuple[str, str], ...]] = (
    (COMMAND_STUDIO, "Launch MAIVN Studio - UI/UX developer tool"),
    (COMMAND_VERSION, "Show MAIVN SDK version"),
)

STUDIO_COMMAND: Final = "maivn-studio"
STUDIO_MODULE: Final = "maivn_studio"
STUDIO_MODULE_MAIN: Final = "maivn_studio.main"

ERROR_PREFIX: Final = "[ERROR]"
INFO_PREFIX: Final = "[INFO]"
EXIT_ERROR: Final = 1
EXIT_SIGINT: Final = 130


# MARK: CLI Dispatch


def main() -> None:
    """Main entry point for the maivn CLI.

    Usage:
        maivn studio [args...]  - Launch MAIVN Studio
        maivn version           - Show SDK version
        maivn --help            - Show help
    """
    args = sys.argv[1:]

    if not args or args[0] in HELP_FLAGS:
        _print_help()
        return

    command = args[0]
    command_args = args[1:]

    if command == COMMAND_STUDIO:
        _run_studio(command_args)
    elif command == COMMAND_VERSION:
        _show_version()
    else:
        print(f"{ERROR_PREFIX} Unknown command: {command}", file=sys.stderr)
        print(file=sys.stderr)
        _print_help(file=sys.stderr)
        sys.exit(EXIT_ERROR)


# MARK: Helpers


def _print_help(*, file: TextIO | None = None) -> None:
    """Print CLI help message."""
    stream = file or sys.stdout
    print("MAIVN SDK Command Line Interface", file=stream)
    print(file=stream)
    print("Usage: maivn <command> [options]", file=stream)
    print(file=stream)
    print("Commands:", file=stream)
    for command, description in COMMAND_HELP:
        print(f"  {command:<9} {description}", file=stream)
    print(file=stream)
    print('Run "maivn studio --help" for studio-specific options.', file=stream)


def _run_studio(extra_args: list[str]) -> None:
    """Launch MAIVN Studio, offering to install the companion if it is missing."""
    if find_spec(STUDIO_MODULE) is None and not _ensure_studio_installed():
        sys.exit(EXIT_ERROR)
    _launch_studio(extra_args)


def _is_interactive() -> bool:
    """Return True when both stdin and stdout are attached to a terminal."""
    return sys.stdin.isatty() and sys.stdout.isatty()


def _ensure_studio_installed() -> bool:
    """Offer to install the missing Studio companion.

    Returns True when Studio has just been installed and is ready to launch;
    False when the caller should abort. In a non-interactive shell, prints the
    install hint and returns False rather than blocking on a prompt.
    """
    if not _is_interactive():
        _print_studio_missing_hint()
        return False

    try:
        answer = input(f"{INFO_PREFIX} MAIVN Studio is not installed. Install it now? [y/N]: ")
    except (EOFError, KeyboardInterrupt):
        print(file=sys.stderr)
        answer = ""

    if answer.strip().lower() not in {"y", "yes"}:
        print(
            f"{INFO_PREFIX} To install it later: uv pip install {STUDIO_COMMAND}",
            file=sys.stderr,
        )
        return False

    print(f"{INFO_PREFIX} Installing {STUDIO_COMMAND}...", file=sys.stderr)
    install: subprocess.CompletedProcess[bytes] = subprocess.run(
        [sys.executable, "-m", "pip", "install", STUDIO_COMMAND], check=False
    )
    if install.returncode != 0:
        print(
            f"{ERROR_PREFIX} Automatic install failed. Install it manually:",
            file=sys.stderr,
        )
        print(f"       uv pip install {STUDIO_COMMAND}", file=sys.stderr)
        return False
    return True


def _launch_studio(extra_args: list[str]) -> None:
    """Delegate to the Studio entry point: the command, then the module fallback."""
    commands = [
        [STUDIO_COMMAND, *extra_args],
        [sys.executable, "-m", STUDIO_MODULE_MAIN, *extra_args],
    ]

    for cmd in commands:
        try:
            result: subprocess.CompletedProcess[bytes] = subprocess.run(cmd, check=False)
            sys.exit(result.returncode)
        except FileNotFoundError:
            continue
        except KeyboardInterrupt:
            sys.exit(EXIT_SIGINT)

    _print_studio_missing_hint()
    sys.exit(EXIT_ERROR)


def _print_studio_missing_hint() -> None:
    """Print the companion-install hint when Studio is unavailable."""
    print(
        f"{ERROR_PREFIX} Failed to launch MAIVN Studio: {STUDIO_COMMAND} not found",
        file=sys.stderr,
    )
    print(f"{INFO_PREFIX} Make sure the Studio companion is installed:", file=sys.stderr)
    print(f"       uv pip install {STUDIO_COMMAND}", file=sys.stderr)


def _show_version() -> None:
    """Show the MAIVN SDK version."""
    print(f"maivn {__version__}")


# MARK: Entry Point

if __name__ == "__main__":
    main()
