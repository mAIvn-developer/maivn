"""Builder returned by ``Agent.cron`` / ``Swarm.cron`` and the interval/at variants.

The builder mirrors the underlying scope's invocation surface (``invoke``,
``stream``, ``batch``, ``abatch``, ``ainvoke``, ``astream``). Each terminal
call schedules the job and returns a :class:`ScheduledJob` handle.
"""

from __future__ import annotations

import asyncio
import inspect
import threading
from collections.abc import Iterable
from datetime import datetime, timedelta, timezone
from typing import TYPE_CHECKING, Any, Literal

from .jitter import JitterSpec
from .job import ScheduledJob
from .models import RunRecord
from .retry import Retry
from .schedule import Schedule

if TYPE_CHECKING:  # pragma: no cover - typing only
    from ..base_scope import BaseScope


MisfirePolicy = Literal["skip", "fire_now", "coalesce"]
OverlapPolicy = Literal["skip", "queue", "replace"]


class CronInvocationBuilder:
    """Chainable builder produced by ``scope.cron(...)`` and friends."""

    def __init__(
        self,
        scope: BaseScope,
        schedule: Schedule,
        *,
        name: str | None = None,
        jitter: JitterSpec | timedelta | float | tuple | None = None,
        misfire: MisfirePolicy = "coalesce",
        max_overlap: int = 1,
        overlap_policy: OverlapPolicy = "skip",
        start_at: datetime | None = None,
        end_at: datetime | None = None,
        max_runs: int | None = None,
        retry: Retry | None = None,
        emit_events: bool = False,
    ) -> None:
        self._scope = scope
        self._schedule = schedule
        self._name = (
            name
            or f"{type(scope).__name__}-{getattr(scope, 'name', None) or scope.__class__.__name__}"
        )
        self._jitter = JitterSpec.from_value(jitter)
        self._misfire = misfire
        self._max_overlap = max_overlap
        self._overlap_policy: OverlapPolicy = overlap_policy
        self._start_at = start_at
        self._end_at = end_at
        self._max_runs = max_runs
        self._retry = retry or Retry()
        self._emit_events = emit_events

        self._inflight_count = 0
        self._inflight_lock = threading.Lock()
        self._queue_semaphore: asyncio.Semaphore | None = None
        self._replace_token = 0

    # MARK: - Mutation helpers

    def with_jitter(
        self, jitter: JitterSpec | timedelta | float | tuple | None
    ) -> CronInvocationBuilder:
        self._jitter = JitterSpec.from_value(jitter)
        return self

    def with_retry(self, retry: Retry) -> CronInvocationBuilder:
        self._retry = retry
        return self

    def with_overlap(self, policy: OverlapPolicy, *, max_overlap: int = 1) -> CronInvocationBuilder:
        self._overlap_policy = policy
        self._max_overlap = max_overlap
        return self

    def with_misfire(self, policy: MisfirePolicy) -> CronInvocationBuilder:
        self._misfire = policy
        return self

    def with_window(
        self, *, start_at: datetime | None = None, end_at: datetime | None = None
    ) -> CronInvocationBuilder:
        if start_at is not None:
            self._start_at = start_at
        if end_at is not None:
            self._end_at = end_at
        return self

    def with_max_runs(self, max_runs: int | None) -> CronInvocationBuilder:
        self._max_runs = max_runs
        return self

    def with_emit_events(self, emit: bool = True) -> CronInvocationBuilder:
        self._emit_events = emit
        return self

    # MARK: - Terminal methods

    def invoke(self, *args: Any, **kwargs: Any) -> ScheduledJob:
        return self._build_job(method="invoke", args=args, kwargs=kwargs)

    def stream(self, *args: Any, **kwargs: Any) -> ScheduledJob:
        return self._build_job(method="stream", args=args, kwargs=kwargs)

    def batch(self, inputs: Iterable[Any], **kwargs: Any) -> ScheduledJob:
        return self._build_job(method="batch", args=(list(inputs),), kwargs=kwargs)

    def abatch(self, inputs: Iterable[Any], **kwargs: Any) -> ScheduledJob:
        return self._build_job(method="abatch", args=(list(inputs),), kwargs=kwargs)

    def ainvoke(self, *args: Any, **kwargs: Any) -> ScheduledJob:
        return self._build_job(method="ainvoke", args=args, kwargs=kwargs)

    def astream(self, *args: Any, **kwargs: Any) -> ScheduledJob:
        return self._build_job(method="astream", args=args, kwargs=kwargs)

    # MARK: - Construction

    def _build_job(
        self, *, method: str, args: tuple[Any, ...], kwargs: dict[str, Any]
    ) -> ScheduledJob:
        if not hasattr(self._scope, method):
            raise AttributeError(f"Scope {type(self._scope).__name__} has no method {method!r}")

        async def _placeholder(record: RunRecord) -> None:  # pragma: no cover
            return None

        job = ScheduledJob(
            name=self._name,
            schedule=self._schedule,
            runner=_placeholder,
            max_runs=self._max_runs,
            end_at=self._end_at,
            emit_events=self._emit_events,
        )
        job._runner = self._make_runner(
            method=method,
            args=args,
            kwargs=kwargs,
            notify_fire=job.dispatch_fire,
        )

        from .registry import register_job

        register_job(job)
        job.start()
        return job

    def _make_runner(
        self,
        *,
        method: str,
        args: tuple[Any, ...],
        kwargs: dict[str, Any],
        notify_fire: Any,
    ):
        scope = self._scope
        jitter = self._jitter
        retry = self._retry
        overlap_policy = self._overlap_policy
        max_overlap = self._max_overlap
        misfire = self._misfire
        schedule = self._schedule

        async def _invoke_method() -> Any:
            target = getattr(scope, method)
            if inspect.iscoroutinefunction(target):
                return await target(*args, **kwargs)
            if method == "stream":
                return await asyncio.to_thread(lambda: list(target(*args, **kwargs)))
            if method == "astream":
                return [event async for event in target(*args, **kwargs)]
            return await asyncio.to_thread(target, *args, **kwargs)

        async def _runner(record: RunRecord) -> None:
            now = datetime.now(tz=timezone.utc)
            if not record.metadata.get("manual"):
                drift = now - record.scheduled_at
                misfire_grace = timedelta(seconds=30)
                if drift > misfire_grace:
                    if misfire == "skip":
                        record.status = "skipped_misfire"
                        record.metadata["misfire_drift_seconds"] = drift.total_seconds()
                        return
                    if misfire == "coalesce":
                        # Drop oldest missed fires; treat as a single run now.
                        record.metadata["coalesced_drift_seconds"] = drift.total_seconds()

                if jitter is not None:
                    next_scheduled = schedule.next_after(record.scheduled_at)
                    fire_at, offset, skipped = jitter.apply(record.scheduled_at, next_scheduled)
                    record.jitter_offset = offset
                    if skipped:
                        record.status = "skipped_jitter"
                        return
                    delay = max(0.0, (fire_at - datetime.now(tz=timezone.utc)).total_seconds())
                    if delay > 0:
                        await asyncio.sleep(delay)

            if not await self._claim_slot(overlap_policy, max_overlap, record):
                return

            try:
                record.fired_at = datetime.now(tz=timezone.utc)
                await notify_fire(record)
                attempt = 1
                last_exc: BaseException | None = None
                while True:
                    record.attempt = attempt
                    try:
                        result = await _invoke_method()
                    except BaseException as exc:  # noqa: BLE001
                        last_exc = exc
                        if not retry.should_retry(exc, attempt):
                            record.status = "failed"
                            record.error = exc
                            break
                        delay = retry.delay_for_attempt(attempt + 1).total_seconds()
                        attempt += 1
                        if delay > 0:
                            await asyncio.sleep(delay)
                        continue
                    record.status = "succeeded"
                    record.result = result
                    last_exc = None
                    break
                record.finished_at = datetime.now(tz=timezone.utc)
                if last_exc is not None and record.status != "succeeded":
                    raise last_exc
            finally:
                self._release_slot()

        return _runner

    # MARK: - Overlap handling

    async def _claim_slot(
        self,
        policy: OverlapPolicy,
        max_overlap: int,
        record: RunRecord,
    ) -> bool:
        if max_overlap <= 0:
            with self._inflight_lock:
                self._inflight_count += 1
            return True

        with self._inflight_lock:
            if self._inflight_count < max_overlap:
                self._inflight_count += 1
                return True

        if policy == "skip":
            record.status = "skipped_overlap"
            return False
        if policy == "queue":
            while True:
                await asyncio.sleep(0.05)
                with self._inflight_lock:
                    if self._inflight_count < max_overlap:
                        self._inflight_count += 1
                        return True
        if policy == "replace":
            with self._inflight_lock:
                self._inflight_count += 1
            self._replace_token += 1
            record.metadata["replaced_token"] = self._replace_token
            return True
        return False

    def _release_slot(self) -> None:
        with self._inflight_lock:
            if self._inflight_count > 0:
                self._inflight_count -= 1


__all__ = ["CronInvocationBuilder", "MisfirePolicy", "OverlapPolicy"]
