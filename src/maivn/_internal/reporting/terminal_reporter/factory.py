"""Factory and tier detection for the v1-compatible terminal reporter.

Tier selection lives here and only here. Every environment probe - the optional
``rich`` extra, TTY state, ``NO_COLOR``, ``CI``, dumb terminals, and the explicit
``MAIVN_TERMINAL_TIER`` override - feeds one function so no reporter code ever
re-checks the terminal on its own. Detection only ever degrades downward: an
environment that cannot support Rich output falls back to plain text, it never
raises.
"""

from __future__ import annotations

import os
import sys
from typing import TYPE_CHECKING, Final

from maivn._internal.reporting.terminal_reporter.reporters.simple_reporter import SimpleReporter

if TYPE_CHECKING:
    from maivn._internal.reporting.terminal_reporter.base import BaseReporter

TIER_PLAIN: Final = 0
"""Plain-text output: the default for every install without the ``rich`` extra."""

TIER_RICH: Final = 1
"""Rich-backed output: requires the ``maivn[rich]`` extra and a capable terminal."""

TIER_ENV_VAR: Final = 'MAIVN_TERMINAL_TIER'
"""Explicit tier override: ``plain``/``0`` or ``rich``/``1``."""

_PLAIN_OVERRIDES: Final = frozenset({'0', 'plain', 'simple'})
_RICH_OVERRIDES: Final = frozenset({'1', 'rich'})


def _detect_rich_available() -> bool:
    try:
        import rich  # noqa: PLC0415 - availability must be probed without importing the adapter.
    except ImportError:
        return False
    _ = rich
    return True


RICH_AVAILABLE: Final[bool] = _detect_rich_available()


def _tier_override() -> str:
    """Return the normalized explicit tier override, or an empty string."""
    return os.environ.get(TIER_ENV_VAR, '').strip().lower()


def detect_terminal_tier() -> int:  # noqa: PLR0911 - one return per degrade signal reads clearest.
    """Resolve the output tier for the current process environment.

    ``MAIVN_TERMINAL_TIER`` wins in both directions, except that forcing the
    rich tier without the ``rich`` package degrades to plain instead of
    raising. Without an override, Rich output requires all of: the ``rich``
    extra installed, no ``NO_COLOR``, no ``CI``, a terminal that is not
    ``dumb``, and stdout attached to a TTY (so pipes and redirects stay plain).
    """
    override = _tier_override()
    if override in _PLAIN_OVERRIDES:
        return TIER_PLAIN
    if not RICH_AVAILABLE:
        return TIER_PLAIN
    if override in _RICH_OVERRIDES:
        return TIER_RICH
    if os.environ.get('NO_COLOR'):
        return TIER_PLAIN
    if os.environ.get('CI'):
        return TIER_PLAIN
    if os.environ.get('TERM', '').strip().lower() == 'dumb':
        return TIER_PLAIN
    is_a_tty = getattr(sys.stdout, 'isatty', None)
    if not callable(is_a_tty):
        return TIER_PLAIN
    try:
        if not is_a_tty():
            return TIER_PLAIN
    except ValueError:
        # A closed stdout (e.g. a detached service) must not break reporting.
        return TIER_PLAIN
    return TIER_RICH


def create_reporter(*, enabled: bool = True) -> BaseReporter:
    """Create the reporter for the detected tier.

    Disabled reporting never initializes Rich. An explicit ``rich`` override
    also forces terminal rendering inside the Rich console, so captured runs
    (pipes, files) still produce styled output for inspection.
    """
    if not enabled:
        return SimpleReporter(enabled=False)
    if detect_terminal_tier() == TIER_RICH:
        from maivn._internal.reporting.terminal_reporter.reporters.rich_reporter import (  # noqa: PLC0415 - plain installs must never import rich.
            RichReporter,
        )

        forced = _tier_override() in _RICH_OVERRIDES
        return RichReporter(enabled=True, force_terminal=True if forced else None)
    return SimpleReporter(enabled=True)


__all__ = [
    'RICH_AVAILABLE',
    'TIER_ENV_VAR',
    'TIER_PLAIN',
    'TIER_RICH',
    'create_reporter',
    'detect_terminal_tier',
]
