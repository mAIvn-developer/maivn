# pyright: strict
from __future__ import annotations

from typing import cast

from maivn._internal.core.application_services.orchestration import (
    tool_execution_orchestrator,
)
from maivn._internal.core.application_services.tool_execution import (
    tool_execution_service,
)
from maivn._internal.core.entities.execution_context import ExecutionContext

ToolExecutionOrchestrator = tool_execution_orchestrator.ToolExecutionOrchestrator
ToolExecutionRuntime = tool_execution_orchestrator.ToolExecutionRuntime
ToolExecutionService = tool_execution_service.ToolExecutionService


def _runtime() -> ToolExecutionRuntime:
    """Wrap ``ToolExecutionService`` as the orchestrator-facing runtime protocol.

    The double-cast through ``object`` is required because ``ToolExecutionService``
    declares ``args: JsonObject`` while the protocol uses the slightly-wider
    ``ToolArgs = dict[str, object]``.
    """
    return cast(ToolExecutionRuntime, cast(object, ToolExecutionService()))


class _Scope:
    private_data: dict[str, str]

    def __init__(self) -> None:
        self.private_data = {"email": "user@example.com"}


def test_build_context_merges_missing_scope_for_execution_context_overrides() -> None:
    scope = _Scope()
    orchestrator = ToolExecutionOrchestrator(
        tool_execution_service=_runtime(),
        scope=scope,
    )

    ctx = orchestrator.build_context(ExecutionContext(metadata={"k": "v"}))
    assert ctx.scope is scope
    assert ctx.metadata == {"k": "v"}


def test_build_context_respects_empty_tool_results_override() -> None:
    orchestrator = ToolExecutionOrchestrator(tool_execution_service=_runtime())
    orchestrator.get_tool_results()["fallback"] = {"x": 1}

    ctx = orchestrator.build_context(ExecutionContext(tool_results={}))
    assert ctx.tool_results == {}
