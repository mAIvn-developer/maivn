"""End-to-end behaviour of ``ScheduledJob`` driven by the in-process backend.

Uses a lightweight stub scope that exposes ``invoke``/``ainvoke`` so the
scheduling pipeline (jitter, retry, overlap policy, callbacks) can be
exercised without spinning up the real Agent runtime.
"""

from __future__ import annotations

import asyncio
import threading
from datetime import datetime, timedelta, timezone

import pytest

from maivn._internal.api.scheduling.builder import CronInvocationBuilder
from maivn._internal.api.scheduling.jitter import JitterSpec
from maivn._internal.api.scheduling.retry import Retry
from maivn._internal.api.scheduling.schedule import IntervalSchedule


class _StubScope:
    """Minimal scope-like object exposing the surface the builder needs."""

    def __init__(self) -> None:
        self.calls: list[tuple] = []
        self.fail_count = 0
        self.lock = threading.Lock()

    def invoke(self, *args, **kwargs):
        with self.lock:
            self.calls.append(("invoke", args, kwargs))
            if self.fail_count > 0:
                self.fail_count -= 1
                raise RuntimeError("transient")
        return {"status": "ok", "n": len(self.calls)}

    async def ainvoke(self, *args, **kwargs):
        return self.invoke(*args, **kwargs)


async def _wait_for_call_count(
    scope: _StubScope,
    expected_count: int,
    *,
    timeout_seconds: float,
) -> None:
    loop = asyncio.get_running_loop()
    deadline = loop.time() + timeout_seconds
    while True:
        with scope.lock:
            call_count = len(scope.calls)
        if call_count >= expected_count or loop.time() >= deadline:
            return
        await asyncio.sleep(0.02)


@pytest.mark.asyncio
async def test_runs_fire_and_callbacks_invoked() -> None:
    scope = _StubScope()
    builder = CronInvocationBuilder(
        scope,  # type: ignore[arg-type]
        IntervalSchedule(timedelta(milliseconds=1), start=datetime.now(tz=timezone.utc)),
        max_runs=3,
        max_overlap=3,
    )
    fire_records: list = []
    success_records: list = []
    error_records: list = []

    job = builder.invoke("hello")
    job.on_fire(lambda r: fire_records.append(r))
    job.on_success(lambda r: success_records.append(r))
    job.on_error(lambda r: error_records.append(r))

    await _wait_for_call_count(scope, 3, timeout_seconds=2.0)
    job.stop(drain=True, timeout=2)

    assert len(scope.calls) >= 3
    assert all(r.status == "succeeded" for r in success_records[:3])
    assert error_records == []
    assert len(fire_records) >= 3
    assert all(r.fired_at is not None for r in fire_records)


@pytest.mark.asyncio
async def test_retry_recovers_from_transient_failure() -> None:
    scope = _StubScope()
    scope.fail_count = 2
    builder = CronInvocationBuilder(
        scope,  # type: ignore[arg-type]
        IntervalSchedule(timedelta(milliseconds=200), start=datetime.now(tz=timezone.utc)),
        max_runs=1,
        retry=Retry(max_attempts=3, backoff="constant", base=timedelta(milliseconds=20)),
    )

    job = builder.invoke()
    await asyncio.sleep(0.8)
    job.stop(drain=True, timeout=2)

    assert job.success_count == 1
    last = job.last_run
    assert last is not None
    assert last.status == "succeeded"
    assert last.attempt == 3


@pytest.mark.asyncio
async def test_jitter_offset_is_recorded() -> None:
    scope = _StubScope()
    jitter = JitterSpec(min=timedelta(0), max=timedelta(milliseconds=50), seed=11)
    builder = CronInvocationBuilder(
        scope,  # type: ignore[arg-type]
        IntervalSchedule(timedelta(milliseconds=150), start=datetime.now(tz=timezone.utc)),
        jitter=jitter,
        max_runs=2,
    )
    job = builder.invoke()
    await asyncio.sleep(0.7)
    job.stop(drain=True, timeout=2)

    assert all(r.jitter_offset >= timedelta(0) for r in job.history())
    assert any(r.jitter_offset > timedelta(0) for r in job.history())


@pytest.mark.asyncio
async def test_pause_resume_and_stop_drain() -> None:
    scope = _StubScope()
    builder = CronInvocationBuilder(
        scope,  # type: ignore[arg-type]
        IntervalSchedule(timedelta(milliseconds=80), start=datetime.now(tz=timezone.utc)),
        max_runs=10,
    )
    job = builder.invoke()
    await asyncio.sleep(0.25)
    job.pause()
    # Let any fire that was already in-flight when pause was called finish so
    # the post-pause snapshot reflects only steady-state paused behaviour.
    await asyncio.sleep(0.15)
    paused_count = len(scope.calls)
    await asyncio.sleep(0.25)
    assert len(scope.calls) == paused_count
    job.resume()
    await asyncio.sleep(0.3)
    assert len(scope.calls) > paused_count
    job.stop(drain=True, timeout=2)
    assert job.is_done


@pytest.mark.asyncio
async def test_max_runs_caps_executions() -> None:
    scope = _StubScope()
    builder = CronInvocationBuilder(
        scope,  # type: ignore[arg-type]
        IntervalSchedule(timedelta(milliseconds=80), start=datetime.now(tz=timezone.utc)),
        max_runs=2,
    )
    job = builder.invoke()
    await asyncio.sleep(0.6)
    job.stop(drain=True, timeout=2)
    assert job.fire_count == 2
    assert len(scope.calls) == 2
