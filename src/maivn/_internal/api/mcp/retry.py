from __future__ import annotations

import threading
import time
from collections import deque
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator


class _RateLimiter:
    """Simple sliding-window rate limiter."""

    def __init__(self, max_calls: int, window_seconds: float) -> None:
        self._max_calls = max_calls
        self._window_seconds = window_seconds
        self._min_interval_seconds = window_seconds / max_calls if max_calls > 0 else window_seconds
        self._lock = threading.Lock()
        self._timestamps: deque[float] = deque()
        self._next_allowed_at: float = 0.0

    def _compute_wait_seconds(self, now: float) -> float:
        wait_for = 0.0
        if now < self._next_allowed_at:
            wait_for = max(wait_for, self._next_allowed_at - now)
        while self._timestamps and (now - self._timestamps[0]) >= self._window_seconds:
            self._timestamps.popleft()

        if len(self._timestamps) >= self._max_calls:
            wait_for = max(wait_for, self._window_seconds - (now - self._timestamps[0]))

        return wait_for

    def peek_wait_seconds(self) -> float:
        with self._lock:
            return self._compute_wait_seconds(time.monotonic())

    def acquire(self) -> None:
        while True:
            with self._lock:
                now = time.monotonic()
                sleep_for = self._compute_wait_seconds(now)

                if sleep_for <= 0 and len(self._timestamps) < self._max_calls:
                    self._timestamps.append(now)
                    self._next_allowed_at = now + self._min_interval_seconds
                    return

            if sleep_for > 0:
                time.sleep(sleep_for)


class MCPSoftErrorHandling(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    enabled: bool = Field(default=False, description="Enable soft error detection")
    keys: list[str] = Field(
        default_factory=lambda: ["Note", "Information", "Error Message"],
        description="JSON keys that indicate a soft error when found in structured_content",
    )
    max_retries: int = Field(
        default=1,
        description="Number of additional attempts after detecting a soft error",
    )
    initial_backoff_seconds: float = Field(
        default=5.0,
        description="Initial delay before retrying after a soft error",
    )
    max_backoff_seconds: float = Field(
        default=60.0,
        description="Maximum delay between retries",
    )

    @field_validator("max_retries")
    @classmethod
    def _validate_max_retries(cls, value: int) -> int:
        if value < 0:
            raise ValueError("max_retries must be >= 0")
        return value

    @field_validator("initial_backoff_seconds", "max_backoff_seconds")
    @classmethod
    def _validate_backoff_seconds(cls, value: float) -> float:
        if value <= 0:
            raise ValueError("backoff seconds must be > 0")
        return value


def _find_soft_error_message(payload: Any, keys: set[str]) -> str | None:
    if isinstance(payload, dict):
        for key, value in payload.items():
            if key in keys and isinstance(value, str) and value.strip():
                return value.strip()
            nested = _find_soft_error_message(value, keys)
            if nested:
                return nested
        return None
    if isinstance(payload, list):
        for item in payload:
            nested = _find_soft_error_message(item, keys)
            if nested:
                return nested
        return None
    return None
