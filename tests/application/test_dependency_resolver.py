from __future__ import annotations

from typing import Any

import pytest
from maivn_shared import AgentDependency, DataDependency, InterruptDependency, ToolDependency

from maivn._internal.core.application_services.tool_execution.helpers.dependency_resolver import (
    DependencyResolver,
)
from maivn._internal.core.entities.execution_context import ExecutionContext
from maivn._internal.core.exceptions import ToolDependencyNotFoundError


class _StubExecutor:
    def __init__(self, result: Any, *, should_raise: bool = False) -> None:
        self._result = result
        self._should_raise = should_raise
        self.calls: list[tuple[str, dict[str, Any]]] = []

    def execute_tool_call(
        self, tool_id: str, args: dict[str, Any], context: ExecutionContext
    ) -> Any:
        self.calls.append((tool_id, args))
        if self._should_raise:
            raise RuntimeError("boom")
        return self._result


class _StubDependencyService:
    def __init__(self) -> None:
        self.calls: list[Any] = []

    def execute_dependency(self, dependency: Any, context: ExecutionContext) -> Any:
        self.calls.append(dependency)
        return "fallback"


class _StubTool:
    def __init__(self) -> None:
        self.name = "tool"


def test_dependency_resolver_needs_resolution() -> None:
    resolver = DependencyResolver()

    deps = [DataDependency(arg_name="a", data_key="a")]
    assert resolver.needs_resolution(deps, {"a": None}) is True
    assert resolver.needs_resolution(deps, {"a": "value"}) is False

    agent_deps = [AgentDependency(arg_name="agent", agent_id="agent-1")]
    assert resolver.needs_resolution(agent_deps, {"agent": "value"}) is True


def test_dependency_resolver_resolves_agent_dependency() -> None:
    service = _StubDependencyService()
    executor = _StubExecutor(result="agent-result")
    resolver = DependencyResolver(dependency_service=service)

    dependency = AgentDependency(arg_name="agent", agent_id="agent-1")
    context = ExecutionContext(metadata={})

    result = resolver.resolve_all(
        tool=_StubTool(),
        args={},
        dependencies=[dependency],
        context=context,
        executor=executor,
    )

    assert result["agent"] == "agent-result"
    assert executor.calls


def test_dependency_resolver_falls_back_when_agent_call_fails() -> None:
    service = _StubDependencyService()
    executor = _StubExecutor(result=None, should_raise=True)
    resolver = DependencyResolver(dependency_service=service)

    dependency = AgentDependency(arg_name="agent", agent_id="agent-1")
    context = ExecutionContext(metadata={})

    result = resolver.resolve_all(
        tool=_StubTool(),
        args={},
        dependencies=[dependency],
        context=context,
        executor=executor,
    )

    assert result["agent"] == "fallback"
    assert service.calls


def test_dependency_resolver_handles_tool_dependency_not_found() -> None:
    resolver = DependencyResolver()
    dependency = ToolDependency(arg_name="tool", tool_id="missing")

    context = ExecutionContext(tool_results={})
    with pytest.raises(ToolDependencyNotFoundError):
        resolver.resolve_all(_StubTool(), {}, [dependency], context, executor=_StubExecutor(""))


def test_dependency_resolver_resolves_interrupt_dependency() -> None:
    service = _StubDependencyService()
    resolver = DependencyResolver(dependency_service=service)
    dependency = InterruptDependency(
        arg_name="answer", prompt="Prompt", input_handler=lambda p: "ok"
    )

    context = ExecutionContext()
    result = resolver.resolve_all(
        tool=_StubTool(),
        args={},
        dependencies=[dependency],
        context=context,
        executor=_StubExecutor(""),
    )

    assert result["answer"] == "fallback"
    assert service.calls
