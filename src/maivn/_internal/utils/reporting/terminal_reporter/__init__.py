"""Terminal reporter infrastructure for agent execution.
Provides rich or simple console output with progress and live updates.
Automatically selects the appropriate implementation based on rich availability.
"""

from __future__ import annotations

# MARK: - Imports
from .base import BaseReporter
from .factory import RICH_AVAILABLE, create_reporter
from .reporters import RichReporter, SimpleReporter

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
]
