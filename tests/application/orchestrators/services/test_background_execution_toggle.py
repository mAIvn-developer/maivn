from __future__ import annotations

from typing import Any

from maivn._internal.core.application_services.execution import BackgroundExecutor
from maivn._internal.core.application_services.orchestration import (
    tool_execution_orchestrator,
)
from maivn._internal.core.application_services.tool_execution import (
    tool_execution_service,
)

ToolExecutionOrchestrator = tool_execution_orchestrator.ToolExecutionOrchestrator
ToolExecutionService = tool_execution_service.ToolExecutionService


class SpyOrchestrator(ToolExecutionOrchestrator):
    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self.parallel_called = False
        self.sequential_called = False

    def _execute_parallel(self, tool_events: Any) -> dict[str, Any]:
        self.parallel_called = True
        return {"parallel": True}

    def _execute_sequential(self, tool_events: Any) -> dict[str, Any]:
        self.sequential_called = True
        return {"sequential": True}


class BatchSpyOrchestrator(ToolExecutionOrchestrator):
    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self.calls: list[int] = []

    def _execute_indexed(self, call: dict[str, Any], idx: int) -> tuple[int, Any]:
        self.calls.append(idx)
        return idx, f"value-{idx}"


def _tool_events() -> dict[str, Any]:
    return {
        "evt-1": {"value": {"tool_call": {"tool_id": "tool-1", "args": {}}}},
        "evt-2": {"value": {"tool_call": {"tool_id": "tool-2", "args": {}}}},
    }


def test_background_executor_run_inline_returns_completed_future() -> None:
    executor = BackgroundExecutor(run_inline=True)
    future = executor.submit(lambda: "ok")
    assert future.done() is True
    assert future.result() == "ok"


def test_disable_background_execution_forces_sequential() -> None:
    orchestrator = SpyOrchestrator(
        tool_execution_service=ToolExecutionService(),
        enable_background_execution=False,
    )
    result = orchestrator.execute_tool_events(_tool_events())

    assert result == {"sequential": True}
    assert orchestrator.sequential_called is True
    assert orchestrator.parallel_called is False


def test_enable_background_execution_uses_parallel_path() -> None:
    orchestrator = SpyOrchestrator(
        tool_execution_service=ToolExecutionService(),
        enable_background_execution=True,
    )
    result = orchestrator.execute_tool_events(_tool_events())

    assert result == {"parallel": True}
    assert orchestrator.parallel_called is True
    assert orchestrator.sequential_called is False


def test_disable_background_execution_batches_inline() -> None:
    orchestrator = BatchSpyOrchestrator(
        tool_execution_service=ToolExecutionService(),
        enable_background_execution=False,
    )
    results = orchestrator.execute_tool_batch(
        [
            {"tool_id": "tool-1", "args": {"k": "v"}},
            {"tool_id": "tool-2", "args": {"k": "v2"}},
        ]
    )

    assert results == ["value-0", "value-1"]
    assert orchestrator.calls == [0, 1]
