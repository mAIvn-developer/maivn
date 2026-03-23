"""Reporter factory for terminal output.
Detects rich availability and returns the appropriate reporter implementation.
"""

from __future__ import annotations

# MARK: - Imports
from .base import BaseReporter

# MARK: - Rich Detection

try:
    from rich.console import Console  # noqa: F401

    RICH_AVAILABLE = True
except ImportError:
    RICH_AVAILABLE = False

# MARK: - Factory


def create_reporter(enabled: bool = True) -> BaseReporter:
    """Create the appropriate reporter instance based on rich availability.

    This factory function detects whether the rich library is available
    and creates the appropriate reporter implementation.

    Args:
        enabled: Whether reporting is enabled

    Returns:
        RichReporter if rich is available, SimpleReporter otherwise
    """
    if not enabled:
        from .reporters import SimpleReporter

        return SimpleReporter(enabled=False)

    if RICH_AVAILABLE:
        from .reporters import RichReporter

        return RichReporter(enabled=enabled)

    from .reporters import SimpleReporter

    return SimpleReporter(enabled=enabled)


# MARK: - Public API

__all__ = ["create_reporter", "RICH_AVAILABLE"]
