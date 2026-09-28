"""Unit tests for v1-compatible scheduling observers."""

from __future__ import annotations

import asyncio
import threading
import time
from datetime import datetime, timedelta, timezone
from typing import TYPE_CHECKING, Any

import pytest

if TYPE_CHECKING:
    from collections.abc import AsyncIterator, Callable, Iterator

from maivn._internal.compat.scheduling import (
    AtSchedule,
    CronInvocationBuilder,
    CronSchedule,
    IntervalSchedule,
    JitterSpec,
    MisfirePolicy,
    Retry,
    RunRecord,
    Schedule,
    ScheduledJob,
)

_EXPECTED_RETRY_ATTEMPTS = 2
_EXPECTED_OVERLAP_FIRES = 2
_QUEUE_THIRD_FIRE_COUNT = 3
_MISFIRE_GRACE_SECONDS = 30


def _wait_for(predicate: Callable[[], bool], *, timeout: float = 1.0) -> bool:
    """Wait for a concurrent scheduler condition without sleeping a fixed duration."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(0.005)
    return predicate()


def test_cron_schedule_honors_the_expression_cadence() -> None:
    """A five-minute cron expression advances on five-minute boundaries."""
    schedule = CronSchedule('*/5 * * * *', tz=timezone.utc)
    after = datetime(2026, 7, 22, 21, 51, 30, tzinfo=timezone.utc)

    assert schedule.upcoming(3, after=after) == [
        datetime(2026, 7, 22, 21, 55, tzinfo=timezone.utc),
        datetime(2026, 7, 22, 22, 0, tzinfo=timezone.utc),
        datetime(2026, 7, 22, 22, 5, tzinfo=timezone.utc),
    ]


def test_cron_schedule_rejects_invalid_expressions() -> None:
    """Invalid cron expressions fail at configuration time."""
    with pytest.raises(ValueError, match='Invalid cron expression'):
        CronSchedule('not a cron expression')


def test_jitter_spec_rejects_invalid_bounds_and_alignment() -> None:
    """Jitter ranges and alignment grids must be internally valid."""
    with pytest.raises(ValueError, match='min must be <='):
        JitterSpec(min=timedelta(seconds=2), max=timedelta(seconds=1))

    with pytest.raises(ValueError, match='align_to must be a positive'):
        JitterSpec(align_to=timedelta(0))


def test_seeded_jitter_advances_a_reproducible_sequence() -> None:
    """A seed fixes the sequence without returning the same sample forever."""
    first = JitterSpec(
        min=timedelta(seconds=-10),
        max=timedelta(seconds=10),
        seed=42,
    )
    second = JitterSpec(
        min=timedelta(seconds=-10),
        max=timedelta(seconds=10),
        seed=42,
    )

    first_samples = [first.sample() for _ in range(3)]
    second_samples = [second.sample() for _ in range(3)]

    assert len(set(first_samples)) > 1
    assert first_samples == second_samples


def test_jitter_sample_aligns_to_configured_grid() -> None:
    """Sampled offsets snap to the requested grid while staying in bounds."""
    spec = JitterSpec(
        min=timedelta(0),
        max=timedelta(seconds=10),
        align_to=timedelta(seconds=5),
        seed=42,
    )

    assert spec.sample() == timedelta(seconds=5)


def test_scheduled_job_fires_automatically_when_due() -> None:
    """A started job executes at its scheduled time without a manual trigger."""
    fired = threading.Event()
    scheduled_at = datetime.now(timezone.utc) + timedelta(milliseconds=50)

    def runner(_record: RunRecord) -> str:
        return 'automatic'

    job = ScheduledJob(
        name='automatic-fire',
        schedule=IntervalSchedule(timedelta(minutes=1), start=scheduled_at),
        runner=runner,
        max_runs=1,
    )

    try:
        job.on_success(lambda _record: fired.set()).start()

        assert fired.wait(timeout=1.0), 'scheduled job did not fire when due'
        assert job.fire_count == 1
        assert job.success_count == 1
        assert job.is_done is True
        assert job.last_run is not None
        assert job.last_run.result == 'automatic'
    finally:
        job.stop()


def test_scheduled_job_keeps_worker_alive_for_far_future_run() -> None:
    """A long wait is chunked so platform timeout limits do not kill the worker."""
    job = ScheduledJob(
        name='far-future',
        schedule=AtSchedule(datetime(2099, 1, 1, tzinfo=timezone.utc)),
        runner=lambda _record: None,
    )

    try:
        job.start()
        _ = threading.Event().wait(0.05)
        worker = vars(job)['_worker']

        assert isinstance(worker, threading.Thread)
        assert worker.is_alive()
    finally:
        job.stop()


def test_cron_builder_applies_jitter_and_retries_before_success() -> None:
    """Scheduled builder fires honor deterministic jitter and retry policy."""
    succeeded = threading.Event()

    class FlakyScope:
        def __init__(self) -> None:
            self.attempts = 0

        def invoke(self) -> str:
            self.attempts += 1
            if self.attempts == 1:
                message = 'transient'
                raise RuntimeError(message)
            return 'recovered'

    scope = FlakyScope()
    job = CronInvocationBuilder(
        scope,
        AtSchedule(datetime.now(timezone.utc) + timedelta(milliseconds=100)),
        jitter=JitterSpec(
            min=timedelta(milliseconds=20),
            max=timedelta(milliseconds=20),
        ),
        retry=Retry(max_attempts=2, base=timedelta(0)),
        max_runs=1,
    ).invoke()

    try:
        job.on_success(lambda _record: succeeded.set())

        assert succeeded.wait(timeout=1.5), 'scheduled retry did not recover'
        assert scope.attempts == _EXPECTED_RETRY_ATTEMPTS
        assert job.success_count == 1
        assert job.failure_count == 0
        assert job.last_run is not None
        assert job.last_run.status == 'succeeded'
        assert job.last_run.attempt == _EXPECTED_RETRY_ATTEMPTS
        assert job.last_run.jitter_offset == timedelta(milliseconds=20)
        assert job.last_run.result == 'recovered'
    finally:
        job.stop()


def test_cron_builder_awaits_async_scope_method() -> None:
    """An async scheduled scope method is awaited and records its resolved result."""
    called = threading.Event()
    succeeded = threading.Event()

    class AsyncScope:
        async def ainvoke(self) -> str:
            await asyncio.sleep(0)
            called.set()
            return 'async-result'

    job = CronInvocationBuilder(
        AsyncScope(),
        AtSchedule(datetime.now(timezone.utc) + timedelta(milliseconds=100)),
        max_runs=1,
    ).ainvoke()

    try:
        job.on_success(lambda _record: succeeded.set())

        assert succeeded.wait(timeout=1.0), 'async scheduled call did not finish'
        assert called.is_set()
        assert job.last_run is not None
        assert job.last_run.result == 'async-result'
    finally:
        job.stop()


def test_cron_builder_consumes_sync_stream() -> None:
    """A scheduled sync stream is consumed and stored as an ordered result list."""
    succeeded = threading.Event()

    class StreamScope:
        def stream(self) -> Iterator[str]:
            yield 'first'
            yield 'second'

    job = CronInvocationBuilder(
        StreamScope(),
        AtSchedule(datetime.now(timezone.utc) + timedelta(milliseconds=100)),
        max_runs=1,
    ).stream()

    try:
        job.on_success(lambda _record: succeeded.set())

        assert succeeded.wait(timeout=1.0), 'scheduled stream did not finish'
        assert job.last_run is not None
        assert job.last_run.result == ['first', 'second']
    finally:
        job.stop()


def test_cron_builder_consumes_async_stream() -> None:
    """A scheduled async stream is consumed and stored as an ordered result list."""
    yielded = threading.Event()
    succeeded = threading.Event()

    class AsyncStreamScope:
        async def astream(self) -> AsyncIterator[str]:
            await asyncio.sleep(0)
            yielded.set()
            yield 'first'
            yield 'second'

    job = CronInvocationBuilder(
        AsyncStreamScope(),
        AtSchedule(datetime.now(timezone.utc) + timedelta(milliseconds=100)),
        max_runs=1,
    ).astream()

    try:
        job.on_success(lambda _record: succeeded.set())

        assert succeeded.wait(timeout=1.0), 'scheduled async stream did not finish'
        assert yielded.is_set()
        assert job.last_run is not None
        assert job.last_run.result == ['first', 'second']
    finally:
        job.stop()


def test_scheduled_job_observers_counters_and_next_runs() -> None:
    """Callbacks observe fire/result records in v1 order and counters increment."""
    start = datetime(2099, 1, 1, tzinfo=timezone.utc)
    seen: list[tuple[str, str, timedelta | None]] = []

    def runner(record: RunRecord) -> dict[str, object]:
        seen.append(('runner', record.fire_id, record.duration))
        return {'ok': True}

    job = ScheduledJob(
        name='observer-success',
        schedule=IntervalSchedule(timedelta(minutes=5), start=start),
        runner=runner,
    ).start()

    assert job.on_fire(lambda r: seen.append(('fire', r.fire_id, r.duration))) is job
    assert job.on_success(lambda r: seen.append(('success', r.fire_id, r.duration))) is job

    record = job.trigger_now()

    assert record.status == 'succeeded'
    assert job.is_done is False
    assert job.fire_count == 1
    assert job.success_count == 1
    assert job.failure_count == 0
    assert job.skip_count == 0
    assert [event[0] for event in seen] == ['fire', 'runner', 'success']
    assert seen[0][1] == record.fire_id
    assert seen[-1][2] is not None
    assert job.last_run is record
    assert job.history() == [record]
    assert job.next_runs(3) == [
        start,
        start + timedelta(minutes=5),
        start + timedelta(minutes=10),
    ]


def test_scheduled_job_error_and_skip_observers_increment_counters() -> None:
    """Terminal callbacks receive failed and skipped fire records."""
    failures: list[BaseException | None] = []
    skips: list[str] = []

    def failing_runner(_record: RunRecord) -> object:
        message = 'boom'
        raise RuntimeError(message)

    failed_job = ScheduledJob(
        name='observer-error',
        schedule=IntervalSchedule(timedelta(minutes=1)),
        runner=failing_runner,
    ).start()
    _ = failed_job.on_error(lambda r: failures.append(r.error))

    failed_record = failed_job.trigger_now()

    assert failed_record.status == 'failed'
    assert isinstance(failures[0], RuntimeError)
    assert failed_job.fire_count == 1
    assert failed_job.success_count == 0
    assert failed_job.failure_count == 1
    assert failed_job.skip_count == 0

    def skipped_runner(record: RunRecord) -> object:
        record.status = 'skipped_overlap'
        return None

    skipped_job = ScheduledJob(
        name='observer-skip',
        schedule=IntervalSchedule(timedelta(minutes=1)),
        runner=skipped_runner,
    ).start()
    _ = skipped_job.on_skip(lambda r: skips.append(r.status))

    skipped_record = skipped_job.trigger_now()

    assert skipped_record.status == 'skipped_overlap'
    assert skips == ['skipped_overlap']
    assert skipped_job.fire_count == 1
    assert skipped_job.success_count == 0
    assert skipped_job.failure_count == 0
    assert skipped_job.skip_count == 1


def test_cron_invocation_builder_batch_schedules_scope_batch() -> None:
    """The scheduled builder exposes the v1 ``batch`` terminal method."""

    class BatchScope:
        def __init__(self) -> None:
            self.calls: list[tuple[list[object], dict[str, object]]] = []

        def batch(self, inputs: list[object], **kwargs: object) -> list[str]:
            self.calls.append((inputs, kwargs))
            return ['done']

    scope = BatchScope()
    inputs: list[Any] = ['one', {'two': 2}]
    job = CronInvocationBuilder(
        scope,
        IntervalSchedule(timedelta(minutes=1)),
        max_runs=1,
    ).batch(inputs, max_concurrency=2)

    record = job.trigger_now()

    assert record.status == 'succeeded'
    assert record.result == ['done']
    assert job.is_done is True
    assert scope.calls == [(['one', {'two': 2}], {'max_concurrency': 2})]


def test_due_fires_skip_when_a_slow_run_occupies_the_only_slot() -> None:
    """The timer continues observing ticks while a slow callable occupies its slot."""
    started = threading.Event()
    release = threading.Event()

    def runner(_record: RunRecord) -> None:
        started.set()
        assert release.wait(timeout=1.0)

    job = ScheduledJob(
        name='due-overlap-skip',
        schedule=IntervalSchedule(
            timedelta(milliseconds=20),
            start=datetime.now(timezone.utc) + timedelta(milliseconds=20),
        ),
        runner=runner,
        max_runs=2,
        overlap_policy='skip',
        max_overlap=1,
    ).start()
    try:
        assert started.wait(timeout=1.0)
        assert _wait_for(lambda: any(r.status == 'skipped_overlap' for r in job.history()))
        assert not job.is_done, 'a skipped last tick must not hide the active run'
    finally:
        release.set()
        job.stop(drain=True, timeout=1.0)

    assert job.fire_count == _EXPECTED_OVERLAP_FIRES
    assert job.skip_count == 1
    assert [record.status for record in job.history()] == ['skipped_overlap', 'succeeded']


def test_fire_callback_can_stop_its_own_schedule_without_deadlock() -> None:
    """A callback may stop future ticks without waiting on its own execution."""
    returned = threading.Event()
    job = ScheduledJob(
        name='callback-stop',
        schedule=AtSchedule(datetime.now(timezone.utc) + timedelta(milliseconds=50)),
        runner=lambda _record: 'completed',
    )

    def stop_from_callback(_record: RunRecord) -> None:
        job.stop()
        returned.set()

    job.on_fire(stop_from_callback).start()
    assert returned.wait(timeout=1.0), 'stop deadlocked inside its own callback'
    job.stop(timeout=1.0)
    assert job.success_count == 1


def test_manual_triggers_respect_overlap_capacity() -> None:
    """Manual fires use the same slot budget as scheduled fires."""
    started = threading.Event()
    release = threading.Event()
    active = 0
    peak_active = 0
    state_lock = threading.Lock()

    def runner(_record: RunRecord) -> None:
        nonlocal active, peak_active
        with state_lock:
            active += 1
            peak_active = max(peak_active, active)
        started.set()
        assert release.wait(timeout=1.0)
        with state_lock:
            active -= 1

    job = ScheduledJob(
        name='manual-overlap-capacity',
        schedule=AtSchedule(datetime(2099, 1, 1, tzinfo=timezone.utc)),
        runner=runner,
        overlap_policy='skip',
        max_overlap=1,
    )
    first = threading.Thread(target=job.trigger_now)
    first.start()
    try:
        assert started.wait(timeout=1.0)
        skipped = job.trigger_now()
    finally:
        release.set()
        first.join(timeout=1.0)

    assert skipped.status == 'skipped_overlap'
    assert peak_active == 1
    assert job.skip_count == 1


def test_queued_fires_start_in_arrival_order_after_the_active_run() -> None:
    """Queue policy preserves every fire and releases them in FIFO order."""
    entered: list[str] = []
    first_started = threading.Event()
    release_first = threading.Event()
    release_second = threading.Event()

    def runner(record: RunRecord) -> None:
        entered.append(record.fire_id)
        if len(entered) == 1:
            first_started.set()
            assert release_first.wait(timeout=1.0)
        elif len(entered) == _EXPECTED_OVERLAP_FIRES:
            assert release_second.wait(timeout=1.0)

    job = ScheduledJob(
        name='queued-overlap',
        schedule=AtSchedule(datetime(2099, 1, 1, tzinfo=timezone.utc)),
        runner=runner,
        overlap_policy='queue',
        max_overlap=1,
    )
    first = threading.Thread(target=job.trigger_now)
    first.start()
    assert first_started.wait(timeout=1.0)
    second_result: list[RunRecord] = []
    third_result: list[RunRecord] = []
    second = threading.Thread(target=lambda: second_result.append(job.trigger_now()))
    third = threading.Thread(target=lambda: third_result.append(job.trigger_now()))
    second.start()
    assert _wait_for(lambda: job.fire_count == _EXPECTED_OVERLAP_FIRES)
    third.start()
    assert _wait_for(lambda: job.fire_count == _QUEUE_THIRD_FIRE_COUNT)
    release_first.set()
    assert _wait_for(lambda: len(entered) == _EXPECTED_OVERLAP_FIRES)
    release_second.set()
    first.join(timeout=1.0)
    second.join(timeout=1.0)
    third.join(timeout=1.0)

    assert len(second_result) == 1
    assert len(third_result) == 1
    assert [record.fire_id for record in job.history()] == entered


def test_builder_applies_start_window_and_misfire_policy_at_runtime() -> None:
    """The builder passes its runtime timing options through to the job."""
    calls: list[datetime] = []
    start_at = datetime.now(timezone.utc) + timedelta(milliseconds=120)

    class Scope:
        def invoke(self) -> None:
            calls.append(datetime.now(timezone.utc))

    job = CronInvocationBuilder(
        Scope(),
        IntervalSchedule(timedelta(milliseconds=20), start=datetime.now(timezone.utc)),
        start_at=start_at,
        misfire='skip',
        max_runs=1,
    ).invoke()
    try:
        assert _wait_for(lambda: len(calls) == 1, timeout=1.0)
    finally:
        job.stop(drain=True, timeout=1.0)

    assert calls[0] >= start_at


@pytest.mark.parametrize('policy', ['skip', 'coalesce', 'fire_now'])
def test_builder_applies_misfire_policy_to_one_expired_tick(policy: MisfirePolicy) -> None:
    """A missed tick skips or executes once, preserving v1 coalescing semantics."""
    calls: list[None] = []

    class LateOnceSchedule(Schedule):
        tz = timezone.utc

        def __init__(self) -> None:
            self._returned_late_fire = False

        def next_after(self, after: datetime) -> datetime | None:
            _ = after
            if self._returned_late_fire:
                return None
            self._returned_late_fire = True
            return datetime.now(timezone.utc) - timedelta(seconds=_MISFIRE_GRACE_SECONDS + 1)

    class Scope:
        def invoke(self) -> None:
            calls.append(None)

    job = CronInvocationBuilder(
        Scope(),
        LateOnceSchedule(),
        misfire=policy,
    ).invoke()
    try:
        assert _wait_for(lambda: len(job.history()) == 1)
    finally:
        job.stop(drain=True, timeout=1.0)

    (record,) = job.history()
    assert record.status == ('skipped_misfire' if policy == 'skip' else 'succeeded')
    if policy != 'fire_now':
        key = 'misfire_drift_seconds' if policy == 'skip' else 'coalesced_drift_seconds'
        drift = record.metadata[key]
        assert isinstance(drift, float)
        assert drift >= _MISFIRE_GRACE_SECONDS
    assert calls == ([] if policy == 'skip' else [None])


@pytest.mark.parametrize('drain', [False, True])
def test_stop_preserves_or_cancels_admitted_queue_according_to_drain(*, drain: bool) -> None:
    """Stopping while one fire is queued must neither abandon nor execute it twice."""
    started = threading.Event()
    release = threading.Event()
    entered: list[str] = []

    def runner(record: RunRecord) -> None:
        entered.append(record.fire_id)
        started.set()
        assert release.wait(timeout=2)

    job = ScheduledJob(
        name='stop-admitted-queue',
        schedule=AtSchedule(datetime(2099, 1, 1, tzinfo=timezone.utc)),
        runner=runner,
        overlap_policy='queue',
        max_overlap=1,
    ).start()
    first = threading.Thread(target=job.trigger_now)
    second = threading.Thread(target=job.trigger_now)
    first.start()
    assert started.wait(timeout=1)
    second.start()
    # Observe admission before stopping; finished history cannot expose a queued fire.
    assert _wait_for(lambda: bool(job._queued_records))  # pyright: ignore[reportPrivateUsage]  # noqa: SLF001
    stopped = threading.Event()

    def stop() -> None:
        job.stop(drain=drain, timeout=2)
        stopped.set()

    stopper = threading.Thread(target=stop)
    stopper.start()
    try:
        assert _wait_for(lambda: not job.is_running)
        if not drain:
            assert stopped.wait(timeout=1)
        release.set()
        assert stopped.wait(timeout=2)
    finally:
        release.set()
        first.join(timeout=2)
        second.join(timeout=2)
        stopper.join(timeout=2)
        job.stop(timeout=2)
    assert not first.is_alive()
    assert not second.is_alive()
    assert not stopper.is_alive()
    assert len(entered) == (_EXPECTED_OVERLAP_FIRES if drain else 1)
    assert sorted(record.status for record in job.history()) == (
        ['succeeded', 'succeeded'] if drain else ['cancelled', 'succeeded']
    )


def test_replace_requests_cooperative_cancellation_without_concurrent_execution() -> None:
    """Replacement waits for synchronous work to finish while exposing its cancellation token."""
    first_started = threading.Event()
    first_finished = threading.Event()
    active = 0
    peak_active = 0
    state_lock = threading.Lock()

    def runner(record: RunRecord) -> None:
        nonlocal active, peak_active
        with state_lock:
            active += 1
            peak_active = max(peak_active, active)
        if not first_started.is_set():
            first_started.set()
            token = record.metadata['cancellation_event']
            assert isinstance(token, threading.Event)
            assert token.wait(timeout=1.0)
            first_finished.set()
        with state_lock:
            active -= 1

    job = ScheduledJob(
        name='cooperative-replacement',
        schedule=AtSchedule(datetime(2099, 1, 1, tzinfo=timezone.utc)),
        runner=runner,
        overlap_policy='replace',
        max_overlap=1,
    )
    first = threading.Thread(target=job.trigger_now)
    first.start()
    assert first_started.wait(timeout=1.0)
    second_result: list[RunRecord] = []
    second = threading.Thread(target=lambda: second_result.append(job.trigger_now()))
    second.start()
    assert first_finished.wait(timeout=1.0)
    first.join(timeout=1.0)
    second.join(timeout=1.0)

    assert len(second_result) == 1
    assert peak_active == 1
    history = job.history()
    assert history[0].metadata['cancellation_requested'] is True
    assert history[1].metadata['replaces_active_run'] is True
