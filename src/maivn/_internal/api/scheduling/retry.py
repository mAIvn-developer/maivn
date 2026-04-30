"""Retry policy for scheduled invocations."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta
from typing import Literal

RetryBackoff = Literal["constant", "linear", "exponential"]


@dataclass(frozen=True)
class Retry:
    """Retry policy applied to each scheduled fire.

    ``max_attempts`` includes the initial attempt: ``max_attempts=1`` disables
    retries.
    """

    max_attempts: int = 1
    backoff: RetryBackoff = "constant"
    base: timedelta = timedelta(seconds=5)
    factor: float = 2.0
    max_delay: timedelta | None = timedelta(minutes=10)
    retry_on: tuple[type[BaseException], ...] = (Exception,)

    def __post_init__(self) -> None:
        if self.max_attempts < 1:
            raise ValueError("Retry.max_attempts must be >= 1")
        if self.factor <= 0:
            raise ValueError("Retry.factor must be > 0")
        if self.base < timedelta(0):
            raise ValueError("Retry.base must be non-negative")

    def delay_for_attempt(self, attempt: int) -> timedelta:
        """Return the delay before ``attempt`` (1-indexed)."""
        if attempt <= 1:
            return timedelta(0)
        if self.backoff == "constant":
            delay = self.base
        elif self.backoff == "linear":
            delay = self.base * (attempt - 1)
        else:
            delay = self.base * (self.factor ** (attempt - 2))
        if self.max_delay is not None and delay > self.max_delay:
            delay = self.max_delay
        return delay

    def should_retry(self, exc: BaseException, attempt: int) -> bool:
        if attempt >= self.max_attempts:
            return False
        return isinstance(exc, self.retry_on)


__all__ = ["Retry", "RetryBackoff"]
