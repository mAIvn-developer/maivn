# pyright: strict
from __future__ import annotations

from collections.abc import Sequence
from typing import cast

from maivn_shared import (
    AgentDependency,
    BaseMessage,
    DataDependency,
    HumanMessage,
    InterruptDependency,
)
from typing_extensions import override

from maivn._internal.core.entities.execution_context import ExecutionContext
from maivn._internal.core.services.agent_execution_service import AgentExecutionTarget
from maivn._internal.core.services.dependency_execution_service import (
    DependencyExecutionService,
)
from maivn._internal.core.services.interrupt_service import InterruptService


class DummyScope:
    def __init__(self) -> None:
        self.private_data: dict[str, object] = {"foo": "bar"}


class FakeAgent:
    def __init__(self) -> None:
        self.name: str | None = "helper"
        self.id: str = "helper-id"

    def invoke(self, messages: Sequence[BaseMessage]) -> object:
        contents: list[object] = [cast(object, m.content) for m in messages]
        return type("Resp", (), {"result": contents})()


def test_execute_data_dependency_returns_scope_value() -> None:
    service = DependencyExecutionService()
    context = ExecutionContext(scope=DummyScope())
    dependency = DataDependency(arg_name="foo_value", data_key="foo")

    result = service.execute_dependency(dependency, context)

    assert result == "bar"


def test_execute_agent_dependency_uses_agent_registry() -> None:
    agent = FakeAgent()

    class FakeRegistry:
        def get_agent(self, agent_id: str) -> AgentExecutionTarget | None:
            return agent if agent_id == agent.id else None

        def get_agent_by_name(self, name: str) -> AgentExecutionTarget | None:
            return agent if name == agent.name else None

    deps = DependencyExecutionService()
    deps.set_agent_registry(FakeRegistry())
    context = ExecutionContext(messages=[HumanMessage(content="hi")])
    dependency = AgentDependency(arg_name="helper", agent_id=agent.id)

    result = deps.execute_dependency(dependency, context)

    assert result == ["hi"]


def test_execute_interrupt_dependency_uses_interrupt_service() -> None:
    captured: dict[str, str] = {}

    class StubInterruptService(InterruptService):
        def __init__(self) -> None:
            super().__init__()

        @override
        def get_user_input(
            self,
            prompt: str,
            *,
            input_type: str = "text",
            choices: list[str] | None = None,
            data_key: str | None = None,
            arg_name: str | None = None,
        ) -> str:
            _ = (input_type, choices, data_key, arg_name)
            captured["prompt"] = prompt
            return "user-supplied"

    deps = DependencyExecutionService(interrupt_service=StubInterruptService())
    context = ExecutionContext()
    dependency = InterruptDependency(
        arg_name="answer",
        prompt="Enter value",
        input_handler=lambda prompt: "ignored",
    )
    setattr(dependency, "input_handler", cast(object, None))  # noqa: B010 - bypass Pydantic field type for None test

    result = deps.execute_dependency(dependency, context)

    assert result == "user-supplied"
    assert captured["prompt"] == "Enter value"
