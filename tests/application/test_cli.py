from __future__ import annotations

import sys
from types import SimpleNamespace

import pytest

from maivn import cli


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
    import importlib

    version_module = importlib.import_module("maivn.__version__")

    monkeypatch.setattr(sys, "argv", ["maivn", "version"])
    monkeypatch.setattr(version_module, "__version__", "9.9.9")

    cli.main()

    output = capsys.readouterr().out.strip()
    assert output == "maivn 9.9.9"


def test_cli_unknown_command_exits_nonzero(
    capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(sys, "argv", ["maivn", "nope"])

    with pytest.raises(SystemExit) as excinfo:
        cli.main()

    assert excinfo.value.code == 1
    output = capsys.readouterr().out
    assert "[ERROR] Unknown command: nope" in output


def test_cli_studio_uses_fallback(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[list[str]] = []

    def fake_run(cmd: list[str], check: bool = False) -> SimpleNamespace:
        calls.append(cmd)
        if len(calls) == 1:
            raise FileNotFoundError
        return SimpleNamespace(returncode=0)

    monkeypatch.setattr(sys, "argv", ["maivn", "studio", "--flag"])
    monkeypatch.setattr(cli.subprocess, "run", fake_run)

    with pytest.raises(SystemExit) as excinfo:
        cli.main()

    assert excinfo.value.code == 0
    assert calls[0][0] == "maivn-studio"
    assert calls[1][0] == sys.executable


def test_cli_studio_not_found(
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fake_run(cmd: list[str], check: bool = False) -> SimpleNamespace:
        raise FileNotFoundError

    monkeypatch.setattr(sys, "argv", ["maivn", "studio"])
    monkeypatch.setattr(cli.subprocess, "run", fake_run)

    with pytest.raises(SystemExit) as excinfo:
        cli.main()

    assert excinfo.value.code == 1
    output = capsys.readouterr().out
    assert "Failed to launch MAIVN Studio" in output
