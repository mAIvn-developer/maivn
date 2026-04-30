from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from maivn import JitterSpec


def test_zero_range_returns_zero_offset() -> None:
    spec = JitterSpec()
    assert spec.sample() == timedelta(0)


def test_symmetric_shorthand_builds_signed_range() -> None:
    spec = JitterSpec.symmetric(timedelta(seconds=30))
    assert spec.min == timedelta(seconds=-30)
    assert spec.max == timedelta(seconds=30)


def test_seeded_uniform_is_deterministic() -> None:
    a = JitterSpec(min=timedelta(0), max=timedelta(seconds=60), seed=42)
    b = JitterSpec(min=timedelta(0), max=timedelta(seconds=60), seed=42)
    assert [a.sample() for _ in range(5)] == [b.sample() for _ in range(5)]


def test_normal_distribution_clamps_to_range() -> None:
    spec = JitterSpec(
        min=timedelta(seconds=0),
        max=timedelta(seconds=10),
        distribution="normal",
        sigma=timedelta(seconds=100),
        seed=7,
    )
    for _ in range(50):
        sample = spec.sample()
        assert spec.min <= sample <= spec.max


def test_align_to_snaps_to_grid() -> None:
    spec = JitterSpec(
        min=timedelta(seconds=0),
        max=timedelta(seconds=60),
        align_to=timedelta(seconds=15),
        seed=1,
    )
    for _ in range(20):
        sample = spec.sample()
        assert sample.total_seconds() % 15 == 0


def test_apply_skips_when_overrunning_next_run() -> None:
    spec = JitterSpec(
        min=timedelta(seconds=0),
        max=timedelta(seconds=120),
        skip_if_overruns_next=True,
        seed=2,
    )
    scheduled = datetime(2026, 1, 1, tzinfo=timezone.utc)
    next_run = scheduled + timedelta(seconds=30)
    saw_skip = False
    for _ in range(50):
        _, offset, skipped = spec.apply(scheduled, next_run)
        if skipped:
            saw_skip = True
            assert offset >= timedelta(seconds=30)
    assert saw_skip


def test_min_greater_than_max_rejected() -> None:
    with pytest.raises(ValueError):
        JitterSpec(min=timedelta(seconds=10), max=timedelta(seconds=0))


def test_from_value_shorthand() -> None:
    assert JitterSpec.from_value(None) is None
    spec = JitterSpec.from_value(timedelta(seconds=10))
    assert spec is not None and spec.min == timedelta(seconds=-10)
    pair = JitterSpec.from_value((timedelta(seconds=0), timedelta(seconds=5)))
    assert pair is not None and pair.min == timedelta(0) and pair.max == timedelta(seconds=5)
