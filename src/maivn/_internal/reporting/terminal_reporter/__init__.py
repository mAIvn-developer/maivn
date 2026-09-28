"""Internal terminal reporter package.

``RichReporter`` resolves lazily so a plain install (no ``rich`` extra) can
import this package; the eager import here used to defeat the factory's
availability probe before it ever ran.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from maivn._internal.reporting.terminal_reporter.base import BaseReporter
from maivn._internal.reporting.terminal_reporter.factory import (
    RICH_AVAILABLE,
    create_reporter,
    detect_terminal_tier,
)
from maivn._internal.reporting.terminal_reporter.reporters.simple_reporter import SimpleReporter

if TYPE_CHECKING:
    from maivn._internal.reporting.terminal_reporter.reporters.rich_reporter import RichReporter

__all__ = [
    'RICH_AVAILABLE',
    'BaseReporter',
    'RichReporter',
    'SimpleReporter',
    'create_reporter',
    'detect_terminal_tier',
]


def __getattr__(name: str) -> object:
    """Resolve the Rich-backed reporter only when it is actually requested."""
    if name == 'RichReporter':
        from maivn._internal.reporting.terminal_reporter.reporters.rich_reporter import (  # noqa: PLC0415 - plain installs must never import rich.
            RichReporter,
        )

        return RichReporter
    message = f'module {__name__!r} has no attribute {name!r}'
    raise AttributeError(message)
