"""Process-wide registry of live scheduled jobs.

The registry exists so that ``Agent.close()`` and ``Swarm.close()`` (or a
top-level ``stop_all_jobs()`` helper) can drain in-flight scheduled work
without each scope needing to track its own jobs.
"""

# pyright: strict
from __future__ import annotations

import threading
import weakref

from .job import ScheduledJob

_lock = threading.Lock()
_jobs: weakref.WeakValueDictionary[str, ScheduledJob] = weakref.WeakValueDictionary()


# MARK: Registry


def register_job(job: ScheduledJob) -> None:
    """Add ``job`` to the process-wide registry."""
    with _lock:
        _jobs[job.id] = job


def list_jobs() -> list[ScheduledJob]:
    """Return every live :class:`ScheduledJob` currently registered.

    The registry holds weak references, so jobs that have been garbage-
    collected are silently dropped from the returned list.
    """
    with _lock:
        return list(_jobs.values())


def stop_all_jobs(*, drain: bool = True, timeout: float | None = None) -> None:
    """Stop every live job. Useful for shutdown hooks and tests."""
    for job in list_jobs():
        try:
            job.stop(drain=drain, timeout=timeout)
        except Exception:  # noqa: BLE001 - shutdown should continue if one job refuses to stop
            pass


# MARK: Exports

__all__ = ["list_jobs", "register_job", "stop_all_jobs"]
