"""Unit tests for :class:`CronInvocationBuilder` mutation helpers.

Covers the public ``with_scope`` re-point API: the runner built by a terminal
call must invoke the scope set via ``with_scope`` rather than the one passed to
the constructor.
"""

# pyright: strict
from __future__ import annotations

import asyncio
import threading
from datetime import datetime, timedelta, timezone

import pytest

from maivn._internal.api.scheduling.builder import CronInvocationBuilder
from maivn._internal.api.scheduling.schedule import IntervalSchedule


class _StubScope:
    """Minimal scope-like object recording the ``invoke`` calls it receives."""

    def __init__(self, label: str) -> None:
        self.label: str = label
        self.calls: list[tuple[object, ...]] = []
        self.lock: threading.Lock = threading.Lock()

    def invoke(self, *args: object, **kwargs: object) -> dict[str, object]:
        with self.lock:
            self.calls.append(args)
        return {"label": self.label}


def test_with_scope_returns_self_for_chaining() -> None:
    original = _StubScope("original")
    replacement = _StubScope("replacement")
    builder = CronInvocationBuilder(
        original,
        IntervalSchedule(timedelta(milliseconds=1), start=datetime.now(tz=timezone.utc)),
    )

    result = builder.with_scope(replacement)

    assert result is builder
    assert builder._scope is replacement  # pyright: ignore[reportPrivateUsage]


@pytest.mark.asyncio
async def test_with_scope_repoints_runner_target() -> None:
    original = _StubScope("original")
    replacement = _StubScope("replacement")
    builder = CronInvocationBuilder(
        original,
        IntervalSchedule(timedelta(milliseconds=1), start=datetime.now(tz=timezone.utc)),
        max_runs=1,
    )

    _ = builder.with_scope(replacement)
    job = builder.invoke("payload")

    deadline = asyncio.get_running_loop().time() + 2.0
    while asyncio.get_running_loop().time() < deadline:
        with replacement.lock:
            if replacement.calls:
                break
        await asyncio.sleep(0.02)
    job.stop(drain=True, timeout=2)

    # The runner fired against the re-pointed scope, never the constructor one.
    assert replacement.calls == [("payload",)]
    assert original.calls == []
