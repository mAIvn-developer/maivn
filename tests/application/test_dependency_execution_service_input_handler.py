from __future__ import annotations

from maivn._internal.core.services.dependency_execution_service import (
    DependencyExecutionService,
)


def test_custom_input_handler_failure_not_double_invoked() -> None:
    service = DependencyExecutionService()
    calls: list[str] = []

    def handler(prompt: str, **kwargs: object) -> str:
        _ = kwargs
        calls.append(prompt)
        raise ValueError("boom")

    result = service._try_custom_input_handler(
        handler,
        "prompt",
        input_type="text",
        choices=None,
    )

    assert result is None
    assert calls == ["prompt"]
