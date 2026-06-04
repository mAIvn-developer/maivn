# pyright: strict
from __future__ import annotations

from collections.abc import Mapping
from typing import cast, override

from maivn_shared import ToolCall

from maivn._internal.core import ToolEventPayload
from maivn._internal.core.application_services.execution import BackgroundExecutor
from maivn._internal.core.application_services.orchestration import (
    tool_execution_orchestrator,
)
from maivn._internal.core.application_services.tool_execution import (
    tool_execution_service,
)

ToolExecutionOrchestrator = tool_execution_orchestrator.ToolExecutionOrchestrator
ToolExecutionRuntime = tool_execution_orchestrator.ToolExecutionRuntime
ToolBatchCall = tool_execution_orchestrator.ToolBatchCall
ToolResults = tool_execution_orchestrator.ToolResults
ToolExecutionService = tool_execution_service.ToolExecutionService


class SpyOrchestrator(ToolExecutionOrchestrator):
    parallel_called: bool
    sequential_called: bool

    def __init__(
        self,
        *,
        tool_execution_service: ToolExecutionRuntime,
        enable_background_execution: bool = True,
    ) -> None:
        super().__init__(
            tool_execution_service=tool_execution_service,
            enable_background_execution=enable_background_execution,
        )
        self.parallel_called = False
        self.sequential_called = False

    @override
    def _execute_parallel(self, tool_events: Mapping[str, ToolEventPayload]) -> ToolResults:
        self.parallel_called = True
        return {"parallel": True}

    @override
    def _execute_sequential(self, tool_events: Mapping[str, ToolEventPayload]) -> ToolResults:
        self.sequential_called = True
        return {"sequential": True}


class BatchSpyOrchestrator(ToolExecutionOrchestrator):
    calls: list[int]

    def __init__(
        self,
        *,
        tool_execution_service: ToolExecutionRuntime,
        enable_background_execution: bool = True,
    ) -> None:
        super().__init__(
            tool_execution_service=tool_execution_service,
            enable_background_execution=enable_background_execution,
        )
        self.calls = []

    @override
    def _execute_indexed(self, call: ToolBatchCall, idx: int) -> tuple[int, object]:
        self.calls.append(idx)
        return idx, f"value-{idx}"


def _tool_events() -> dict[str, ToolEventPayload]:
    return {
        "evt-1": {"value": {"tool_call": ToolCall(tool_id="tool-1", args={})}},
        "evt-2": {"value": {"tool_call": ToolCall(tool_id="tool-2", args={})}},
    }


def _runtime() -> ToolExecutionRuntime:
    """Wrap ``ToolExecutionService`` as the orchestrator-facing runtime protocol.

    The double-cast through ``object`` is required because ``ToolExecutionService``
    declares ``args: JsonObject`` while the protocol uses ``ToolArgs`` (a slightly
    wider ``dict[str, object]``). The spy overrides never call the inner service,
    so the structural mismatch is irrelevant at runtime.
    """
    return cast(ToolExecutionRuntime, cast(object, ToolExecutionService()))


def test_background_executor_run_inline_returns_completed_future() -> None:
    executor = BackgroundExecutor(run_inline=True)
    future = executor.submit(lambda: "ok")
    assert future.done() is True
    assert future.result() == "ok"


def test_disable_background_execution_forces_sequential() -> None:
    orchestrator = SpyOrchestrator(
        tool_execution_service=_runtime(),
        enable_background_execution=False,
    )
    result = orchestrator.execute_tool_events(_tool_events())

    assert result == {"sequential": True}
    assert orchestrator.sequential_called is True
    assert orchestrator.parallel_called is False


def test_enable_background_execution_uses_parallel_path() -> None:
    orchestrator = SpyOrchestrator(
        tool_execution_service=_runtime(),
        enable_background_execution=True,
    )
    result = orchestrator.execute_tool_events(_tool_events())

    assert result == {"parallel": True}
    assert orchestrator.parallel_called is True
    assert orchestrator.sequential_called is False


def test_disable_background_execution_batches_inline() -> None:
    orchestrator = BatchSpyOrchestrator(
        tool_execution_service=_runtime(),
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
