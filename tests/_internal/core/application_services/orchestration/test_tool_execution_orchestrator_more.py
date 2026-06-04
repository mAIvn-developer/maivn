# pyright: strict
from __future__ import annotations

from collections.abc import Callable
from typing import cast

from maivn_shared import ToolCall

from maivn._internal.core import ToolEventPayload, ToolEventValue
from maivn._internal.core.application_services.orchestration.tool_execution_orchestrator import (
    ToolExecutionOrchestrator,
)
from maivn._internal.core.entities.execution_context import ExecutionContext

ToolArgs = dict[str, object]


class _Tool:
    def __init__(
        self,
        tool_id: str,
        *,
        tool_type: str = "func",
        target_agent_id: str | None = None,
    ) -> None:
        self.tool_id: str = tool_id
        self.tool_type: str = tool_type
        self.target_agent_id: str | None = target_agent_id


class _ToolExecution:
    """Stub satisfying `ToolExecutionRuntime` Protocol.

    `resolve_tool` is intentionally implemented (S10d NEW-OUT-3 fix). The S10d
    refactor required `ToolExecutionRuntime` to expose `resolve_tool`, so the
    test stub now matches that protocol surface exactly.
    """

    def __init__(self) -> None:
        self.calls: list[tuple[str, ToolArgs]] = []
        self.resolved: dict[str, _Tool] = {}

    def execute_tool_call(
        self,
        tool_id: str,
        args: ToolArgs,
        context: ExecutionContext | None = None,
        *,
        tool_event_id: str | None = None,
    ) -> object:
        _ = (context, tool_event_id)
        self.calls.append((tool_id, args))
        return {"tool_id": tool_id, "args": args}

    def resolve_tool(self, tool_id: str) -> object:
        tool = self.resolved.get(tool_id)
        if tool is None:
            tool = _Tool(tool_id)
        return tool

    def to_jsonable(self, obj: object) -> object:
        return obj


def test_execute_tool_events_sequential_when_disabled() -> None:
    tool_exec = _ToolExecution()
    orchestrator = ToolExecutionOrchestrator(
        tool_exec,
        enable_background_execution=False,
    )

    tool_events: dict[str, ToolEventPayload] = {
        "evt": ToolEventPayload(
            value=ToolEventValue(
                tool_call=ToolCall(tool_id="tool", args={"a": 1}),
            )
        )
    }

    results = orchestrator.execute_tool_events(tool_events)

    evt_result = results["evt"]
    assert isinstance(evt_result, dict)
    assert evt_result["tool_id"] == "tool"


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

    first = results[0]
    second = results[1]
    assert isinstance(first, dict)
    assert isinstance(second, dict)
    assert first["tool_id"] == "t1"
    assert second["tool_id"] == "t2"


def test_store_result_records_agent_alias() -> None:
    tool_exec = _ToolExecution()
    orchestrator = ToolExecutionOrchestrator(tool_exec)

    agent_tool = _Tool("agent-tool", tool_type="agent", target_agent_id="agent-1")
    # `_store_result` is a private helper; the test exercises it directly to
    # confirm the agent-alias side effect. ``getattr`` keeps the private access
    # out of the ``reportPrivateUsage`` path while preserving the call shape.
    store = cast(
        Callable[[str, object, object], None],
        getattr(orchestrator, "_store_result"),  # noqa: B009
    )
    store("agent-tool", agent_tool, {"ok": True})

    results = orchestrator.get_tool_results()
    assert results["agent-tool"] == {"ok": True}
    assert results["agent-1"] == {"ok": True}


def test_build_context_merges_overrides() -> None:
    tool_exec = _ToolExecution()

    class _Scope:
        timeout: float = 5.0

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

    # `_process_tool_event` is private; exercised directly here via ``getattr``
    # so the strict ``reportPrivateUsage`` check stays quiet at the boundary.
    process = cast(
        Callable[[ToolEventPayload], object],
        getattr(orchestrator, "_process_tool_event"),  # noqa: B009
    )
    # Pattern 2: deliberately pass a malformed payload (string-typed `value` field)
    # to exercise the orchestrator's invalid-payload guard.
    bad_payload = cast(ToolEventPayload, cast(object, {"value": "bad"}))
    result = process(bad_payload)
    assert result == "error:invalid_payload"
