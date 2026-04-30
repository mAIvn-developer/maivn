"""Scheduling methods for BaseScope."""

from __future__ import annotations

from datetime import datetime, timedelta
from datetime import timezone as dt_timezone
from typing import TYPE_CHECKING, Any, Literal, cast

if TYPE_CHECKING:
    from ..scheduling.builder import CronInvocationBuilder


# MARK: Scheduling Mixin


class BaseScopeSchedulingMixin:
    def cron(
        self,
        expression: str,
        *,
        tz: str | dt_timezone | None = None,
        jitter: Any = None,
        name: str | None = None,
        misfire: Literal["skip", "fire_now", "coalesce"] = "coalesce",
        max_overlap: int = 1,
        overlap_policy: Literal["skip", "queue", "replace"] = "skip",
        start_at: datetime | None = None,
        end_at: datetime | None = None,
        max_runs: int | None = None,
        retry: Any = None,
        emit_events: bool = False,
    ) -> CronInvocationBuilder:
        """Build a scheduled invocation driven by a cron expression."""
        from ..scheduling.builder import CronInvocationBuilder
        from ..scheduling.schedule import CronSchedule

        return CronInvocationBuilder(
            cast(Any, self),
            CronSchedule(expression, tz=tz),
            name=name,
            jitter=jitter,
            misfire=misfire,
            max_overlap=max_overlap,
            overlap_policy=overlap_policy,
            start_at=start_at,
            end_at=end_at,
            max_runs=max_runs,
            retry=retry,
            emit_events=emit_events,
        )

    def every(
        self,
        interval: timedelta | float | int,
        *,
        tz: str | dt_timezone | None = None,
        start: datetime | None = None,
        jitter: Any = None,
        name: str | None = None,
        misfire: Literal["skip", "fire_now", "coalesce"] = "coalesce",
        max_overlap: int = 1,
        overlap_policy: Literal["skip", "queue", "replace"] = "skip",
        end_at: datetime | None = None,
        max_runs: int | None = None,
        retry: Any = None,
        emit_events: bool = False,
    ) -> CronInvocationBuilder:
        """Build a scheduled invocation that fires every ``interval``."""
        from ..scheduling.builder import CronInvocationBuilder
        from ..scheduling.schedule import IntervalSchedule

        if not isinstance(interval, timedelta):
            interval = timedelta(seconds=float(interval))
        return CronInvocationBuilder(
            cast(Any, self),
            IntervalSchedule(interval, start=start, tz=tz),
            name=name,
            jitter=jitter,
            misfire=misfire,
            max_overlap=max_overlap,
            overlap_policy=overlap_policy,
            end_at=end_at,
            max_runs=max_runs,
            retry=retry,
            emit_events=emit_events,
        )

    def at(
        self,
        when: datetime,
        *,
        tz: str | dt_timezone | None = None,
        jitter: Any = None,
        name: str | None = None,
        retry: Any = None,
        emit_events: bool = False,
    ) -> CronInvocationBuilder:
        """Build a one-shot scheduled invocation that fires at ``when``."""
        from ..scheduling.builder import CronInvocationBuilder
        from ..scheduling.schedule import AtSchedule

        return CronInvocationBuilder(
            cast(Any, self),
            AtSchedule(when, tz=tz),
            name=name,
            jitter=jitter,
            max_runs=1,
            retry=retry,
            emit_events=emit_events,
        )


__all__ = ["BaseScopeSchedulingMixin"]
