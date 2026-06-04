"""Background execution utilities for orchestrators."""

# pyright: strict
from __future__ import annotations

import contextvars
import queue
import threading
from collections.abc import Callable
from concurrent.futures import Future, ThreadPoolExecutor
from concurrent.futures import TimeoutError as FutureTimeoutError
from types import TracebackType
from typing import ParamSpec, TypeVar

from ..helpers import get_optimal_worker_count

# MARK: - Constants

DEFAULT_MAX_QUEUE_SIZE = 1000
"""Default maximum queue size to prevent unbounded memory growth."""


# MARK: Types

_P = ParamSpec("_P")
_R = TypeVar("_R")


# MARK: - BackgroundExecutor


class BackgroundExecutor:
    """Manages a shared ThreadPoolExecutor for asynchronous tool execution.

    Provides bounded queue support to prevent unbounded memory growth and
    health check methods to detect saturation.
    """

    # MARK: - Initialization

    def __init__(
        self,
        *,
        max_workers: int | None = None,
        max_queue_size: int | None = None,
        run_inline: bool = False,
    ) -> None:
        """Initialize background executor with optimal worker count.

        Args:
            max_workers: Number of workers (default: auto-detect from CPU count)
            max_queue_size: Maximum pending tasks before saturation (default: 1000)
            run_inline: Execute tasks synchronously without spawning background threads
        """
        self._max_workers: int = max_workers or get_optimal_worker_count()
        self._max_queue_size: int = max_queue_size or DEFAULT_MAX_QUEUE_SIZE
        self._run_inline: bool = run_inline

        self._pending_count: int = 0
        self._lock: threading.Lock = threading.Lock()

        # `_executor` is nulled after shutdown() so submit() can lazily
        # re-create a fresh pool on the next call. `_shutdown` is a
        # belt-and-suspenders flag in case any caller holds onto an old
        # ThreadPoolExecutor reference. The revive path makes the
        # BackgroundExecutor safe to re-use after a close() cycle, which
        # is required when an Agent instance outlives a Studio demo
        # reload (demo A's teardown shuts the pool, demo B's cached
        # module-level Agent submits the next tool call).
        self._executor: ThreadPoolExecutor | None = None
        self._shutdown: bool = False
        if not self._run_inline:
            self._executor = ThreadPoolExecutor(max_workers=self._max_workers)

    # MARK: - Submission API

    def submit(
        self,
        fn: Callable[_P, _R],
        *args: _P.args,
        **kwargs: _P.kwargs,
    ) -> Future[_R]:
        """Submit a callable to the background executor.

        Captures the current context (including contextvars like current_reporter)
        and runs the callable within that context in the background thread.
        This ensures nested agent invocations inherit the parent's reporter.

        Note: To enforce a timeout on the result, use wait_with_timeout() on the
        returned Future. Timeouts cannot be enforced on running threads in Python.

        Args:
            fn: Callable to execute
            *args: Positional arguments for the callable
            **kwargs: Keyword arguments for the callable

        Returns:
            Future representing the pending execution

        Raises:
            queue.Full: If the executor queue is at capacity
        """
        with self._lock:
            if self._pending_count >= self._max_queue_size:
                raise queue.Full(f"Executor queue at capacity ({self._max_queue_size})")
            # Revive the pool if a prior shutdown() tore it down. This
            # turns shutdown() into a "return the pool to fresh state"
            # rather than "permanently disable" - the latter bit several
            # callers that reused the same BackgroundExecutor across a
            # demo-reload cycle in Studio (see
            # apps/maivn-studio/src/maivn_studio/services/session_manager/
            # lifecycle.py).
            if not self._run_inline and (self._executor is None or self._shutdown):
                self._executor = ThreadPoolExecutor(max_workers=self._max_workers)
                self._shutdown = False
            self._pending_count += 1

        ctx = contextvars.copy_context()

        if self._run_inline:
            future: Future[_R] = Future()
            try:
                result = ctx.run(fn, *args, **kwargs)
            except Exception as exc:  # noqa: BLE001 - capture task failure in the Future.
                future.set_exception(exc)
            else:
                future.set_result(result)
            finally:
                self._on_task_complete()
            return future

        try:
            if self._executor is None:
                raise RuntimeError("BackgroundExecutor is not initialized")
            future = self._executor.submit(ctx.run, fn, *args, **kwargs)
        except Exception:  # noqa: BLE001 - decrement pending count before re-raising.
            with self._lock:
                self._pending_count = max(0, self._pending_count - 1)
            raise

        def mark_complete(_future: Future[_R]) -> None:
            self._on_task_complete()

        future.add_done_callback(mark_complete)
        return future

    def _on_task_complete(self) -> None:
        """Callback invoked when a submitted task completes."""
        with self._lock:
            self._pending_count = max(0, self._pending_count - 1)

    # MARK: - Health Check

    def is_saturated(self) -> bool:
        """Check if the executor queue is at or near capacity.

        Returns:
            True if pending tasks >= max_queue_size
        """
        with self._lock:
            return self._pending_count >= self._max_queue_size

    def pending_count(self) -> int:
        """Get the current number of pending tasks.

        Returns:
            Number of tasks currently queued or executing
        """
        with self._lock:
            return self._pending_count

    # MARK: - Lifecycle

    def shutdown(self, *, wait: bool = False) -> None:
        """Shutdown the underlying executor.

        Idempotent - safe to call multiple times. After shutdown, a
        subsequent call to submit() will transparently create a fresh
        ThreadPoolExecutor so the BackgroundExecutor instance remains
        usable. This matters for Agent singletons whose lifecycle is
        managed by a caller (e.g. Studio) that closes-and-reopens the
        Agent between demo runs.

        Args:
            wait: Whether to wait for pending tasks to complete
        """
        with self._lock:
            if self._executor is None:
                self._shutdown = True
                return
            executor = self._executor
            self._executor = None
            self._shutdown = True
        # Drop the lock before calling shutdown(): with wait=True this
        # can block on running tasks, and we do not want to hold the
        # instance lock across that.
        executor.shutdown(wait=wait)

    # MARK: - Context Manager

    def __enter__(self) -> BackgroundExecutor:
        """Enter context manager."""
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> bool:
        """Exit context manager and shutdown executor."""
        self.shutdown(wait=False)
        return False


# MARK: - Utility Functions


def wait_with_timeout(future: Future[_R], timeout: float | None = None) -> _R:
    """Wait for a future result with optional timeout.

    This is the correct way to apply timeouts to tasks submitted via
    BackgroundExecutor.submit(). Python cannot forcibly stop running threads,
    so timeouts are enforced when waiting for results.

    Args:
        future: The future to wait on
        timeout: Optional timeout in seconds

    Returns:
        The result of the future

    Raises:
        TimeoutError: If the timeout is exceeded
        Exception: Any exception raised by the future's callable
    """
    try:
        return future.result(timeout=timeout)
    except FutureTimeoutError as e:
        raise TimeoutError(f"Task timed out after {timeout}s") from e


__all__ = ["BackgroundExecutor", "wait_with_timeout"]
