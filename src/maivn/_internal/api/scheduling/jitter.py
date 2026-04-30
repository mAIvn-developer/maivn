"""Jitter specification for scheduled invocations.

Provides bounded randomness around a scheduled fire time so launches feel
natural rather than perfectly periodic. Distributions and bounds are explicit
to keep behaviour predictable; a deterministic seed is supported for tests.
"""

from __future__ import annotations

import random
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Literal

JitterDistribution = Literal["uniform", "normal", "triangular"]


_ZERO = timedelta(0)


def _coerce_offset(value: timedelta | float | int) -> timedelta:
    """Accept either a timedelta or a number of seconds."""
    if isinstance(value, timedelta):
        return value
    return timedelta(seconds=float(value))


@dataclass(frozen=True)
class JitterSpec:
    """Bounded jitter applied to a scheduled fire time.

    Parameters
    ----------
    min, max:
        Inclusive offset bounds. May be negative (fire earlier) or positive
        (fire later). Asymmetric ranges are supported.
    distribution:
        Sampling distribution. ``"uniform"`` is the default; ``"normal"`` uses
        ``sigma`` and clamps to ``[min, max]``; ``"triangular"`` peaks at the
        midpoint between ``min`` and ``max``.
    sigma:
        Standard deviation for the normal distribution. Ignored for other
        distributions. Defaults to 1/3 of the half-range so ~99.7% of samples
        land inside ``[min, max]`` before clamping.
    align_to:
        Optional snap-to-grid applied after sampling (for example a 15-second
        grid yields more "human" looking timestamps). ``None`` disables it.
    skip_if_overruns_next:
        If True, when a sampled offset would push the fire time past the next
        scheduled time the run is skipped instead of compressing the gap.
    seed:
        Optional deterministic seed for reproducible tests. Each spec owns a
        private :class:`random.Random` instance when seeded.
    """

    min: timedelta = _ZERO
    max: timedelta = _ZERO
    distribution: JitterDistribution = "uniform"
    sigma: timedelta | None = None
    align_to: timedelta | None = None
    skip_if_overruns_next: bool = True
    seed: int | None = None

    _rng: random.Random = field(init=False, repr=False, compare=False)

    def __post_init__(self) -> None:
        if self.min > self.max:
            raise ValueError("JitterSpec.min must be <= JitterSpec.max")
        if self.align_to is not None and self.align_to <= _ZERO:
            raise ValueError("JitterSpec.align_to must be a positive timedelta")
        rng = random.Random(self.seed) if self.seed is not None else random.Random()
        object.__setattr__(self, "_rng", rng)

    # MARK: - Construction helpers

    @classmethod
    def symmetric(
        cls,
        amount: timedelta | float | int,
        *,
        distribution: JitterDistribution = "uniform",
        seed: int | None = None,
    ) -> JitterSpec:
        """Build a symmetric range of ``[-amount, +amount]``."""
        delta = _coerce_offset(amount)
        return cls(min=-delta, max=delta, distribution=distribution, seed=seed)

    @classmethod
    def from_value(
        cls, value: JitterSpec | timedelta | float | int | tuple | None
    ) -> JitterSpec | None:
        """Coerce shorthand inputs into a :class:`JitterSpec`.

        Accepts ``None``, a :class:`JitterSpec`, a positive ``timedelta``/number
        (interpreted as symmetric), or a ``(min, max)`` tuple.
        """
        if value is None:
            return None
        if isinstance(value, JitterSpec):
            return value
        if isinstance(value, tuple) and len(value) == 2:
            return cls(min=_coerce_offset(value[0]), max=_coerce_offset(value[1]))
        if isinstance(value, (timedelta, int, float)):
            return cls.symmetric(value)
        raise TypeError(f"Unsupported jitter shorthand: {value!r}")

    # MARK: - Sampling

    def sample(self) -> timedelta:
        """Draw a single offset within ``[min, max]``."""
        if self.min == self.max:
            offset = self.min
        elif self.distribution == "uniform":
            low = self.min.total_seconds()
            high = self.max.total_seconds()
            offset = timedelta(seconds=self._rng.uniform(low, high))
        elif self.distribution == "triangular":
            low = self.min.total_seconds()
            high = self.max.total_seconds()
            mid = (low + high) / 2.0
            offset = timedelta(seconds=self._rng.triangular(low, high, mid))
        elif self.distribution == "normal":
            half_range = (self.max - self.min) / 2
            mid = self.min + half_range
            sigma = (
                self.sigma
                if self.sigma is not None
                else (half_range / 3 if half_range > _ZERO else _ZERO)
            )
            sampled = self._rng.gauss(mid.total_seconds(), max(sigma.total_seconds(), 0.0))
            offset = timedelta(seconds=sampled)
            if offset < self.min:
                offset = self.min
            elif offset > self.max:
                offset = self.max
        else:
            raise ValueError(f"Unknown jitter distribution: {self.distribution}")

        if self.align_to is not None and self.align_to > _ZERO:
            grid = self.align_to.total_seconds()
            seconds = offset.total_seconds()
            offset = timedelta(seconds=round(seconds / grid) * grid)
            if offset < self.min:
                offset = self.min
            elif offset > self.max:
                offset = self.max
        return offset

    def apply(
        self, scheduled_at: datetime, next_scheduled_at: datetime | None = None
    ) -> tuple[datetime, timedelta, bool]:
        """Apply jitter to ``scheduled_at``.

        Returns a tuple ``(fire_at, offset, skipped)``. When ``skipped`` is
        True the caller should drop the run rather than fire at ``fire_at``.
        """
        offset = self.sample()
        fire_at = scheduled_at + offset
        skipped = False
        if (
            self.skip_if_overruns_next
            and next_scheduled_at is not None
            and fire_at >= next_scheduled_at
        ):
            skipped = True
        return fire_at, offset, skipped


__all__ = ["JitterDistribution", "JitterSpec"]
