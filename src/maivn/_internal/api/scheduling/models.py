"""Public dataclasses for scheduled invocation."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any, Literal

RunStatus = Literal[
    "pending",
    "running",
    "succeeded",
    "failed",
    "skipped_misfire",
    "skipped_jitter",
    "skipped_overlap",
    "cancelled",
]


@dataclass
class RunRecord:
    """Outcome of a single scheduled fire."""

    scheduled_at: datetime
    fire_id: str
    fired_at: datetime | None = None
    finished_at: datetime | None = None
    jitter_offset: timedelta = timedelta(0)
    attempt: int = 1
    status: RunStatus = "pending"
    result: Any = None
    error: BaseException | None = None
    metadata: dict[str, Any] = field(default_factory=dict)

    @property
    def duration(self) -> timedelta | None:
        if self.fired_at is None or self.finished_at is None:
            return None
        return self.finished_at - self.fired_at


__all__ = ["RunRecord", "RunStatus"]
