"""MAIVN CLI - Unified command-line interface."""

from __future__ import annotations

import subprocess
import sys


def main() -> None:
    """Main entry point for the maivn CLI.

    Usage:
        maivn studio [args...]  - Launch MAIVN Studio
        maivn version           - Show SDK version
        maivn --help            - Show help
    """
    args = sys.argv[1:]

    if not args or args[0] in ("-h", "--help"):
        _print_help()
        return

    command = args[0]
    command_args = args[1:]

    if command == "studio":
        _run_studio(command_args)
    elif command == "version":
        _show_version()
    else:
        print(f"[ERROR] Unknown command: {command}")
        print()
        _print_help()
        sys.exit(1)


def _print_help() -> None:
    """Print CLI help message."""
    print("MAIVN SDK Command Line Interface")
    print()
    print("Usage: maivn <command> [options]")
    print()
    print("Commands:")
    print("  studio    Launch MAIVN Studio - UI/UX developer tool")
    print("  version   Show MAIVN SDK version")
    print()
    print('Run "maivn studio --help" for studio-specific options.')


def _run_studio(extra_args: list[str]) -> None:
    """Launch MAIVN Studio by delegating to maivn-studio command."""
    commands = [
        ["maivn-studio", *extra_args],
        [sys.executable, "-m", "maivn_studio.main", *extra_args],
    ]

    for cmd in commands:
        try:
            result = subprocess.run(cmd, check=False)
            sys.exit(result.returncode)
        except FileNotFoundError:
            continue
        except KeyboardInterrupt:
            sys.exit(130)

    print("[ERROR] Failed to launch MAIVN Studio: maivn-studio not found")
    print("[INFO] Make sure the Studio companion is installed:")
    print('       uv pip install "maivn[studio]"')
    sys.exit(1)


def _show_version() -> None:
    """Show the MAIVN SDK version."""
    from maivn.__version__ import __version__

    print(f"maivn {__version__}")


if __name__ == "__main__":
    main()
