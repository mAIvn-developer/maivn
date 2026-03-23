from __future__ import annotations

from maivn._internal.core.application_services.orchestration import (
    tool_execution_orchestrator,
)
from maivn._internal.core.application_services.tool_execution import (
    tool_execution_service,
)
from maivn._internal.core.entities.execution_context import ExecutionContext

ToolExecutionOrchestrator = tool_execution_orchestrator.ToolExecutionOrchestrator
ToolExecutionService = tool_execution_service.ToolExecutionService


def test_build_context_merges_missing_scope_for_execution_context_overrides() -> None:
    scope = type("Scope", (), {"private_data": {"email": "user@example.com"}})()
    orchestrator = ToolExecutionOrchestrator(
        tool_execution_service=ToolExecutionService(),
        scope=scope,
    )

    ctx = orchestrator.build_context(ExecutionContext(metadata={"k": "v"}))
    assert ctx.scope is scope
    assert ctx.metadata == {"k": "v"}


def test_build_context_respects_empty_tool_results_override() -> None:
    orchestrator = ToolExecutionOrchestrator(tool_execution_service=ToolExecutionService())
    orchestrator.get_tool_results()["fallback"] = {"x": 1}

    ctx = orchestrator.build_context(ExecutionContext(tool_results={}))
    assert ctx.tool_results == {}
