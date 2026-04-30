from __future__ import annotations

from datetime import timedelta

import pytest

from maivn import Retry


def test_constant_backoff() -> None:
    policy = Retry(max_attempts=4, backoff="constant", base=timedelta(seconds=2))
    assert policy.delay_for_attempt(1) == timedelta(0)
    assert policy.delay_for_attempt(2) == timedelta(seconds=2)
    assert policy.delay_for_attempt(4) == timedelta(seconds=2)


def test_linear_backoff() -> None:
    policy = Retry(max_attempts=5, backoff="linear", base=timedelta(seconds=3))
    assert policy.delay_for_attempt(2) == timedelta(seconds=3)
    assert policy.delay_for_attempt(3) == timedelta(seconds=6)
    assert policy.delay_for_attempt(4) == timedelta(seconds=9)


def test_exponential_backoff_with_cap() -> None:
    policy = Retry(
        max_attempts=10,
        backoff="exponential",
        base=timedelta(seconds=1),
        factor=2.0,
        max_delay=timedelta(seconds=8),
    )
    assert policy.delay_for_attempt(2) == timedelta(seconds=1)
    assert policy.delay_for_attempt(3) == timedelta(seconds=2)
    assert policy.delay_for_attempt(4) == timedelta(seconds=4)
    assert policy.delay_for_attempt(5) == timedelta(seconds=8)
    assert policy.delay_for_attempt(6) == timedelta(seconds=8)


def test_should_retry_respects_max_attempts() -> None:
    policy = Retry(max_attempts=2)
    assert policy.should_retry(RuntimeError("boom"), attempt=1)
    assert not policy.should_retry(RuntimeError("boom"), attempt=2)


def test_retry_on_filters_exceptions() -> None:
    policy = Retry(max_attempts=3, retry_on=(ValueError,))
    assert policy.should_retry(ValueError("x"), 1)
    assert not policy.should_retry(RuntimeError("x"), 1)


def test_max_attempts_must_be_positive() -> None:
    with pytest.raises(ValueError):
        Retry(max_attempts=0)
