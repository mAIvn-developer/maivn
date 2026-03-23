from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from maivn_shared import AgentDependency, DataDependency, HumanMessage, InterruptDependency

from maivn._internal.core.entities.execution_context import ExecutionContext
from maivn._internal.core.services.dependency_execution_service import (
    DependencyExecutionService,
)
from maivn._internal.core.services.interrupt_service import InterruptService


class DummyScope:
    def __init__(self) -> None:
        self.private_data = {"foo": "bar"}


class FakeAgent:
    def __init__(self) -> None:
        self.name = "helper"
        self.id = "helper-id"

    def invoke(
        self,
        messages: Sequence[HumanMessage],
        *,
        force_final_tool: bool = False,
        targeted_tools: list[str] | None = None,
        model: str | None = None,
        reasoning: str | None = None,
        thread_id: str | None = None,
        verbose: bool = False,
    ) -> Any:
        return type("Resp", (), {"result": [m.content for m in messages]})()


def test_execute_data_dependency_returns_scope_value() -> None:
    service = DependencyExecutionService()
    context = ExecutionContext(scope=DummyScope())
    dependency = DataDependency(arg_name="foo_value", data_key="foo")

    result = service.execute_dependency(dependency, context)

    assert result == "bar"


def test_execute_agent_dependency_uses_agent_registry() -> None:
    agent = FakeAgent()

    class FakeRegistry:
        def get_agent(self, agent_id: str) -> FakeAgent | None:
            return agent if agent_id == agent.id else None

        def get_agent_by_name(self, name: str) -> FakeAgent | None:
            return agent if name == agent.name else None

    deps = DependencyExecutionService()
    deps.set_agent_registry(FakeRegistry())
    context = ExecutionContext(messages=[HumanMessage(content="hi")])
    dependency = AgentDependency(arg_name="helper", agent_id=agent.id)

    result = deps.execute_dependency(dependency, context)

    assert result == ["hi"]


def test_execute_interrupt_dependency_uses_interrupt_service() -> None:
    captured: dict[str, Any] = {}

    class StubInterruptService(InterruptService):
        def __init__(self) -> None:
            super().__init__()

        def get_user_input(self, prompt: str) -> str:
            captured["prompt"] = prompt
            return "user-supplied"

    deps = DependencyExecutionService(interrupt_service=StubInterruptService())
    context = ExecutionContext()
    dependency = InterruptDependency(
        arg_name="answer",
        prompt="Enter value",
        input_handler=lambda prompt: "ignored",
    )
    dependency.input_handler = None  # type: ignore[attr-defined]

    result = deps.execute_dependency(dependency, context)

    assert result == "user-supplied"
    assert captured["prompt"] == "Enter value"
