from __future__ import annotations

from typing import Any

from maivn._internal.core.application_services.orchestration.tool_execution_orchestrator import (
    ToolExecutionOrchestrator,
)
from maivn._internal.core.entities.execution_context import ExecutionContext


class _Tool:
    def __init__(
        self, tool_id: str, *, tool_type: str = "func", target_agent_id: str | None = None
    ) -> None:
        self.tool_id = tool_id
        self.tool_type = tool_type
        self.target_agent_id = target_agent_id


class _ToolExecution:
    def __init__(self) -> None:
        self.calls: list[tuple[str, dict[str, Any]]] = []
        self.resolved: dict[str, _Tool] = {}

    def execute_tool_call(
        self, tool_id: str, args: dict[str, Any], context: ExecutionContext
    ) -> Any:
        self.calls.append((tool_id, args))
        return {"tool_id": tool_id, "args": args}

    def resolve_tool(self, tool_id: str) -> _Tool:
        tool = self.resolved.get(tool_id)
        if tool is None:
            tool = _Tool(tool_id)
        return tool

    def to_jsonable(self, result: Any) -> Any:
        return result


def test_execute_tool_events_sequential_when_disabled() -> None:
    tool_exec = _ToolExecution()
    orchestrator = ToolExecutionOrchestrator(
        tool_exec,
        enable_background_execution=False,
    )

    tool_events = {
        "evt": {
            "value": {
                "tool_call": {"tool_id": "tool", "args": {"a": 1}},
            }
        }
    }

    results = orchestrator.execute_tool_events(tool_events)

    assert results["evt"]["tool_id"] == "tool"


def test_execute_tool_batch_sequential_when_disabled() -> None:
    tool_exec = _ToolExecution()
    orchestrator = ToolExecutionOrchestrator(
        tool_exec,
        enable_background_execution=False,
    )

    results = orchestrator.execute_tool_batch(
        [
            {"tool_id": "t1", "args": {"a": 1}},
            {"tool_id": "t2", "args": {"b": 2}},
        ]
    )

    assert results[0]["tool_id"] == "t1"
    assert results[1]["tool_id"] == "t2"


def test_store_result_records_agent_alias() -> None:
    tool_exec = _ToolExecution()
    orchestrator = ToolExecutionOrchestrator(tool_exec)

    agent_tool = _Tool("agent-tool", tool_type="agent", target_agent_id="agent-1")
    orchestrator._store_result("agent-tool", agent_tool, {"ok": True})

    results = orchestrator.get_tool_results()
    assert results["agent-tool"] == {"ok": True}
    assert results["agent-1"] == {"ok": True}


def test_build_context_merges_overrides() -> None:
    tool_exec = _ToolExecution()

    class _Scope:
        timeout = 5.0

    orchestrator = ToolExecutionOrchestrator(tool_exec, scope=_Scope(), default_timeout=10.0)
    orchestrator.update_messages(["msg"])

    overrides = ExecutionContext(tool_results={"a": 1}, timeout=None)
    context = orchestrator.build_context(overrides)

    assert context.tool_results == {"a": 1}
    assert context.timeout == 5.0
    assert context.messages == ["msg"]

    dict_context = orchestrator.build_context({"timeout": 1.0, "metadata": {"k": "v"}})
    assert dict_context.timeout == 1.0
    assert dict_context.metadata == {"k": "v"}


def test_process_tool_event_handles_invalid_payload() -> None:
    tool_exec = _ToolExecution()
    orchestrator = ToolExecutionOrchestrator(tool_exec, enable_background_execution=False)

    result = orchestrator._process_tool_event({"value": "bad"})
    assert result == "error:invalid_payload"
