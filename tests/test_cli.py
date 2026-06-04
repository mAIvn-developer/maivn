# pyright: strict
from __future__ import annotations

import subprocess
import sys
from types import SimpleNamespace

import pytest

from maivn import cli
from maivn.__version__ import __version__


def test_cli_help_prints_usage(
    capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(sys, "argv", ["maivn"])

    cli.main()

    output = capsys.readouterr().out
    assert "MAIVN SDK Command Line Interface" in output
    assert "Usage: maivn <command>" in output


def test_cli_version_prints_version(
    capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(sys, "argv", ["maivn", "version"])

    cli.main()

    output = capsys.readouterr().out.strip()
    assert output == f"maivn {__version__}"


def test_cli_unknown_command_exits_nonzero(
    capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(sys, "argv", ["maivn", "nope"])

    with pytest.raises(SystemExit) as excinfo:
        cli.main()

    assert excinfo.value.code == 1
    captured = capsys.readouterr()
    assert captured.out == ""
    assert "[ERROR] Unknown command: nope" in captured.err


def _find_spec_present(_name: str) -> object:
    return object()


def _find_spec_missing(_name: str) -> object | None:
    return None


def _always_interactive() -> bool:
    return True


def _input_yes(_prompt: str) -> str:
    return "y"


def _input_no(_prompt: str) -> str:
    return "n"


def test_cli_studio_uses_fallback(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[list[str]] = []

    def fake_run(cmd: list[str], **_kwargs: object) -> SimpleNamespace:
        calls.append(cmd)
        if len(calls) == 1:
            raise FileNotFoundError
        return SimpleNamespace(returncode=0)

    monkeypatch.setattr(sys, "argv", ["maivn", "studio", "--flag"])
    monkeypatch.setattr(cli, "find_spec", _find_spec_present)
    monkeypatch.setattr(subprocess, "run", fake_run)

    with pytest.raises(SystemExit) as excinfo:
        cli.main()

    assert excinfo.value.code == 0
    assert calls[0][0] == "maivn-studio"
    assert calls[1][0] == sys.executable


def test_cli_studio_command_not_found_uses_module_fallback(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[list[str]] = []

    def fake_run(cmd: list[str], **_kwargs: object) -> SimpleNamespace:
        calls.append(cmd)
        if len(calls) == 1:
            raise FileNotFoundError
        return SimpleNamespace(returncode=0)

    monkeypatch.setattr(sys, "argv", ["maivn", "studio"])
    monkeypatch.setattr(cli, "find_spec", _find_spec_present)
    monkeypatch.setattr(subprocess, "run", fake_run)

    with pytest.raises(SystemExit) as excinfo:
        cli.main()

    assert excinfo.value.code == 0
    assert calls[0][0] == "maivn-studio"
    assert calls[1][0] == sys.executable


def test_cli_studio_module_not_found_prints_hint_to_stderr(
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(sys, "argv", ["maivn", "studio"])
    monkeypatch.setattr(cli, "find_spec", _find_spec_missing)

    with pytest.raises(SystemExit) as excinfo:
        cli.main()

    assert excinfo.value.code == 1
    captured = capsys.readouterr()
    assert captured.out == ""
    assert "Failed to launch MAIVN Studio" in captured.err


def test_cli_studio_missing_offers_install_and_launches(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[list[str]] = []

    def fake_run(cmd: list[str], **_kwargs: object) -> SimpleNamespace:
        calls.append(cmd)
        return SimpleNamespace(returncode=0)

    monkeypatch.setattr(sys, "argv", ["maivn", "studio"])
    monkeypatch.setattr(cli, "find_spec", _find_spec_missing)
    monkeypatch.setattr(cli, "_is_interactive", _always_interactive)
    monkeypatch.setattr("builtins.input", _input_yes)
    monkeypatch.setattr(subprocess, "run", fake_run)

    with pytest.raises(SystemExit) as excinfo:
        cli.main()

    assert excinfo.value.code == 0
    assert calls[0][:4] == [sys.executable, "-m", "pip", "install"]
    assert calls[1][0] == "maivn-studio"


def test_cli_studio_missing_install_declined_exits_nonzero(
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(sys, "argv", ["maivn", "studio"])
    monkeypatch.setattr(cli, "find_spec", _find_spec_missing)
    monkeypatch.setattr(cli, "_is_interactive", _always_interactive)
    monkeypatch.setattr("builtins.input", _input_no)

    with pytest.raises(SystemExit) as excinfo:
        cli.main()

    assert excinfo.value.code == 1
    assert "install it later" in capsys.readouterr().err.lower()


def test_cli_studio_missing_install_failure_exits_nonzero(
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fake_run(_cmd: list[str], **_kwargs: object) -> SimpleNamespace:
        return SimpleNamespace(returncode=1)

    monkeypatch.setattr(sys, "argv", ["maivn", "studio"])
    monkeypatch.setattr(cli, "find_spec", _find_spec_missing)
    monkeypatch.setattr(cli, "_is_interactive", _always_interactive)
    monkeypatch.setattr("builtins.input", _input_yes)
    monkeypatch.setattr(subprocess, "run", fake_run)

    with pytest.raises(SystemExit) as excinfo:
        cli.main()

    assert excinfo.value.code == 1
    assert "install failed" in capsys.readouterr().err.lower()
