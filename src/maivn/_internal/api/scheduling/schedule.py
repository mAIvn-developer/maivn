"""Schedule iterators for cron, interval, and one-shot triggers."""

from __future__ import annotations

from abc import ABC, abstractmethod
from datetime import datetime, timedelta, timezone
from typing import TYPE_CHECKING

if TYPE_CHECKING:  # pragma: no cover - typing only
    pass

try:
    from zoneinfo import ZoneInfo as _ZoneInfo
except ImportError:  # pragma: no cover - py<3.9 fallback (unsupported)
    _ZoneInfo = None  # type: ignore[assignment]


def resolve_timezone(tz: str | timezone | None) -> timezone:
    """Resolve ``tz`` shorthand into a tzinfo-compatible value."""
    if tz is None:
        return timezone.utc
    if isinstance(tz, timezone):
        return tz
    if _ZoneInfo is None:
        raise RuntimeError("zoneinfo is unavailable; pass a datetime.timezone instance")
    return _ZoneInfo(tz)  # type: ignore[return-value]


# MARK: - Schedule protocol


class Schedule(ABC):
    """Iterates the sequence of scheduled fire times."""

    tz: timezone

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

        self._croniter_cls = croniter
        if not croniter.is_valid(expression):
            raise ValueError(f"Invalid cron expression: {expression!r}")
        self.expression = expression
        self.tz = resolve_timezone(tz)

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
        self.interval = interval
        self.tz = resolve_timezone(tz)
        if start is None:
            start = datetime.now(tz=self.tz)
        elif start.tzinfo is None:
            start = start.replace(tzinfo=self.tz)
        else:
            start = start.astimezone(self.tz)
        self.start = start

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
        self.tz = resolve_timezone(tz)
        if when.tzinfo is None:
            when = when.replace(tzinfo=self.tz)
        else:
            when = when.astimezone(self.tz)
        self.when = when

    def next_after(self, after: datetime) -> datetime | None:
        if after.tzinfo is None:
            after = after.replace(tzinfo=self.tz)
        else:
            after = after.astimezone(self.tz)
        if after >= self.when:
            return None
        return self.when


__all__ = [
    "AtSchedule",
    "CronSchedule",
    "IntervalSchedule",
    "Schedule",
    "resolve_timezone",
]
