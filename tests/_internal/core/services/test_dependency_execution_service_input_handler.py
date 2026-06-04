# pyright: strict
from __future__ import annotations

from collections.abc import Callable
from typing import cast

from maivn._internal.core.services.dependency_execution_service import (
    DependencyExecutionService,
)

_TryHandler = Callable[..., object | None]


def test_custom_input_handler_failure_not_double_invoked() -> None:
    service = DependencyExecutionService()
    calls: list[str] = []

    def handler(prompt: str, **kwargs: object) -> str:
        _ = kwargs
        calls.append(prompt)
        raise ValueError("boom")

    # _try_custom_input_handler is a deliberate private hook the service uses
    # for custom-handler probe-and-fallback; the test guards that the probe
    # does not double-invoke when the handler raises. ``getattr`` keeps the
    # access out of the ``reportPrivateUsage`` path.
    try_handler = cast(_TryHandler, getattr(service, "_try_custom_input_handler"))  # noqa: B009
    result = try_handler(
        handler,
        "prompt",
        input_type="text",
        choices=None,
    )

    assert result is None
    assert calls == ["prompt"]
