"""Orchestrator execution constants.
Centralizes shared limits and defaults used across orchestrator services."""

from __future__ import annotations

# MARK: - Parallel Execution

MAX_PARALLEL_WORKERS: int = 16
"""Maximum cap for parallel workers to prevent resource exhaustion.

This constant serves as an upper limit for parallel execution to prevent
excessive resource usage on high-core systems. For dynamic worker count
based on available CPUs, use get_optimal_worker_count() from helpers.resource_utils.
"""

# MARK: - Public API

__all__ = ["MAX_PARALLEL_WORKERS"]
