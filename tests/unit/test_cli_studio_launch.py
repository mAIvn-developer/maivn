"""CLI studio launch: the same-environment module is the only launch path.

Regression coverage for the owner-reported shadowing bug (2026-07-21): the
old launch order probed the ``maivn-studio`` console script via PATH before
the same-interpreter module, so a globally-installed Studio from another
checkout silently shadowed the project's own copy. The contract pinned here:
``maivn studio`` launches ``sys.executable -m maivn_studio.main`` (the
environment this CLI itself runs from) and nothing else. There is no PATH
fallback to a console script - Studio stopped generating one (2026-08-07)
because the Windows .exe wrapper locks itself while running, and a stale
wrapper blocked every sync and start until it was hunted down.
"""

from __future__ import annotations

import subprocess
import sys

import pytest

from maivn import cli


def _completed(returncode: int = 0) -> subprocess.CompletedProcess[bytes]:
    return subprocess.CompletedProcess(args=[], returncode=returncode)


def test_launch_studio_runs_the_same_interpreter_module(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The launch is `sys.executable -m maivn_studio.main`, args passed through."""
    attempts: list[list[str]] = []

    def record_run(
        cmd: list[str],
        *,
        check: bool = False,  # noqa: ARG001 - matches subprocess.run's call shape
    ) -> subprocess.CompletedProcess[bytes]:
        attempts.append(cmd)
        return _completed(0)

    monkeypatch.setattr(cli.subprocess, 'run', record_run)

    with pytest.raises(SystemExit) as exit_info:
        cli._launch_studio(['--help'])  # noqa: SLF001 # pyright: ignore[reportPrivateUsage] - unit under test

    assert exit_info.value.code == 0
    assert attempts, 'no launch attempt recorded'
    assert attempts[0][:3] == [sys.executable, '-m', cli.STUDIO_MODULE_MAIN]
    assert attempts[0][3:] == ['--help']
    # The module launch is the ONLY attempt: PATH is never probed, so no
    # globally-installed Studio can shadow the environment-local one.
    assert len(attempts) == 1


def test_launch_studio_never_probes_a_path_console_script(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A failing module launch exits with its code rather than probing PATH."""
    attempts: list[list[str]] = []

    def failing_run(
        cmd: list[str],
        *,
        check: bool = False,  # noqa: ARG001 - matches subprocess.run's call shape
    ) -> subprocess.CompletedProcess[bytes]:
        attempts.append(cmd)
        return _completed(1)

    monkeypatch.setattr(cli.subprocess, 'run', failing_run)

    with pytest.raises(SystemExit) as exit_info:
        cli._launch_studio([])  # noqa: SLF001 # pyright: ignore[reportPrivateUsage] - unit under test

    assert exit_info.value.code == 1
    assert len(attempts) == 1
    assert attempts[0][0] == sys.executable
