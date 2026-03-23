from __future__ import annotations

from typing import Any

import pytest
from maivn_shared import AgentDependency, DataDependency, InterruptDependency, ToolDependency

from maivn._internal.core.entities.execution_context import ExecutionContext
from maivn._internal.core.services.dependency_execution_service import DependencyExecutionService


class _AgentExecutionStub:
    def __init__(self) -> None:
        self.calls: list[Any] = []

    def execute_agent_dependency(self, dependency, messages, timeout):
        self.calls.append(messages)
        return "agent-result"


class _InterruptServiceStub:
    def __init__(self) -> None:
        self.choice_calls: list[str] = []
        self.confirm_calls: list[str] = []
        self.input_calls: list[str] = []

    def get_user_choice(self, prompt: str, choices: list[str]):
        self.choice_calls.append(prompt)
        return choices[0]

    def get_user_confirmation(self, prompt: str):
        self.confirm_calls.append(prompt)
        return True

    def get_user_input(self, prompt: str):
        self.input_calls.append(prompt)
        return "value"


def test_dependency_execution_service_uses_default_messages() -> None:
    agent_service = _AgentExecutionStub()
    service = DependencyExecutionService(agent_execution_service=agent_service)

    dep = AgentDependency(arg_name="agent", agent_id="agent-1")
    result = service.execute_dependency(dep, ExecutionContext())

    assert result == "agent-result"
    assert agent_service.calls
    assert agent_service.calls[0][0].content


def test_dependency_execution_service_handles_interrupt_types() -> None:
    interrupt_service = _InterruptServiceStub()
    service = DependencyExecutionService(interrupt_service=interrupt_service)

    dep_choice = InterruptDependency(
        arg_name="choice",
        prompt="Pick",
        input_handler=lambda p: "",
        input_type="choice",
        choices=["a", "b"],
    )
    dep_choice.input_handler = None  # type: ignore[attr-defined]
    assert service.execute_dependency(dep_choice, ExecutionContext()) == "a"

    dep_bool = InterruptDependency(
        arg_name="flag",
        prompt="Confirm",
        input_handler=lambda p: "",
        input_type="boolean",
    )
    dep_bool.input_handler = None  # type: ignore[attr-defined]
    assert service.execute_dependency(dep_bool, ExecutionContext()) is True

    dep_text = InterruptDependency(
        arg_name="text",
        prompt="Enter",
        input_handler=lambda p: "",
    )
    dep_text.input_handler = None  # type: ignore[attr-defined]
    assert service.execute_dependency(dep_text, ExecutionContext()) == "value"


def test_dependency_execution_service_custom_handler_extended_signature() -> None:
    def handler(prompt: str, *, input_type: str, choices: list[str]) -> str:
        return f"{prompt}:{input_type}:{choices[0]}"

    service = DependencyExecutionService()
    dep = InterruptDependency(
        arg_name="choice",
        prompt="Pick",
        input_handler=handler,
        input_type="choice",
        choices=["a"],
    )

    result = service.execute_dependency(dep, ExecutionContext())

    assert result == "Pick:choice:a"


def test_dependency_execution_service_data_dependency_errors() -> None:
    service = DependencyExecutionService()
    dep = DataDependency(arg_name="data", data_key="missing")

    class _Scope:
        private_data = {}

    with pytest.raises(ValueError):
        service.execute_dependency(dep, ExecutionContext(scope=_Scope()))


def test_dependency_execution_service_tool_dependency_raises() -> None:
    service = DependencyExecutionService()
    dep = ToolDependency(arg_name="tool", tool_id="tool-1")

    with pytest.raises(ValueError):
        service.execute_dependency(dep, ExecutionContext())
