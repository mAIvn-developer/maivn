from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from maivn import AtSchedule, CronSchedule, IntervalSchedule


def test_cron_schedule_yields_expected_minutes() -> None:
    sch = CronSchedule("*/5 * * * *", tz=timezone.utc)
    cursor = datetime(2026, 1, 1, 0, 1, tzinfo=timezone.utc)
    upcoming = sch.upcoming(3, after=cursor)
    assert [u.minute for u in upcoming] == [5, 10, 15]


def test_invalid_cron_expression_rejected() -> None:
    with pytest.raises(ValueError):
        CronSchedule("not a cron")


def test_interval_schedule_aligns_to_start() -> None:
    start = datetime(2026, 1, 1, 0, 0, tzinfo=timezone.utc)
    sch = IntervalSchedule(timedelta(seconds=30), start=start)
    cursor = datetime(2026, 1, 1, 0, 1, tzinfo=timezone.utc)
    upcoming = sch.upcoming(2, after=cursor)
    assert upcoming[0] == datetime(2026, 1, 1, 0, 1, 30, tzinfo=timezone.utc)
    assert upcoming[1] == datetime(2026, 1, 1, 0, 2, 0, tzinfo=timezone.utc)


def test_interval_must_be_positive() -> None:
    with pytest.raises(ValueError):
        IntervalSchedule(timedelta(0))


def test_at_schedule_returns_when_then_none() -> None:
    when = datetime(2026, 1, 1, 0, 0, tzinfo=timezone.utc)
    sch = AtSchedule(when)
    before = when - timedelta(seconds=1)
    assert sch.next_after(before) == when
    assert sch.next_after(when) is None
