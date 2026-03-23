"""Terminal reporting infrastructure for verbose agent execution."""

from __future__ import annotations

# MARK: - Imports
from .context import current_reporter, get_current_reporter, set_current_reporter
from .terminal_reporter import (
    RICH_AVAILABLE,
    BaseReporter,
    RichReporter,
    SimpleReporter,
    create_reporter,
)

# MARK: - Public API

__all__ = [
    # Factory
    "create_reporter",
    "RICH_AVAILABLE",
    # Base class
    "BaseReporter",
    # Implementations
    "RichReporter",
    "SimpleReporter",
    # Context
    "current_reporter",
    "get_current_reporter",
    "set_current_reporter",
]
