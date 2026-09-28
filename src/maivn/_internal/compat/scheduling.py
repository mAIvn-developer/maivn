"""V1-compatible scheduling helpers."""

from __future__ import annotations

import asyncio
import inspect
import random
import threading
import uuid
from collections import deque
from collections.abc import AsyncIterable, Awaitable, Callable, Iterable
from contextlib import suppress
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone, tzinfo
from typing import Literal, Protocol, TypeAlias, cast
from zoneinfo import ZoneInfo

from croniter import croniter

_JITTER_RANGE_ITEM_COUNT = 2
_MAX_EVENT_WAIT_SECONDS = 86_400.0

JitterDistribution: TypeAlias = Literal['uniform', 'normal', 'triangular']
RetryBackoff: TypeAlias = Literal['constant', 'linear', 'exponential']
RunStatus: TypeAlias = Literal[
    'pending',
    'running',
    'succeeded',
    'failed',
    'skipped_misfire',
    'skipped_jitter',
    'skipped_overlap',
    'cancelled',
]
MisfirePolicy: TypeAlias = Literal['skip', 'fire_now', 'coalesce']
OverlapPolicy: TypeAlias = Literal['skip', 'queue', 'replace']
BackpressurePolicy: TypeAlias = Literal['drop_oldest', 'drop_newest', 'block']


class _CronIterator(Protocol):
    def get_next(self, ret_type: type[datetime]) -> datetime: ...


class _CroniterClass(Protocol):
    is_valid: Callable[[str], bool]

    def __call__(self, expression: str, start_time: datetime) -> _CronIterator: ...


class Schedule:
    """Iterates the sequence of scheduled fire times."""

    tz: tzinfo

    def next_after(self, after: datetime) -> datetime | None:
        """Return the next scheduled time strictly greater than ``after``."""
        raise NotImplementedError

    def upcoming(self, count: int, *, after: datetime | None = None) -> list[datetime]:
        """Return up to ``count`` upcoming scheduled times."""
        cursor = after if after is not None else datetime.now(tz=self.tz)
        output: list[datetime] = []
        for _ in range(count):
            next_at = self.next_after(cursor)
            if next_at is None:
                break
            output.append(next_at)
            cursor = next_at
        return output


class CronSchedule(Schedule):
    """Cron expression evaluated by croniter."""

    def __init__(self, expression: str, tz: str | timezone | None = None) -> None:
        """Create a cron schedule from an expression and timezone."""
        croniter_cls = cast('_CroniterClass', cast('object', croniter))
        if not croniter_cls.is_valid(expression):
            message = f'Invalid cron expression: {expression!r}'
            raise ValueError(message)
        self._croniter_cls = croniter_cls
        self.expression = expression
        self.tz = _resolve_timezone(tz)

    def next_after(self, after: datetime) -> datetime | None:
        """Return the next cron occurrence strictly after ``after``."""
        resolved = _with_timezone(after, self.tz)
        iterator = self._croniter_cls(self.expression, resolved)
        return iterator.get_next(datetime)


class IntervalSchedule(Schedule):
    """Fixed interval schedule."""

    def __init__(
        self,
        interval: timedelta,
        *,
        start: datetime | None = None,
        tz: str | timezone | None = None,
    ) -> None:
        """Create an interval schedule."""
        if interval <= timedelta(0):
            message = 'IntervalSchedule.interval must be positive'
            raise ValueError(message)
        self.interval = interval
        self.tz = _resolve_timezone(tz)
        self.start = _with_timezone(start or datetime.now(tz=self.tz), self.tz)

    def next_after(self, after: datetime) -> datetime | None:
        """Return the next fixed interval after ``after``."""
        resolved = _with_timezone(after, self.tz)
        if resolved < self.start:
            return self.start
        elapsed = resolved - self.start
        steps = int(elapsed.total_seconds() // self.interval.total_seconds()) + 1
        return self.start + steps * self.interval


class AtSchedule(Schedule):
    """Single scheduled fire time."""

    def __init__(self, when: datetime, *, tz: str | timezone | None = None) -> None:
        """Create a one-shot schedule."""
        self.tz = _resolve_timezone(tz)
        self.when = _with_timezone(when, self.tz)

    def next_after(self, after: datetime) -> datetime | None:
        """Return ``when`` if it is still in the future."""
        resolved = _with_timezone(after, self.tz)
        if resolved >= self.when:
            return None
        return self.when


@dataclass(frozen=True)
class JitterSpec:
    """Bounded jitter applied to a scheduled fire time."""

    min: timedelta = timedelta(0)
    max: timedelta = timedelta(0)
    distribution: JitterDistribution = 'uniform'
    sigma: timedelta | None = None
    align_to: timedelta | None = None
    skip_if_overruns_next: bool = True
    seed: int | None = None
    _rng: random.Random = field(init=False, repr=False, compare=False)
    _rng_lock: threading.Lock = field(init=False, repr=False, compare=False)

    def __post_init__(self) -> None:
        if self.min > self.max:
            message = 'JitterSpec.min must be <= JitterSpec.max'
            raise ValueError(message)
        if self.align_to is not None and self.align_to <= timedelta(0):
            message = 'JitterSpec.align_to must be a positive timedelta'
            raise ValueError(message)
        object.__setattr__(
            self,
            '_rng',
            random.Random(self.seed),  # noqa: S311 - scheduling jitter is not cryptographic
        )
        object.__setattr__(self, '_rng_lock', threading.Lock())

    @classmethod
    def symmetric(
        cls,
        amount: timedelta | float,
        *,
        distribution: JitterDistribution = 'uniform',
        seed: int | None = None,
    ) -> JitterSpec:
        """Build a symmetric jitter range."""
        delta = _coerce_offset(amount)
        return cls(min=-delta, max=delta, distribution=distribution, seed=seed)

    @classmethod
    def from_value(
        cls,
        value: JitterSpec | timedelta | float | tuple[timedelta | float, ...] | None,
    ) -> JitterSpec | None:
        """Coerce shorthand inputs into a JitterSpec."""
        if value is None or isinstance(value, JitterSpec):
            return value
        if isinstance(value, tuple) and len(value) == _JITTER_RANGE_ITEM_COUNT:
            return cls(min=_coerce_offset(value[0]), max=_coerce_offset(value[1]))
        if isinstance(value, timedelta | int | float):
            return cls.symmetric(value)
        message = f'unsupported jitter shorthand: {value!r}'
        raise TypeError(message)

    def sample(self) -> timedelta:
        """Draw one jitter offset."""
        if self.min == self.max:
            offset = self.min
        else:
            low = self.min.total_seconds()
            high = self.max.total_seconds()
            with self._rng_lock:
                if self.distribution == 'normal':
                    midpoint = (low + high) / 2
                    sigma = (
                        self.sigma.total_seconds() if self.sigma is not None else (high - low) / 6
                    )
                    sampled = self._rng.gauss(midpoint, sigma)
                    sampled = max(low, min(high, sampled))
                elif self.distribution == 'triangular':
                    sampled = self._rng.triangular(low, high)
                else:
                    sampled = self._rng.uniform(low, high)
            offset = timedelta(seconds=sampled)
        if self.align_to is None:
            return offset
        grid = self.align_to.total_seconds()
        aligned = timedelta(seconds=round(offset.total_seconds() / grid) * grid)
        return max(self.min, min(self.max, aligned))

    def apply(
        self,
        scheduled_at: datetime,
        next_scheduled_at: datetime | None = None,
    ) -> tuple[datetime, timedelta, bool]:
        """Apply jitter to a fire time."""
        offset = self.sample()
        fire_at = scheduled_at + offset
        skipped = (
            self.skip_if_overruns_next
            and next_scheduled_at is not None
            and fire_at >= next_scheduled_at
        )
        return fire_at, offset, skipped


@dataclass(frozen=True)
class Retry:
    """Retry policy applied to scheduled fires."""

    max_attempts: int = 1
    backoff: RetryBackoff = 'constant'
    base: timedelta = timedelta(seconds=5)
    factor: float = 2.0
    max_delay: timedelta | None = timedelta(minutes=10)
    retry_on: tuple[type[BaseException], ...] = (Exception,)

    def delay_for_attempt(self, attempt: int) -> timedelta:
        """Return the delay before ``attempt``."""
        if attempt <= 1:
            return timedelta(0)
        if self.backoff == 'linear':
            delay = self.base * (attempt - 1)
        elif self.backoff == 'exponential':
            delay = self.base * (self.factor ** (attempt - 2))
        else:
            delay = self.base
        if self.max_delay is not None and delay > self.max_delay:
            return self.max_delay
        return delay

    def should_retry(self, exc: BaseException, attempt: int) -> bool:
        """Return True when ``exc`` should be retried."""
        return attempt < self.max_attempts and isinstance(exc, self.retry_on)


@dataclass
class RunRecord:
    """Outcome of a single scheduled fire."""

    scheduled_at: datetime
    fire_id: str
    fired_at: datetime | None = None
    finished_at: datetime | None = None
    jitter_offset: timedelta = timedelta(0)
    attempt: int = 1
    status: RunStatus = 'pending'
    result: object | None = None
    error: BaseException | None = None
    metadata: dict[str, object] = field(default_factory=dict)

    @property
    def duration(self) -> timedelta | None:
        """Return elapsed run duration when available."""
        if self.fired_at is None or self.finished_at is None:
            return None
        return self.finished_at - self.fired_at


JobCallback: TypeAlias = Callable[[RunRecord], object | Awaitable[object]]


class ScheduledJob:
    """Handle for managing a scheduled invocation."""

    def __init__(  # noqa: PLR0913 - Mirrors v1 scheduling handle construction.
        self,
        *,
        name: str,
        schedule: Schedule,
        runner: Callable[[RunRecord], object],
        max_runs: int | None = None,
        start_at: datetime | None = None,
        end_at: datetime | None = None,
        emit_events: bool = False,
        jitter: JitterSpec | None = None,
        retry: Retry | None = None,
        misfire: MisfirePolicy = 'coalesce',
        max_overlap: int = 1,
        overlap_policy: OverlapPolicy = 'skip',
    ) -> None:
        """Create a scheduled job handle."""
        if overlap_policy not in {'skip', 'queue', 'replace'}:
            message = f'unsupported overlap policy: {overlap_policy!r}'
            raise ValueError(message)
        if misfire not in {'skip', 'fire_now', 'coalesce'}:
            message = f'unsupported misfire policy: {misfire!r}'
            raise ValueError(message)
        self.id = str(uuid.uuid4())
        self.name = name
        self.schedule = schedule
        self.max_runs = max_runs
        self.start_at = start_at
        self.end_at = end_at
        self.emit_events = emit_events
        self._jitter = jitter
        self._retry = retry or Retry()
        self._misfire: MisfirePolicy = misfire
        self._max_overlap: int = max_overlap
        self._overlap_policy: OverlapPolicy = overlap_policy
        self._runner = runner
        self._is_running = False
        self._is_paused = False
        self._is_done = False
        self._next_run_at: datetime | None = None
        self._fire_count = 0
        self._success_count = 0
        self._failure_count = 0
        self._skip_count = 0
        self._cancelled_count = 0
        self._history: list[RunRecord] = []
        self._history_limit = 1000
        self._on_fire: list[JobCallback] = []
        self._on_success: list[JobCallback] = []
        self._on_error: list[JobCallback] = []
        self._on_skip: list[JobCallback] = []
        self._stop_event = threading.Event()
        self._worker: threading.Thread | None = None
        self._state_lock = threading.RLock()
        self._slot_available = threading.Condition(self._state_lock)
        self._active_records: dict[str, RunRecord] = {}
        self._queued_records: deque[RunRecord] = deque()
        self._execution_threads: set[threading.Thread] = set()
        self._active_threads: set[threading.Thread] = set()

    def start(self) -> ScheduledJob:
        """Start the background scheduler."""
        with self._state_lock:
            if self._is_running or self._is_done:
                return self
            self._stop_event.clear()
            self._is_running = True
        register_job(self)
        self._worker = threading.Thread(
            target=self._run_loop,
            name=f'maivn-scheduled-job-{self.id}',
            daemon=True,
        )
        self._worker.start()
        return self

    def stop(self, *, drain: bool = True, timeout: float | None = None) -> None:
        """Stop the job."""
        self._stop_event.set()
        with self._state_lock:
            self._is_running = False
        if not drain:
            self._cancel_queued_runs()
        with self._slot_available:
            self._slot_available.notify_all()
            # The timer drains this callback's thread. Joining it here would
            # make both wait forever; the callback must first return.
            if threading.current_thread() in self._active_threads | self._execution_threads:
                return
        worker = self._worker
        if drain and worker is not None and worker is not threading.current_thread():
            worker.join(timeout=timeout)
        if drain:
            self._wait_for_drain(timeout)
        with self._state_lock:
            self._is_done = True

    def pause(self) -> None:
        """Pause the job."""
        self._is_paused = True

    def resume(self) -> None:
        """Resume the job."""
        self._is_paused = False

    def trigger_now(self) -> RunRecord:
        """Run the scheduled callable immediately."""
        return self._execute(datetime.now(timezone.utc), manual=True)

    def _execute(self, scheduled_at: datetime, *, manual: bool = False) -> RunRecord:
        record = self._new_record(scheduled_at, manual=manual)
        self._execute_record(record)
        return record

    def _new_record(self, scheduled_at: datetime, *, manual: bool) -> RunRecord:
        metadata: dict[str, object] = {'manual': True} if manual else {}
        record = RunRecord(
            scheduled_at=scheduled_at,
            fire_id=str(uuid.uuid4()),
            metadata=metadata,
        )
        with self._state_lock:
            self._fire_count += 1
        return record

    def _execute_record(self, record: RunRecord) -> None:
        manual = bool(record.metadata.get('manual'))
        if not manual and not self._prepare_misfire(record):
            record.finished_at = datetime.now(timezone.utc)
            self._finish_record(record)
            return
        if not manual and not self._prepare_jitter(record):
            record.finished_at = datetime.now(timezone.utc)
            self._finish_record(record)
            return

        if not self._claim_slot(record):
            record.finished_at = datetime.now(timezone.utc)
            if record.status != 'cancelled':
                self._finish_record(record)
            return

        try:
            record.status = 'running'
            record.fired_at = datetime.now(timezone.utc)
            self._dispatch(self._on_fire, record)
            self._run_with_retry(record)
            record.finished_at = datetime.now(timezone.utc)
            self._finish_record(record)
        finally:
            self._release_slot(record)

    def _prepare_misfire(self, record: RunRecord) -> bool:
        drift = datetime.now(timezone.utc) - record.scheduled_at.astimezone(timezone.utc)
        misfire_grace = timedelta(seconds=30)
        if drift <= misfire_grace:
            return True
        if self._misfire == 'skip':
            record.status = 'skipped_misfire'
            record.metadata['misfire_drift_seconds'] = drift.total_seconds()
            return False
        if self._misfire == 'coalesce':
            record.metadata['coalesced_drift_seconds'] = drift.total_seconds()
        return True

    def _prepare_jitter(self, record: RunRecord) -> bool:
        if self._jitter is None:
            return True
        next_scheduled_at = self.schedule.next_after(record.scheduled_at)
        fire_at, offset, skipped = self._jitter.apply(
            record.scheduled_at,
            next_scheduled_at,
        )
        record.jitter_offset = offset
        if skipped:
            record.status = 'skipped_jitter'
            return False
        delay = max(
            0.0,
            (fire_at.astimezone(timezone.utc) - datetime.now(timezone.utc)).total_seconds(),
        )
        if not self._stop_event.wait(delay):
            return True
        record.status = 'cancelled'
        return False

    def _run_with_retry(self, record: RunRecord) -> None:
        attempt = 1
        while True:
            record.attempt = attempt
            try:
                result = self._runner(record)
                if inspect.isawaitable(result):
                    result = asyncio.run(
                        _await_result(cast('Awaitable[object]', result)),
                    )
            except BaseException as exc:  # noqa: BLE001 - scheduled user callable boundary.
                if self._retry.should_retry(exc, attempt):
                    attempt += 1
                    delay = self._retry.delay_for_attempt(attempt).total_seconds()
                    if self._stop_event.wait(delay):
                        record.error = exc
                        record.status = 'cancelled'
                        break
                    continue
                record.error = exc
                record.status = 'failed'
                break
            if record.status in {'pending', 'running'}:
                record.result = result
                record.status = 'succeeded'
            elif record.status == 'succeeded' and record.result is None:
                record.result = result
            break

    def _finish_record(self, record: RunRecord) -> RunRecord:
        self._record_terminal(record)
        self._record_history(record)
        if self._max_runs_reached() and (
            self.success_count + self.failure_count + self.skip_count + self._cancelled_count
            == self.fire_count
        ):
            with self._state_lock:
                self._is_done = True
                self._is_running = False
            # This only stops future timer ticks. Runs already admitted to an
            # overlap queue remain drainable and are never abandoned here.
            self._stop_event.set()
            with self._slot_available:
                self._slot_available.notify_all()
        return record

    def _run_loop(self) -> None:
        try:
            while not self._stop_event.is_set():
                if self._is_paused:
                    self._stop_event.wait(0.05)
                    continue
                if self._max_runs_reached():
                    break

                now = datetime.now(timezone.utc)
                next_at = self.schedule.next_after(now)
                next_at = self._apply_start_window(next_at)
                if next_at is None or (self.end_at is not None and next_at > self.end_at):
                    self._next_run_at = None
                    break

                self._next_run_at = next_at
                delay = max(
                    0.0,
                    (next_at.astimezone(timezone.utc) - now).total_seconds(),
                )
                if self._stop_event.wait(min(delay, _MAX_EVENT_WAIT_SECONDS)):
                    break
                if delay > _MAX_EVENT_WAIT_SECONDS:
                    continue
                if self._is_paused or self._max_runs_reached():
                    continue
                self._launch_scheduled_execution(next_at)
        finally:
            self._wait_for_drain(None)
            with self._state_lock:
                self._is_running = False
                self._is_done = True

    def _apply_start_window(self, next_at: datetime | None) -> datetime | None:
        if next_at is None or self.start_at is None:
            return next_at
        start_at = _with_timezone(self.start_at, self.schedule.tz)
        if next_at >= start_at:
            return next_at
        return self.schedule.next_after(start_at - timedelta(microseconds=1))

    def _launch_scheduled_execution(self, scheduled_at: datetime) -> None:
        record = self._new_record(scheduled_at, manual=False)

        def execute() -> None:
            try:
                self._execute_record(record)
            finally:
                with self._slot_available:
                    self._execution_threads.discard(threading.current_thread())
                    self._slot_available.notify_all()

        thread = threading.Thread(
            target=execute,
            name=f'maivn-scheduled-fire-{record.fire_id}',
            daemon=True,
        )
        with self._slot_available:
            self._execution_threads.add(thread)
        thread.start()

    @property
    def is_running(self) -> bool:
        """Return True when the job is running."""
        with self._state_lock:
            return self._is_running

    @property
    def is_paused(self) -> bool:
        """Return True when the job is paused."""
        with self._state_lock:
            return self._is_paused

    @property
    def is_done(self) -> bool:
        """Return True when the job has stopped or exhausted its schedule."""
        with self._state_lock:
            return self._is_done

    @property
    def next_run_at(self) -> datetime | None:
        """Return the next scheduled run time when known."""
        with self._state_lock:
            return self._next_run_at

    @property
    def fire_count(self) -> int:
        """Return the number of fires that have started."""
        with self._state_lock:
            return self._fire_count

    @property
    def success_count(self) -> int:
        """Return the number of successful fires."""
        with self._state_lock:
            return self._success_count

    @property
    def failure_count(self) -> int:
        """Return the number of failed fires."""
        with self._state_lock:
            return self._failure_count

    @property
    def skip_count(self) -> int:
        """Return the number of skipped fires."""
        with self._state_lock:
            return self._skip_count

    def history(self, *, limit: int | None = None) -> list[RunRecord]:
        """Return run history."""
        with self._state_lock:
            if limit is None:
                return list(self._history)
            return list(self._history[-limit:])

    @property
    def last_run(self) -> RunRecord | None:
        """Return the most recent run record."""
        with self._state_lock:
            return self._history[-1] if self._history else None

    def next_runs(self, n: int = 5) -> list[datetime]:
        """Return the next ``n`` scheduled fire times."""
        runs = self.schedule.upcoming(n)
        with self._state_lock:
            self._next_run_at = runs[0] if runs else None
        return runs

    def on_fire(self, cb: JobCallback) -> ScheduledJob:
        """Register a callback for each started fire."""
        self._on_fire.append(cb)
        return self

    def on_success(self, cb: JobCallback) -> ScheduledJob:
        """Register a callback for successful fires."""
        self._on_success.append(cb)
        return self

    def on_error(self, cb: JobCallback) -> ScheduledJob:
        """Register a callback for failed fires."""
        self._on_error.append(cb)
        return self

    def on_skip(self, cb: JobCallback) -> ScheduledJob:
        """Register a callback for skipped fires."""
        self._on_skip.append(cb)
        return self

    def _record_terminal(self, record: RunRecord) -> None:
        callbacks: list[JobCallback] = []
        with self._state_lock:
            if record.status == 'succeeded':
                self._success_count += 1
                callbacks = self._on_success
            elif record.status.startswith('skipped'):
                self._skip_count += 1
                callbacks = self._on_skip
            elif record.status == 'failed':
                self._failure_count += 1
                callbacks = self._on_error
            elif record.status == 'cancelled':
                self._cancelled_count += 1
        if callbacks:
            self._dispatch(callbacks, record)

    def _record_history(self, record: RunRecord) -> None:
        with self._state_lock:
            self._history.append(record)
            if len(self._history) > self._history_limit:
                del self._history[: len(self._history) - self._history_limit]

    def _claim_slot(self, record: RunRecord) -> bool:
        with self._slot_available:
            if self._has_available_slot() and not self._queued_records:
                record.metadata.setdefault('cancellation_event', threading.Event())
                self._active_records[record.fire_id] = record
                self._active_threads.add(threading.current_thread())
                return True
            if self._overlap_policy == 'skip':
                record.status = 'skipped_overlap'
                return False
            if self._overlap_policy == 'replace':
                self._request_replacement(record)
            self._queued_records.append(record)
            while True:
                if record.status == 'cancelled':
                    return False
                if self._queued_records[0] is record and self._has_available_slot():
                    _ = self._queued_records.popleft()
                    record.metadata.setdefault('cancellation_event', threading.Event())
                    self._active_records[record.fire_id] = record
                    self._active_threads.add(threading.current_thread())
                    return True
                self._slot_available.wait()

    def _request_replacement(self, record: RunRecord) -> None:
        """Request cooperative cancellation and retain only the latest replacement."""
        record.metadata['replaces_active_run'] = bool(self._active_records)
        for active in self._active_records.values():
            active.metadata['cancellation_requested'] = True
            token = active.metadata.get('cancellation_event')
            if isinstance(token, threading.Event):
                token.set()
        while self._queued_records:
            superseded = self._queued_records.popleft()
            superseded.status = 'cancelled'
            superseded.metadata['replaced_by'] = record.fire_id
            superseded.finished_at = datetime.now(timezone.utc)
            self._finish_record(superseded)
        self._slot_available.notify_all()

    def _release_slot(self, record: RunRecord) -> None:
        with self._slot_available:
            self._active_records.pop(record.fire_id, None)
            self._active_threads.discard(threading.current_thread())
            self._slot_available.notify_all()

    def _has_available_slot(self) -> bool:
        return self._max_overlap <= 0 or len(self._active_records) < self._max_overlap

    def _cancel_queued_runs(self) -> None:
        cancelled: list[RunRecord] = []
        with self._slot_available:
            while self._queued_records:
                record = self._queued_records.popleft()
                record.status = 'cancelled'
                record.finished_at = datetime.now(timezone.utc)
                cancelled.append(record)
            self._slot_available.notify_all()
        for record in cancelled:
            self._finish_record(record)

    def _wait_for_drain(self, timeout: float | None) -> None:
        deadline = (
            None if timeout is None else datetime.now(timezone.utc) + timedelta(seconds=timeout)
        )
        with self._slot_available:
            while self._active_records or self._queued_records or self._execution_threads:
                if deadline is None:
                    self._slot_available.wait()
                    continue
                remaining = (deadline - datetime.now(timezone.utc)).total_seconds()
                if remaining <= 0:
                    return
                self._slot_available.wait(remaining)

    def _max_runs_reached(self) -> bool:
        with self._state_lock:
            return self.max_runs is not None and self._fire_count >= self.max_runs

    def _dispatch(self, callbacks: list[JobCallback], record: RunRecord) -> None:
        for cb in callbacks:
            with suppress(Exception):
                result = cb(record)
                if inspect.isawaitable(result):
                    _run_callback_awaitable(cast('Awaitable[object]', result))


class CronInvocationBuilder:
    """Chainable builder produced by scope scheduling helpers."""

    def __init__(  # noqa: PLR0913 - Mirrors v1 scheduling builder construction.
        self,
        scope: object,
        schedule: Schedule,
        *,
        name: str | None = None,
        jitter: JitterSpec | timedelta | float | None = None,
        misfire: MisfirePolicy = 'coalesce',
        max_overlap: int = 1,
        overlap_policy: OverlapPolicy = 'skip',
        start_at: datetime | None = None,
        end_at: datetime | None = None,
        max_runs: int | None = None,
        retry: Retry | None = None,
        emit_events: bool = False,
    ) -> None:
        """Create a scheduled invocation builder."""
        self._scope = scope
        self._schedule = schedule
        self._name = name or type(scope).__name__
        self._jitter = JitterSpec.from_value(jitter)
        self._misfire: MisfirePolicy = misfire
        self._max_overlap: int = max_overlap
        self._overlap_policy: OverlapPolicy = overlap_policy
        self._start_at = start_at
        self._end_at = end_at
        self._max_runs = max_runs
        self._retry = retry or Retry()
        self._emit_events = emit_events

    def with_scope(self, scope: object) -> CronInvocationBuilder:
        """Repoint the builder at a different scope."""
        self._scope = scope
        return self

    def with_jitter(
        self,
        jitter: JitterSpec | timedelta | float | None,
    ) -> CronInvocationBuilder:
        """Set jitter."""
        self._jitter = JitterSpec.from_value(jitter)
        return self

    def with_retry(self, retry: Retry) -> CronInvocationBuilder:
        """Set retry policy."""
        self._retry = retry
        return self

    def with_overlap(self, policy: OverlapPolicy, *, max_overlap: int = 1) -> CronInvocationBuilder:
        """Set overlap policy."""
        self._overlap_policy = policy
        self._max_overlap = max_overlap
        return self

    def with_misfire(self, policy: MisfirePolicy) -> CronInvocationBuilder:
        """Set misfire policy."""
        self._misfire = policy
        return self

    def with_window(
        self,
        *,
        start_at: datetime | None = None,
        end_at: datetime | None = None,
    ) -> CronInvocationBuilder:
        """Set scheduling window."""
        self._start_at = start_at or self._start_at
        self._end_at = end_at or self._end_at
        return self

    def with_max_runs(self, max_runs: int | None) -> CronInvocationBuilder:
        """Set maximum run count."""
        self._max_runs = max_runs
        return self

    # A public fluent setter on a shipped builder: making the flag keyword-only,
    # as FBT001/FBT002 ask, would break every caller that passes it positionally.
    def with_emit_events(self, emit: bool = True) -> CronInvocationBuilder:  # noqa: FBT001, FBT002
        """Set event emission flag."""
        self._emit_events = emit
        return self

    def invoke(self, *args: object, **kwargs: object) -> ScheduledJob:
        """Schedule a sync invoke call."""
        return self._build_job('invoke', args, kwargs)

    def stream(self, *args: object, **kwargs: object) -> ScheduledJob:
        """Schedule a sync stream call."""
        return self._build_job('stream', args, kwargs)

    def batch(self, inputs: Iterable[object], **kwargs: object) -> ScheduledJob:
        """Schedule a sync batch call."""
        return self._build_job('batch', (list(inputs),), kwargs)

    def abatch(self, inputs: Iterable[object], **kwargs: object) -> ScheduledJob:
        """Schedule an async batch call."""
        return self._build_job('abatch', (list(inputs),), kwargs)

    def ainvoke(self, *args: object, **kwargs: object) -> ScheduledJob:
        """Schedule an async invoke call."""
        return self._build_job('ainvoke', args, kwargs)

    def astream(self, *args: object, **kwargs: object) -> ScheduledJob:
        """Schedule an async stream call."""
        return self._build_job('astream', args, kwargs)

    def _build_job(
        self,
        method_name: str,
        args: tuple[object, ...],
        kwargs: dict[str, object],
    ) -> ScheduledJob:
        def runner(_record: RunRecord) -> object:
            method = getattr(self._scope, method_name)
            if not callable(method):
                message = f'scope method is not callable: {method_name}'
                raise TypeError(message)
            result = method(*args, **kwargs)
            if method_name == 'stream':
                return list(cast('Iterable[object]', result))
            if method_name == 'astream':
                return _consume_async_iterable(cast('AsyncIterable[object]', result))
            return result

        job = ScheduledJob(
            name=self._name,
            schedule=self._schedule,
            runner=runner,
            max_runs=self._max_runs,
            start_at=self._start_at,
            end_at=self._end_at,
            emit_events=self._emit_events,
            jitter=self._jitter,
            retry=self._retry,
            misfire=self._misfire,
            max_overlap=self._max_overlap,
            overlap_policy=self._overlap_policy,
        )
        return job.start()


_jobs: dict[str, ScheduledJob] = {}


def list_jobs() -> list[ScheduledJob]:
    """Return every live compatibility scheduled job."""
    return list(_jobs.values())


def stop_all_jobs(*, drain: bool = True, timeout: float | None = None) -> None:
    """Stop every live compatibility scheduled job."""
    for job in list_jobs():
        job.stop(drain=drain, timeout=timeout)


def register_job(job: ScheduledJob) -> None:
    """Register a compatibility scheduled job."""
    _jobs[job.id] = job


async def _await_callback(awaitable: Awaitable[object]) -> None:
    _ = await awaitable


async def _await_result(awaitable: Awaitable[object]) -> object:
    return await awaitable


async def _consume_async_iterable(iterable: AsyncIterable[object]) -> list[object]:
    return [item async for item in iterable]


def _run_callback_awaitable(awaitable: Awaitable[object]) -> None:
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        asyncio.run(_await_callback(awaitable))
        return
    task = loop.create_task(_await_callback(awaitable))
    task.add_done_callback(_ignore_callback_task_error)


def _ignore_callback_task_error(task: asyncio.Future[None]) -> None:
    with suppress(Exception):
        _ = task.result()


def _resolve_timezone(tz: str | timezone | None) -> tzinfo:
    if tz is None:
        return timezone.utc
    if isinstance(tz, timezone):
        return tz
    return ZoneInfo(tz)


def _with_timezone(value: datetime, tz: tzinfo) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=tz)
    return value.astimezone(tz)


def _coerce_offset(value: timedelta | float) -> timedelta:
    if isinstance(value, timedelta):
        return value
    return timedelta(seconds=float(value))


__all__ = [
    'AtSchedule',
    'BackpressurePolicy',
    'CronInvocationBuilder',
    'CronSchedule',
    'IntervalSchedule',
    'JitterDistribution',
    'JitterSpec',
    'JobCallback',
    'MisfirePolicy',
    'OverlapPolicy',
    'Retry',
    'RetryBackoff',
    'RunRecord',
    'RunStatus',
    'Schedule',
    'ScheduledJob',
    'list_jobs',
    'register_job',
    'stop_all_jobs',
]
