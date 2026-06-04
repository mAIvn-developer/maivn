# pyright: strict
from __future__ import annotations

from collections.abc import Sequence
from typing import cast

import pytest
from maivn_shared import (
    AgentDependency,
    BaseMessage,
    DataDependency,
    InterruptDependency,
    ToolDependency,
)

from maivn._internal.core.entities.execution_context import ExecutionContext
from maivn._internal.core.services.agent_execution_service import AgentExecutionService
from maivn._internal.core.services.dependency_execution_service import DependencyExecutionService
from maivn._internal.core.services.interrupt_service import InterruptService


class _AgentExecutionStub:
    calls: list[Sequence[BaseMessage]]

    def __init__(self) -> None:
        self.calls = []

    def execute_agent_dependency(
        self,
        dependency: AgentDependency,
        messages: Sequence[BaseMessage],
        timeout: float | None = None,
    ) -> object:
        _ = (dependency, timeout)
        self.calls.append(messages)
        return "agent-result"


class _InterruptServiceStub:
    choice_calls: list[str]
    confirm_calls: list[str]
    input_calls: list[str]

    def __init__(self) -> None:
        self.choice_calls = []
        self.confirm_calls = []
        self.input_calls = []

    def get_user_choice(self, prompt: str, choices: list[str]) -> str:
        self.choice_calls.append(prompt)
        return choices[0]

    def get_user_confirmation(self, prompt: str) -> bool:
        self.confirm_calls.append(prompt)
        return True

    def get_user_input(self, prompt: str) -> str:
        self.input_calls.append(prompt)
        return "value"


def _as_agent_service(stub: _AgentExecutionStub) -> AgentExecutionService:
    """Bridge the duck-typed agent execution stub to the real slot."""
    return cast(AgentExecutionService, cast(object, stub))


def _as_interrupt_service(stub: _InterruptServiceStub) -> InterruptService:
    """Bridge the duck-typed interrupt service stub to the real slot."""
    return cast(InterruptService, cast(object, stub))


def _noop_handler(prompt: str) -> object:
    """Required input_handler placeholder for InterruptDependency construction.

    Tests that exercise the fallback path overwrite the runtime handler via
    setattr to bypass static validation — the service treats anything not
    callable as "no custom handler" via ``callable(input_handler)``.
    """
    _ = prompt
    return None


def _clear_input_handler(dep: InterruptDependency) -> None:
    """Forcibly clear the input_handler attr to exercise the fallback path."""
    # ``setattr`` bypasses the static contract on the Pydantic field so the
    # fallback-path probe ('not callable' branch) can run under strict types.
    setattr(dep, "input_handler", None)  # noqa: B010


def test_dependency_execution_service_uses_default_messages() -> None:
    agent_service = _AgentExecutionStub()
    service = DependencyExecutionService(agent_execution_service=_as_agent_service(agent_service))

    dep = AgentDependency(arg_name="agent", agent_id="agent-1")
    result = service.execute_dependency(dep, ExecutionContext())

    assert result == "agent-result"
    assert agent_service.calls
    first_call = agent_service.calls[0]
    first_msg = first_call[0]
    # ``BaseMessage.content`` is typed ``str | list[str | dict[Unknown, ...]]``;
    # the inner ``dict`` is unparameterised so the strict checker flags partial
    # unknowns. ``cast(object, ...)`` narrows to truthy-only assertion semantics.
    content = cast(object, first_msg.content)
    assert content


def test_dependency_execution_service_handles_interrupt_types() -> None:
    interrupt_service = _InterruptServiceStub()
    service = DependencyExecutionService(interrupt_service=_as_interrupt_service(interrupt_service))

    dep_choice = InterruptDependency(
        arg_name="choice",
        prompt="Pick",
        input_handler=_noop_handler,
        input_type="choice",
        choices=["a", "b"],
    )
    _clear_input_handler(dep_choice)
    assert service.execute_dependency(dep_choice, ExecutionContext()) == "a"

    dep_bool = InterruptDependency(
        arg_name="flag",
        prompt="Confirm",
        input_handler=_noop_handler,
        input_type="boolean",
    )
    _clear_input_handler(dep_bool)
    assert service.execute_dependency(dep_bool, ExecutionContext()) is True

    dep_text = InterruptDependency(
        arg_name="text",
        prompt="Enter",
        input_handler=_noop_handler,
    )
    _clear_input_handler(dep_text)
    assert service.execute_dependency(dep_text, ExecutionContext()) == "value"


def test_dependency_execution_service_custom_handler_extended_signature() -> None:
    def handler(prompt: str, *, input_type: str, choices: list[str]) -> str:
        return f"{prompt}:{input_type}:{choices[0]}"

    service = DependencyExecutionService()
    dep = InterruptDependency(
        arg_name="choice",
        prompt="Pick",
        input_handler=_noop_handler,
        input_type="choice",
        choices=["a"],
    )
    # The handler signature widens beyond Callable[[str], object]; the service
    # introspects parameters at runtime to forward input_type + choices. We
    # install the extended-signature handler via setattr so the static
    # contract on the Pydantic field isn't violated.
    setattr(dep, "input_handler", handler)  # noqa: B010

    result = service.execute_dependency(dep, ExecutionContext())

    assert result == "Pick:choice:a"


def test_dependency_execution_service_data_dependency_errors() -> None:
    service = DependencyExecutionService()
    dep = DataDependency(arg_name="data", data_key="missing")

    class _Scope:
        private_data: dict[str, object] = {}

    with pytest.raises(ValueError, match="data"):
        _ = service.execute_dependency(dep, ExecutionContext(scope=_Scope()))


def test_dependency_execution_service_tool_dependency_raises() -> None:
    service = DependencyExecutionService()
    dep = ToolDependency(arg_name="tool", tool_id="tool-1")

    with pytest.raises(ValueError, match="[Tt]ool"):
        _ = service.execute_dependency(dep, ExecutionContext())
