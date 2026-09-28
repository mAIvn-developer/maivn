"""Terminal reporter implementations.

``RichReporter`` resolves lazily: importing it pulls in the optional ``rich``
package, and a plain install must be able to import this package without it.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from maivn._internal.reporting.terminal_reporter.reporters.simple_reporter import SimpleReporter

if TYPE_CHECKING:
    from maivn._internal.reporting.terminal_reporter.reporters.rich_reporter import RichReporter

__all__ = ['RichReporter', 'SimpleReporter']


def __getattr__(name: str) -> object:
    """Resolve the Rich-backed reporter only when it is actually requested."""
    if name == 'RichReporter':
        from maivn._internal.reporting.terminal_reporter.reporters.rich_reporter import (  # noqa: PLC0415 - plain installs must never import rich.
            RichReporter,
        )

        return RichReporter
    message = f'module {__name__!r} has no attribute {name!r}'
    raise AttributeError(message)
