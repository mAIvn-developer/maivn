"""Regression tests: JitterSpec on remote schedules, uvx --with constraints."""

from __future__ import annotations

from datetime import timedelta

from maivn import MCPAutoSetup
from maivn._internal.compat.base import (
    _remote_jitter_seconds,  # pyright: ignore[reportPrivateUsage] - private helper under test.
)
from maivn._internal.compat.scheduling import JitterSpec

_SYMMETRIC_SECONDS = 30
_DEFAULT_JITTER_SECONDS = 60
_PLAIN_TIMEDELTA_SECONDS = 15


def test_remote_jitter_accepts_a_jitter_spec() -> None:
    """A JitterSpec maps to its amplitude; zero jitter is zero, not an error.

    Regression: the annotation always named JitterSpec but the body never
    handled one, so every scheduling demo that used JitterSpec.symmetric()
    failed with 'remote schedules require jitter as non-negative whole seconds'.
    """
    assert _remote_jitter_seconds(JitterSpec.symmetric(timedelta(seconds=0))) == 0
    symmetric = JitterSpec.symmetric(timedelta(seconds=_SYMMETRIC_SECONDS))
    assert _remote_jitter_seconds(symmetric) == _SYMMETRIC_SECONDS
    bounded = JitterSpec(min=timedelta(0), max=timedelta(seconds=_SYMMETRIC_SECONDS))
    assert _remote_jitter_seconds(bounded) == _SYMMETRIC_SECONDS


def test_remote_jitter_still_accepts_plain_values() -> None:
    """Whole seconds and timedeltas keep their existing meaning."""
    assert _remote_jitter_seconds(None) == _DEFAULT_JITTER_SECONDS
    assert _remote_jitter_seconds(0) == 0
    assert (
        _remote_jitter_seconds(timedelta(seconds=_PLAIN_TIMEDELTA_SECONDS))
        == _PLAIN_TIMEDELTA_SECONDS
    )


def test_auto_setup_with_packages_precede_the_server_package() -> None:
    """--with constraints must reach uvx before the package, not as server args."""
    setup = MCPAutoSetup(
        provider='uvx',
        package='mcp-server-fetch',
        with_packages=['mcp<2'],
        args=['--flag'],
    )
    command, arguments = setup.resolve_command()
    assert command == 'uvx'
    assert arguments == ['--with', 'mcp<2', 'mcp-server-fetch', '--flag']


def test_auto_setup_without_constraints_is_unchanged() -> None:
    """The default command line is exactly what it was before the field existed."""
    setup = MCPAutoSetup(provider='uvx', package='mcp-server-fetch')
    command, arguments = setup.resolve_command()
    assert command == 'uvx'
    assert arguments == ['mcp-server-fetch']
