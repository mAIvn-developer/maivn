"""Public ``ScheduledJob`` handle for managing a scheduled invocation."""

# pyright: strict
from __future__ import annotations

import asyncio
import threading
import uuid
from collections.abc import AsyncIterator, Awaitable, Callable, Coroutine
from concurrent.futures import Future
from datetime import datetime, timezone, tzinfo
from typing import cast

from .models import RunRecord
from .schedule import Schedule

# MARK: Types

JobCallback = Callable[[RunRecord], object | Awaitable[object]]
LoopCallback = Callable[[], object]
TaskDoneCallback = Callable[[asyncio.Task[None]], object]
CallSoonThreadsafe = Callable[[LoopCallback], object]
CreateTask = Callable[[Coroutine[object, object, None]], asyncio.Task[None]]
DateTimeNow = Callable[[tzinfo], datetime]
WaitForBool = Callable[[Awaitable[bool], float], Awaitable[bool]]
ThreadingEventFactory = Callable[[], threading.Event]
EventWait = Callable[[float | None], bool]


# MARK: Utilities


def _utc_now() -> datetime:
    datetime_now = cast(DateTimeNow, getattr(datetime, "now"))  # noqa: B009
    return datetime_now(timezone.utc)


def _call_loop_soon(loop: asyncio.AbstractEventLoop, callback: LoopCallback) -> None:
    call_soon_threadsafe = cast(CallSoonThreadsafe, getattr(loop, "call_soon_threadsafe"))  # noqa: B009
    _ = call_soon_threadsafe(callback)


async def _wait_for_bool(awaitable: Awaitable[bool], *, timeout: float) -> bool:
    wait_for = cast(WaitForBool, getattr(asyncio, "wait_for"))  # noqa: B009
    return await wait_for(awaitable, timeout)


def _create_task(coro: Coroutine[object, object, None]) -> asyncio.Task[None]:
    create_task = cast(CreateTask, getattr(asyncio, "create_task"))  # noqa: B009
    return create_task(coro)


def _add_task_done_callback(task: asyncio.Task[None], callback: TaskDoneCallback) -> None:
    add_done_callback = cast(
        Callable[[TaskDoneCallback], None],
        getattr(task, "add_done_callback"),  # noqa: B009
    )
    add_done_callback(callback)


def _new_thread_event() -> threading.Event:
    event_factory = cast(ThreadingEventFactory, getattr(threading, "Event"))  # noqa: B009
    return event_factory()


def _set_thread_event(event: threading.Event) -> None:
    set_event = cast(Callable[[], None], getattr(event, "set"))  # noqa: B009
    set_event()


def _wait_thread_event(event: threading.Event, timeout: float) -> bool:
    wait_event = cast(EventWait, getattr(event, "wait"))  # noqa: B009
    return wait_event(timeout)


# MARK: Scheduled Job


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
        self.id: str = str(uuid.uuid4())
        self.name: str = name
        self.schedule: Schedule = schedule
        self._runner: Callable[[RunRecord], Awaitable[None]] = runner
        self.max_runs: int | None = max_runs
        self.end_at: datetime | None = end_at
        self.emit_events: bool = emit_events

        self._state_lock: threading.Lock = threading.Lock()
        self._is_running: bool = False
        self._is_paused: bool = False
        self._is_done: bool = False
        self._stop_event: asyncio.Event | None = None
        self._pause_event: asyncio.Event | None = None
        self._task: Future[None] | None = None
        self._loop: asyncio.AbstractEventLoop | None = None
        self._next_run_at: datetime | None = None
        self._fire_count: int = 0
        self._success_count: int = 0
        self._failure_count: int = 0
        self._skip_count: int = 0
        self._history: list[RunRecord] = []
        self._history_limit: int = 1000
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

        self._task = asyncio.run_coroutine_threadsafe(_bootstrap(), loop)
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
        _call_loop_soon(loop, self._stop_event.set)
        if not drain:
            return
        if self._task is not None:
            try:
                self._task.result(timeout=timeout)
            except Exception:  # noqa: BLE001 - drain is best-effort; ignore timeouts and task errors
                pass

    def pause(self) -> None:
        with self._state_lock:
            self._is_paused = True
        self._apply_pause_event_state(paused=True)

    def resume(self) -> None:
        with self._state_lock:
            self._is_paused = False
        self._apply_pause_event_state(paused=False)

    def _apply_pause_event_state(self, *, paused: bool) -> None:
        pause_event = self._pause_event
        loop = self._loop
        if pause_event is None or loop is None:
            return

        def _apply() -> None:
            if paused:
                pause_event.clear()
            else:
                pause_event.set()

        try:
            running_loop = asyncio.get_running_loop()
        except RuntimeError:
            running_loop = None
        if running_loop is loop:
            _apply()
            return

        applied = _new_thread_event()

        def _apply_and_signal() -> None:
            try:
                _apply()
            finally:
                _set_thread_event(applied)

        _call_loop_soon(loop, _apply_and_signal)
        _ = _wait_thread_event(applied, 1.0)

    def trigger_now(self) -> None:
        """Fire the job immediately, outside the schedule."""
        if self._loop is None:
            raise RuntimeError("Job is not running; call start() first")
        record = RunRecord(
            scheduled_at=_utc_now(),
            fire_id=str(uuid.uuid4()),
            metadata={"manual": True},
        )
        _ = asyncio.run_coroutine_threadsafe(self._launch_run(record, _manual=True), self._loop)

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

    async def events(self) -> AsyncIterator[RunRecord]:
        """Async-iterate completed :class:`RunRecord`s in order."""
        if self._event_queue is None:
            raise RuntimeError("Job is not running; call start() first")
        while True:
            record = await self._event_queue.get()
            yield record
            if self._is_done and self._event_queue.empty() and not self._inflight:
                break

    # MARK: - Internals

    def _next_run_after(
        self,
        cursor: datetime,
        *,
        last_fired_at: datetime | None,
    ) -> datetime | None:
        if last_fired_at is not None and cursor < last_fired_at:
            cursor = last_fired_at
        next_at = self.schedule.next_after(cursor)
        if next_at is None:
            return None
        if last_fired_at is not None and next_at <= last_fired_at:
            next_at = self.schedule.next_after(last_fired_at)
            if next_at is None:
                return None
        if self.end_at is not None and next_at > self.end_at:
            return None
        return next_at

    async def _run_loop(self) -> None:
        assert self._stop_event is not None
        assert self._pause_event is not None
        last_fired_at: datetime | None = None
        try:
            while self._is_running:
                if self._max_runs_reached():
                    break

                cursor = _utc_now()
                next_at = self._next_run_after(cursor, last_fired_at=last_fired_at)
                if next_at is None:
                    break
                self._next_run_at = next_at

                wait_seconds = max(0.0, (next_at - _utc_now()).total_seconds())
                try:
                    _ = await _wait_for_bool(self._stop_event.wait(), timeout=wait_seconds)
                    break
                except asyncio.TimeoutError:
                    pass

                was_paused = self._is_paused
                _ = await self._pause_event.wait()
                if not self._is_running:
                    break
                if self._is_paused:
                    continue
                if was_paused:
                    # Resume from the current clock, not the stale tick that
                    # was computed before the pause blocked the loop.
                    cursor = _utc_now()
                    next_at = self._next_run_after(cursor, last_fired_at=last_fired_at)
                    if next_at is None:
                        break
                    self._next_run_at = next_at

                record = RunRecord(
                    scheduled_at=next_at,
                    fire_id=str(uuid.uuid4()),
                )
                last_fired_at = next_at
                self._fire_count += 1
                await self._launch_run(record, _manual=False)
        finally:
            with self._state_lock:
                self._is_done = True
                self._is_running = False
            if self._inflight:
                for task in list(self._inflight):
                    try:
                        await task
                    except BaseException:  # noqa: BLE001 - mirrors gather(return_exceptions=True)
                        pass
            if self._event_queue is not None:
                # Push a sentinel-equivalent: nothing to do; consumers see
                # is_done after queue drains.
                pass

    def _max_runs_reached(self) -> bool:
        if self.max_runs is None:
            return False
        return self._fire_count >= self.max_runs

    async def _launch_run(self, record: RunRecord, *, _manual: bool) -> None:
        task = _create_task(self._execute_run(record, _manual=_manual))
        self._inflight.add(task)

        def _discard_task(done_task: asyncio.Task[None]) -> None:
            self._inflight.discard(done_task)

        _add_task_done_callback(task, _discard_task)

    async def _execute_run(self, record: RunRecord, *, _manual: bool) -> None:
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
                if isinstance(result, Awaitable):
                    _ = await cast(Awaitable[object], result)
            except Exception:  # noqa: BLE001 - never let a callback crash the loop
                pass


# MARK: Exports

__all__ = ["JobCallback", "ScheduledJob"]
