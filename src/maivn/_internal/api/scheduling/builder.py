"""Builder returned by ``Agent.cron`` / ``Swarm.cron`` and the interval/at variants.

The builder mirrors the underlying scope's invocation surface (``invoke``,
``stream``, ``batch``, ``abatch``, ``ainvoke``, ``astream``). Each terminal
call schedules the job and returns a :class:`ScheduledJob` handle.
"""

# pyright: strict
from __future__ import annotations

import asyncio
import inspect
import threading
from collections.abc import AsyncIterable, Awaitable, Callable, Iterable
from datetime import datetime, timedelta, timezone
from typing import TYPE_CHECKING, Literal, TypeAlias, cast

from .jitter import JitterOffset, JitterSpec
from .job import ScheduledJob
from .models import RunRecord
from .retry import Retry
from .schedule import Schedule

if TYPE_CHECKING:  # pragma: no cover - typing only
    # NOTE: not importing concrete `BaseScope` from `..base_scope` to avoid the
    # TYPE_CHECKING mutual-reference cycle (`..base_scope.scheduling` imports
    # `CronInvocationBuilder` + `JitterInput` back from here). The structural
    # contract the builder needs from a scope is minimal — just enough to be
    # passed through to the runtime invocation paths. A formal Protocol /
    # generic-bound break is deferred to post-S11 polish.
    BaseScope = object  # noqa: N806  - typing alias for cycle-free annotation


# MARK: Types

MisfirePolicy: TypeAlias = Literal["skip", "fire_now", "coalesce"]
OverlapPolicy: TypeAlias = Literal["skip", "queue", "replace"]
InvocationMethod: TypeAlias = Literal["invoke", "stream", "batch", "abatch", "ainvoke", "astream"]
JitterInput: TypeAlias = JitterSpec | JitterOffset | tuple[JitterOffset, JitterOffset] | None
ScopeMethod: TypeAlias = Callable[..., object]
AsyncScopeMethod: TypeAlias = Callable[..., Awaitable[object]]
NotifyFire: TypeAlias = Callable[[RunRecord], Awaitable[None]]


# MARK: Builder


class CronInvocationBuilder:
    """Chainable builder produced by ``scope.cron(...)`` and friends."""

    def __init__(
        self,
        scope: BaseScope,
        schedule: Schedule,
        *,
        name: str | None = None,
        jitter: JitterInput = None,
        misfire: MisfirePolicy = "coalesce",
        max_overlap: int = 1,
        overlap_policy: OverlapPolicy = "skip",
        start_at: datetime | None = None,
        end_at: datetime | None = None,
        max_runs: int | None = None,
        retry: Retry | None = None,
        emit_events: bool = False,
    ) -> None:
        self._scope: BaseScope = scope
        self._schedule: Schedule = schedule
        self._name: str = (
            name
            or f"{type(scope).__name__}-{getattr(scope, 'name', None) or scope.__class__.__name__}"
        )
        self._jitter: JitterSpec | None = JitterSpec.from_value(jitter)
        self._misfire: MisfirePolicy = misfire
        self._max_overlap: int = max_overlap
        self._overlap_policy: OverlapPolicy = overlap_policy
        self._start_at: datetime | None = start_at
        self._end_at: datetime | None = end_at
        self._max_runs: int | None = max_runs
        self._retry: Retry = retry or Retry()
        self._emit_events: bool = emit_events

        self._inflight_count: int = 0
        self._inflight_lock: threading.Lock = threading.Lock()
        self._replace_token: int = 0

    # MARK: - Mutation helpers

    def with_scope(self, scope: BaseScope) -> CronInvocationBuilder:
        """Re-point the builder at a different scope before a terminal call.

        The runner captures ``self._scope`` when a terminal method
        (``invoke``/``stream``/...) builds the job, so this must be called
        before scheduling. Useful for wrapping the original scope (e.g. with
        ``events()``) while keeping any scope-derived configuration intact.
        """
        self._scope = scope
        return self

    def with_jitter(self, jitter: JitterInput) -> CronInvocationBuilder:
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

    def invoke(self, *args: object, **kwargs: object) -> ScheduledJob:
        return self._build_job(method="invoke", args=args, kwargs=kwargs)

    def stream(self, *args: object, **kwargs: object) -> ScheduledJob:
        return self._build_job(method="stream", args=args, kwargs=kwargs)

    def batch(self, inputs: Iterable[object], **kwargs: object) -> ScheduledJob:
        return self._build_job(method="batch", args=(list(inputs),), kwargs=kwargs)

    def abatch(self, inputs: Iterable[object], **kwargs: object) -> ScheduledJob:
        return self._build_job(method="abatch", args=(list(inputs),), kwargs=kwargs)

    def ainvoke(self, *args: object, **kwargs: object) -> ScheduledJob:
        return self._build_job(method="ainvoke", args=args, kwargs=kwargs)

    def astream(self, *args: object, **kwargs: object) -> ScheduledJob:
        return self._build_job(method="astream", args=args, kwargs=kwargs)

    # MARK: - Construction

    def _build_job(
        self, *, method: InvocationMethod, args: tuple[object, ...], kwargs: dict[str, object]
    ) -> ScheduledJob:
        if not hasattr(self._scope, method):
            raise AttributeError(f"Scope {type(self._scope).__name__} has no method {method!r}")

        job: ScheduledJob | None = None

        async def _notify_fire(record: RunRecord) -> None:
            if job is None:  # pragma: no cover - start happens after assignment below
                raise RuntimeError("Scheduled job is not initialized")
            await job.dispatch_fire(record)

        runner = self._make_runner(
            method=method,
            args=args,
            kwargs=kwargs,
            notify_fire=_notify_fire,
        )

        job = ScheduledJob(
            name=self._name,
            schedule=self._schedule,
            runner=runner,
            max_runs=self._max_runs,
            end_at=self._end_at,
            emit_events=self._emit_events,
        )

        from .registry import register_job

        register_job(job)
        _ = job.start()
        return job

    def _make_runner(
        self,
        *,
        method: InvocationMethod,
        args: tuple[object, ...],
        kwargs: dict[str, object],
        notify_fire: NotifyFire,
    ) -> Callable[[RunRecord], Awaitable[None]]:
        scope = self._scope
        jitter = self._jitter
        retry = self._retry
        overlap_policy = self._overlap_policy
        max_overlap = self._max_overlap
        misfire = self._misfire
        schedule = self._schedule

        async def _invoke_method() -> object:
            scope_method = cast(ScopeMethod, getattr(scope, method))
            if inspect.iscoroutinefunction(scope_method):
                async_method = cast(AsyncScopeMethod, scope_method)
                return await async_method(*args, **kwargs)
            if method == "stream":
                return await asyncio.to_thread(
                    lambda: list(cast(Iterable[object], scope_method(*args, **kwargs)))
                )
            if method == "astream":
                stream = cast(AsyncIterable[object], scope_method(*args, **kwargs))
                return [event async for event in stream]
            return await asyncio.to_thread(scope_method, *args, **kwargs)

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
                    except BaseException as exc:  # noqa: BLE001 - user retry policy owns run failures
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
        policy: str,
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
                replace_token = self._replace_token
            record.metadata["replaced_token"] = replace_token
            return True
        return False

    def _release_slot(self) -> None:
        with self._inflight_lock:
            if self._inflight_count > 0:
                self._inflight_count -= 1


# MARK: Exports

__all__ = ["CronInvocationBuilder", "MisfirePolicy", "OverlapPolicy"]
