"""Schedule iterators for cron, interval, and one-shot triggers."""

# pyright: strict
from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Callable
from datetime import datetime, timedelta, timezone, tzinfo
from typing import Protocol, cast
from zoneinfo import ZoneInfo

from typing_extensions import override

# MARK: Types


class _CronIterator(Protocol):
    def get_next(self, ret_type: type[datetime]) -> datetime: ...


class _CroniterClass(Protocol):
    is_valid: Callable[[str], bool]

    def __call__(self, expression: str, start_time: datetime) -> _CronIterator: ...


# MARK: Timezones


def resolve_timezone(tz: str | timezone | None) -> tzinfo:
    """Resolve ``tz`` shorthand into a tzinfo-compatible value."""
    if tz is None:
        return timezone.utc
    if isinstance(tz, timezone):
        return tz
    return ZoneInfo(tz)


# MARK: - Schedule protocol


class Schedule(ABC):
    """Iterates the sequence of scheduled fire times."""

    tz: tzinfo

    @abstractmethod
    def next_after(self, after: datetime) -> datetime | None:
        """Return the next scheduled time strictly greater than ``after``."""

    def upcoming(self, count: int, *, after: datetime | None = None) -> list[datetime]:
        """Return up to ``count`` upcoming scheduled times."""
        cursor = after if after is not None else datetime.now(tz=self.tz)
        out: list[datetime] = []
        for _ in range(count):
            nxt = self.next_after(cursor)
            if nxt is None:
                break
            out.append(nxt)
            cursor = nxt
        return out


# MARK: - Cron


class CronSchedule(Schedule):
    """Cron expression evaluated by croniter."""

    def __init__(self, expression: str, tz: str | timezone | None = None) -> None:
        from croniter import croniter

        croniter_cls = cast(_CroniterClass, cast(object, croniter))
        self._croniter_cls: _CroniterClass = croniter_cls
        if not croniter_cls.is_valid(expression):
            raise ValueError(f"Invalid cron expression: {expression!r}")
        self.expression: str = expression
        self.tz: tzinfo = resolve_timezone(tz)

    @override
    def next_after(self, after: datetime) -> datetime | None:
        if after.tzinfo is None:
            after = after.replace(tzinfo=self.tz)
        else:
            after = after.astimezone(self.tz)
        itr = self._croniter_cls(self.expression, after)
        return itr.get_next(datetime)


# MARK: - Interval


class IntervalSchedule(Schedule):
    """Fixed interval starting at the first whole multiple after ``start``."""

    def __init__(
        self,
        interval: timedelta,
        *,
        start: datetime | None = None,
        tz: str | timezone | None = None,
    ) -> None:
        if interval <= timedelta(0):
            raise ValueError("IntervalSchedule.interval must be positive")
        self.interval: timedelta = interval
        self.tz: tzinfo = resolve_timezone(tz)
        if start is None:
            start = datetime.now(tz=self.tz)
        elif start.tzinfo is None:
            start = start.replace(tzinfo=self.tz)
        else:
            start = start.astimezone(self.tz)
        self.start: datetime = start

    @override
    def next_after(self, after: datetime) -> datetime | None:
        if after.tzinfo is None:
            after = after.replace(tzinfo=self.tz)
        else:
            after = after.astimezone(self.tz)
        if after < self.start:
            return self.start
        elapsed = after - self.start
        steps = int(elapsed.total_seconds() // self.interval.total_seconds()) + 1
        return self.start + steps * self.interval


# MARK: - One-shot


class AtSchedule(Schedule):
    """Single scheduled fire time."""

    def __init__(self, when: datetime, *, tz: str | timezone | None = None) -> None:
        self.tz: tzinfo = resolve_timezone(tz)
        if when.tzinfo is None:
            when = when.replace(tzinfo=self.tz)
        else:
            when = when.astimezone(self.tz)
        self.when: datetime = when

    @override
    def next_after(self, after: datetime) -> datetime | None:
        if after.tzinfo is None:
            after = after.replace(tzinfo=self.tz)
        else:
            after = after.astimezone(self.tz)
        if after >= self.when:
            return None
        return self.when


# MARK: Exports

__all__ = [
    "AtSchedule",
    "CronSchedule",
    "IntervalSchedule",
    "Schedule",
    "resolve_timezone",
]
