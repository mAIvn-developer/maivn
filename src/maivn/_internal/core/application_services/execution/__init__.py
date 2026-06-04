"""Background execution services.
Provides a shared thread pool executor for orchestrator operations.
"""

# pyright: strict
from __future__ import annotations

from .background_executor import BackgroundExecutor, wait_with_timeout

# MARK: Public API

__all__ = ["BackgroundExecutor", "wait_with_timeout"]
