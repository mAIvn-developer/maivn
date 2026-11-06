"""Studio guidance names the environment that can actually run it."""

from __future__ import annotations

import builtins
import sys
import venv
from typing import TYPE_CHECKING

import pytest

from maivn import cli

if TYPE_CHECKING:
    from pathlib import Path


def _run(monkeypatch: pytest.MonkeyPatch) -> int | str | None:
    monkeypatch.setattr(sys, 'argv', ['maivn', 'studio'])
    with pytest.raises(SystemExit) as stopped:
        cli.main()
    return stopped.value.code


def test_existing_project_studio_is_found_from_a_nested_folder_without_importing_it(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """A stray global maivn points to the project venv without launching or installing."""
    project = tmp_path / 'project with spaces'
    env = project / '.venv'
    venv.EnvBuilder(with_pip=False).create(env)
    if sys.platform == 'win32':
        site = env / 'Lib' / 'site-packages'
        activate = f"& '{env / 'Scripts' / 'Activate.ps1'}'"
    else:
        site = (
            env
            / 'lib'
            / f'python{sys.version_info.major}.{sys.version_info.minor}'
            / 'site-packages'
        )
        activate = f"source '{env / 'bin' / 'activate'}'"
    package = site / 'maivn_studio'
    package.mkdir(parents=True)
    (package / '__init__.py').write_text(
        'raise RuntimeError("do not import Studio")', encoding='utf-8'
    )
    nested = project / 'src'
    nested.mkdir()
    monkeypatch.chdir(nested)
    monkeypatch.setattr(sys.stdin, 'isatty', lambda: True)
    monkeypatch.setattr(sys.stdout, 'isatty', lambda: True)

    def no_install(_prompt: str) -> str:
        pytest.fail('An existing project environment must not offer a stray install')

    monkeypatch.setattr(builtins, 'input', no_install)
    assert _run(monkeypatch) == 1
    assert capsys.readouterr().err == (
        '[ERROR] Cannot launch MAIVN Studio: maivn_studio is not installed for:\n'
        f'       {sys.executable}\n'
        '[INFO] Studio is already installed in the project environment:\n'
        f'       {env}\n'
        f'[INFO] Run from {project}:\n'
        '       uv run maivn studio\n'
        '[INFO] Or activate it, then run maivn studio:\n'
        f'       {activate}\n'
    )


def test_uv_tool_environment_gets_a_tool_install_command_without_a_pip_offer(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Tool dependencies stay managed by uv even when its interpreter has no pip."""
    (tmp_path / 'uv-receipt.toml').write_text('[tool]\n', encoding='utf-8')
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(sys, 'prefix', str(tmp_path))
    monkeypatch.setattr(sys.stdin, 'isatty', lambda: True)
    monkeypatch.setattr(sys.stdout, 'isatty', lambda: True)

    def no_install(_prompt: str) -> str:
        pytest.fail('uv tool environments must not receive a pip install offer')

    monkeypatch.setattr(builtins, 'input', no_install)
    assert _run(monkeypatch) == 1
    assert capsys.readouterr().err == (
        '[ERROR] Cannot launch MAIVN Studio: maivn_studio is not installed for:\n'
        f'       {sys.executable}\n'
        '[INFO] This maivn command is installed in a uv tool environment:\n'
        f'       {tmp_path}\n'
        '[INFO] Add Studio to that tool with:\n'
        '       uv tool install maivn --with maivn-studio\n'
    )
