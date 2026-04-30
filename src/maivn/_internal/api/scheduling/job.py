"""Public ``ScheduledJob`` handle for managing a scheduled invocation."""

from __future__ import annotations

import asyncio
import threading
import uuid
from collections.abc import Awaitable, Callable
from datetime import datetime, timezone
from typing import Any

from .models import RunRecord
from .schedule import Schedule

JobCallback = Callable[[RunRecord], Any]


class ScheduledJob:
    """Handle returned by ``agent.cron(...).invoke(...)`` and friends.

    The job owns its lifecycle: it can be started, paused, resumed, stopped
    (with optional drain), and inspected. Listeners may register callbacks for
    fire/success/error/skip events. Callbacks may be sync or async.
    """

    def __init__(
        self,
        *,
        name: str,
        schedule: Schedule,
        runner: Callable[[RunRecord], Awaitable[None]],
        max_runs: int | None = None,
        end_at: datetime | None = None,
        emit_events: bool = False,
    ) -> None:
        self.id = str(uuid.uuid4())
        self.name = name
        self.schedule = schedule
        self._runner = runner
        self.max_runs = max_runs
        self.end_at = end_at
        self.emit_events = emit_events

        self._state_lock = threading.Lock()
        self._is_running = False
        self._is_paused = False
        self._is_done = False
        self._stop_event: asyncio.Event | None = None
        self._pause_event: asyncio.Event | None = None
        self._task: asyncio.Task[None] | None = None
        self._loop: asyncio.AbstractEventLoop | None = None
        self._next_run_at: datetime | None = None
        self._fire_count = 0
        self._success_count = 0
        self._failure_count = 0
        self._skip_count = 0
        self._history: list[RunRecord] = []
        self._history_limit = 1000
        self._inflight: set[asyncio.Task[None]] = set()
        self._event_queue: asyncio.Queue[RunRecord] | None = None

        self._on_fire: list[JobCallback] = []
        self._on_success: list[JobCallback] = []
        self._on_error: list[JobCallback] = []
        self._on_skip: list[JobCallback] = []

    # MARK: - Lifecycle

    def start(self) -> ScheduledJob:
        """Start the job. Idempotent."""
        with self._state_lock:
            if self._is_running or self._is_done:
                return self
            self._is_running = True

        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            loop = asyncio.new_event_loop()
            threading.Thread(
                target=loop.run_forever, name=f"scheduled-job-{self.name}", daemon=True
            ).start()

        self._loop = loop
        self._stop_event = asyncio.Event()
        self._pause_event = asyncio.Event()
        self._pause_event.set()
        self._event_queue = asyncio.Queue()

        async def _bootstrap() -> None:
            await self._run_loop()

        self._task = asyncio.run_coroutine_threadsafe(_bootstrap(), loop)  # type: ignore[assignment]
        return self

    def stop(self, *, drain: bool = True, timeout: float | None = None) -> None:
        """Stop the scheduler. When ``drain`` is True, wait for in-flight runs."""
        with self._state_lock:
            if not self._is_running and not self._task:
                return
            self._is_running = False
            self._is_done = True
        if self._loop is None or self._stop_event is None:
            return
        loop = self._loop
        loop.call_soon_threadsafe(self._stop_event.set)
        if not drain:
            return
        if self._task is not None:
            try:
                self._task.result(timeout=timeout)  # type: ignore[union-attr]
            except Exception:
                pass

    def pause(self) -> None:
        with self._state_lock:
            self._is_paused = True
        if self._pause_event is not None and self._loop is not None:
            self._loop.call_soon_threadsafe(self._pause_event.clear)

    def resume(self) -> None:
        with self._state_lock:
            self._is_paused = False
        if self._pause_event is not None and self._loop is not None:
            self._loop.call_soon_threadsafe(self._pause_event.set)

    def trigger_now(self) -> None:
        """Fire the job immediately, outside the schedule."""
        if self._loop is None:
            raise RuntimeError("Job is not running; call start() first")
        record = RunRecord(
            scheduled_at=datetime.now(tz=timezone.utc),
            fire_id=str(uuid.uuid4()),
            metadata={"manual": True},
        )
        asyncio.run_coroutine_threadsafe(self._launch_run(record, manual=True), self._loop)

    # MARK: - Introspection

    @property
    def is_running(self) -> bool:
        return self._is_running

    @property
    def is_paused(self) -> bool:
        return self._is_paused

    @property
    def is_done(self) -> bool:
        return self._is_done

    @property
    def next_run_at(self) -> datetime | None:
        return self._next_run_at

    @property
    def fire_count(self) -> int:
        return self._fire_count

    @property
    def success_count(self) -> int:
        return self._success_count

    @property
    def failure_count(self) -> int:
        return self._failure_count

    @property
    def skip_count(self) -> int:
        return self._skip_count

    def history(self, *, limit: int | None = None) -> list[RunRecord]:
        if limit is None:
            return list(self._history)
        return list(self._history[-limit:])

    @property
    def last_run(self) -> RunRecord | None:
        return self._history[-1] if self._history else None

    def next_runs(self, n: int = 5) -> list[datetime]:
        return self.schedule.upcoming(n)

    # MARK: - Callbacks

    def on_fire(self, cb: JobCallback) -> ScheduledJob:
        self._on_fire.append(cb)
        return self

    def on_success(self, cb: JobCallback) -> ScheduledJob:
        self._on_success.append(cb)
        return self

    def on_error(self, cb: JobCallback) -> ScheduledJob:
        self._on_error.append(cb)
        return self

    def on_skip(self, cb: JobCallback) -> ScheduledJob:
        self._on_skip.append(cb)
        return self

    async def dispatch_fire(self, record: RunRecord) -> None:
        """Run registered ``on_fire`` callbacks for ``record``.

        Public so the builder's runner can signal the moment a run actually
        begins (after jitter wait and overlap claim).
        """
        await self._dispatch(self._on_fire, record)

    async def events(self):  # type: ignore[no-untyped-def]
        """Async-iterate completed :class:`RunRecord`s in order."""
        if self._event_queue is None:
            raise RuntimeError("Job is not running; call start() first")
        while True:
            record = await self._event_queue.get()
            yield record
            if self._is_done and self._event_queue.empty() and not self._inflight:
                break

    # MARK: - Internals

    async def _run_loop(self) -> None:
        assert self._stop_event is not None
        assert self._pause_event is not None
        try:
            while self._is_running:
                if self._max_runs_reached():
                    break

                cursor = datetime.now(tz=timezone.utc)
                next_at = self.schedule.next_after(cursor)
                if next_at is None:
                    break
                if self.end_at is not None and next_at > self.end_at:
                    break
                self._next_run_at = next_at

                wait_seconds = max(0.0, (next_at - datetime.now(tz=timezone.utc)).total_seconds())
                try:
                    await asyncio.wait_for(self._stop_event.wait(), timeout=wait_seconds)
                    break
                except asyncio.TimeoutError:
                    pass

                await self._pause_event.wait()
                if not self._is_running:
                    break

                record = RunRecord(
                    scheduled_at=next_at,
                    fire_id=str(uuid.uuid4()),
                )
                self._fire_count += 1
                await self._launch_run(record, manual=False)
        finally:
            with self._state_lock:
                self._is_done = True
                self._is_running = False
            if self._inflight:
                await asyncio.gather(*list(self._inflight), return_exceptions=True)
            if self._event_queue is not None:
                # Push a sentinel-equivalent: nothing to do; consumers see
                # is_done after queue drains.
                pass

    def _max_runs_reached(self) -> bool:
        if self.max_runs is None:
            return False
        return self._fire_count >= self.max_runs

    async def _launch_run(self, record: RunRecord, *, manual: bool) -> None:
        task = asyncio.create_task(self._execute_run(record, manual=manual))
        self._inflight.add(task)
        task.add_done_callback(self._inflight.discard)

    async def _execute_run(self, record: RunRecord, *, manual: bool) -> None:
        try:
            await self._runner(record)
        except Exception as exc:  # noqa: BLE001 - record any failure
            record.status = "failed"
            record.error = exc
            self._failure_count += 1
            await self._dispatch(self._on_error, record)
        else:
            if record.status == "succeeded":
                self._success_count += 1
                await self._dispatch(self._on_success, record)
            elif record.status.startswith("skipped"):
                self._skip_count += 1
                await self._dispatch(self._on_skip, record)
            elif record.status == "failed":
                self._failure_count += 1
                await self._dispatch(self._on_error, record)
        finally:
            self._record_history(record)
            if self._event_queue is not None:
                await self._event_queue.put(record)

    def _record_history(self, record: RunRecord) -> None:
        self._history.append(record)
        if len(self._history) > self._history_limit:
            del self._history[: len(self._history) - self._history_limit]

    async def _dispatch(self, callbacks: list[JobCallback], record: RunRecord) -> None:
        for cb in callbacks:
            try:
                result = cb(record)
                if asyncio.iscoroutine(result):
                    await result
            except Exception:  # noqa: BLE001 - never let a callback crash the loop
                pass


__all__ = ["JobCallback", "ScheduledJob"]
