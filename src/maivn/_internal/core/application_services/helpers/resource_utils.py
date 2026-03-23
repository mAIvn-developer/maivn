"""Resource and system utilities for orchestrator services.
Provides helpers for choosing safe defaults based on available system resources.
Used primarily for parallel execution sizing."""

from __future__ import annotations

import os

from ..constants import MAX_PARALLEL_WORKERS

# MARK: - Worker Count Utilities


def get_optimal_worker_count() -> int:
    """Get optimal number of parallel workers based on system CPU count.

    Determines the best number of parallel workers by checking available CPU cores
    and capping at MAX_PARALLEL_WORKERS to prevent excessive resource usage on
    high-core systems.

    Returns:
        Optimal number of workers (1 to MAX_PARALLEL_WORKERS)

    Examples:
        >>> # On a 4-core system
        >>> get_optimal_worker_count()
        4

        >>> # On a 16-core system (capped at MAX_PARALLEL_WORKERS=8)
        >>> get_optimal_worker_count()
        8
    """
    cpu_count = _get_cpu_count()
    if cpu_count is not None and cpu_count > 0:
        return min(cpu_count, MAX_PARALLEL_WORKERS)
    return MAX_PARALLEL_WORKERS


# MARK: - Private Helpers


def _get_cpu_count() -> int | None:
    """Safely retrieve the system CPU count.

    Returns:
        Number of CPUs or None if unavailable
    """
    try:
        return os.cpu_count()
    except Exception:
        return None


# MARK: - Public API

__all__ = ["get_optimal_worker_count"]
