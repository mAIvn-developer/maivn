from __future__ import annotations

from typing import Any

import pytest
from maivn_shared import DataDependency
from pydantic import BaseModel

from maivn._internal.core.application_services.tool_execution.tool_execution_service import (
    BasicToolExecutionService,
    ToolExecutionService,
)
from maivn._internal.core.entities import AgentTool, FunctionTool, ModelTool
from maivn._internal.core.entities.execution_context import ExecutionContext


class _Logger:
    def __init__(self) -> None:
        self.tool_calls: list[dict[str, Any]] = []
        self.debug_calls: list[str] = []
        self.error_calls: list[str] = []
        self.exception_calls: list[str] = []

    def log_tool_execution(self, **kwargs: Any) -> None:
        self.tool_calls.append(kwargs)

    def debug(self, message: str, *args: Any) -> None:
        self.debug_calls.append(message % args if args else message)

    def error(self, message: str, *args: Any) -> None:
        self.error_calls.append(message % args if args else message)

    def exception(self, message: str, *args: Any) -> None:
        self.exception_calls.append(message % args if args else message)


class _InputValidator:
    @staticmethod
    def validate_tool_arguments(args: dict[str, Any]) -> dict[str, Any]:
        return args


class _RaisingValidator:
    @staticmethod
    def validate_tool_arguments(args: dict[str, Any]) -> dict[str, Any]:
        raise ValueError("bad")


class _StrategyRegistry:
    def __init__(self) -> None:
        self.calls: list[tuple[Any, dict[str, Any]]] = []

    def execute(self, tool: Any, args: dict[str, Any], context: ExecutionContext) -> Any:
        self.calls.append((tool, args))
        return {"ok": True}


class _DependencyResolver:
    def __init__(self) -> None:
        self.calls: list[tuple[Any, dict[str, Any]]] = []

    def needs_resolution(self, dependencies: list[Any], args: dict[str, Any]) -> bool:
        return True

    def resolve_all(
        self,
        *,
        tool: Any,
        args: dict[str, Any],
        dependencies: list[Any],
        context: ExecutionContext,
        executor: Any,
    ) -> dict[str, Any]:
        self.calls.append((tool, args))
        return {**args, "resolved": True}


class _Model(BaseModel):
    value: int


def _func(value: int) -> int:
    return value


def test_basic_tool_execution_service_executes_tools() -> None:
    service = BasicToolExecutionService(logger=_Logger())

    function_tool = FunctionTool(name="fn", description="f", tool_id="fn", func=_func)
    model_tool = ModelTool(name="model", description="m", model=_Model)

    service.rebuild_index([function_tool, model_tool])

    assert service.execute_tool_call("fn", {"value": 3}) == 3
    assert service.execute_tool_call("model", {"value": 4}) == {"value": 4}


def test_basic_tool_execution_service_rejects_duplicate_ids() -> None:
    service = BasicToolExecutionService(logger=_Logger())

    tool_a = FunctionTool(name="a", description="a", tool_id="dup", func=_func)
    tool_b = FunctionTool(name="b", description="b", tool_id="dup", func=_func)

    with pytest.raises(ValueError):
        service.rebuild_index([tool_a, tool_b])


def test_tool_execution_service_runs_hooks_and_dependency_resolution() -> None:
    logger = _Logger()
    strategy = _StrategyRegistry()
    resolver = _DependencyResolver()

    tool = FunctionTool(
        name="fn",
        description="f",
        tool_id="fn",
        func=_func,
        dependencies=[DataDependency(arg_name="value", data_key="value")],
    )

    hook_calls: list[str] = []

    def before_hook(payload: dict[str, Any]) -> None:
        hook_calls.append("before")

    def after_hook(payload: dict[str, Any]) -> None:
        hook_calls.append("after")

    tool.before_execute = before_hook
    tool.after_execute = after_hook

    class _Scope:
        hook_execution_mode = "tool"
        before_execute = staticmethod(before_hook)
        after_execute = staticmethod(after_hook)

        def get_swarm(self) -> None:
            return None

    context = ExecutionContext(scope=_Scope())

    service = ToolExecutionService(
        logger=logger,
        dependency_resolver=resolver,
        strategy_registry=strategy,
        input_validator=_InputValidator,
    )
    service.rebuild_index([tool])

    result = service.execute_tool_call("fn", {"value": 1}, context)

    assert result == {"ok": True}
    assert resolver.calls
    assert hook_calls == ["before", "before", "after", "after"]


def test_tool_execution_service_skips_validation_for_agent_tools() -> None:
    logger = _Logger()
    strategy = _StrategyRegistry()

    agent_tool = AgentTool(
        name="agent",
        description="a",
        tool_id="agent",
        func=_func,
        target_agent_id="agent-1",
    )

    service = ToolExecutionService(
        logger=logger,
        strategy_registry=strategy,
        input_validator=_RaisingValidator,
    )
    service.rebuild_index([agent_tool])

    result = service.execute_tool_call("agent", {"value": 2})

    assert result == {"ok": True}
