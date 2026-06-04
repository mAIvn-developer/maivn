# pyright: strict
from __future__ import annotations

from typing import Literal, cast

import pytest
from maivn_shared import BaseDependency, DataDependency
from pydantic import BaseModel, JsonValue

from maivn._internal.core.application_services.helpers.input_validator import InputValidator
from maivn._internal.core.application_services.tool_execution.basic_tool_execution_service import (
    BasicToolExecutionService,
    ToolType,
)
from maivn._internal.core.application_services.tool_execution.execution_strategy import (
    StrategyRegistry,
)
from maivn._internal.core.application_services.tool_execution.helpers.dependency_resolver import (
    DependencyResolver,
)
from maivn._internal.core.application_services.tool_execution.tool_execution_service import (
    ToolExecutionService,
)
from maivn._internal.core.entities import AgentTool, FunctionTool, ModelTool
from maivn._internal.core.entities.execution_context import ExecutionContext
from maivn._internal.core.exceptions import ToolExecutionError

JsonObject = dict[str, JsonValue]


class _Logger:
    def __init__(self) -> None:
        self.tool_calls: list[dict[str, object]] = []
        self.debug_calls: list[str] = []
        self.error_calls: list[str] = []
        self.exception_calls: list[str] = []

    def log_tool_execution(
        self,
        phase: Literal["start", "completed", "failed"],
        tool_id: str,
        tool_name: str,
        tool_type: str | None = None,
        args: dict[str, object] | None = None,
        result: object = None,
        error: str | None = None,
        elapsed_ms: int | None = None,
        session_id: str | None = None,
        thread_id: str | None = None,
        task_idx: int | None = None,
        result_safe_for_log: bool = False,
        error_safe_for_log: bool = False,
        **metadata: object,
    ) -> None:
        self.tool_calls.append(
            {
                "phase": phase,
                "tool_id": tool_id,
                "tool_name": tool_name,
                "tool_type": tool_type,
                "args": args,
                "result": result,
                "error": error,
                "elapsed_ms": elapsed_ms,
                "session_id": session_id,
                "thread_id": thread_id,
                "task_idx": task_idx,
                "metadata": metadata,
            }
        )

    def log_token_usage(
        self,
        agent_name: str,
        invocation_type: str,
        total_tokens: int,
        input_tokens: int,
        output_tokens: int,
        cache_read_tokens: int = 0,
        cache_creation_tokens: int = 0,
        model: str = "unknown",
        provider: str = "unknown",
        total_cost: float = 0.0,
        session_id: str | None = None,
        thread_id: str | None = None,
        batch_number: int | None = None,
        **metadata: object,
    ) -> None:
        _ = (
            agent_name,
            invocation_type,
            total_tokens,
            input_tokens,
            output_tokens,
            cache_read_tokens,
            cache_creation_tokens,
            model,
            provider,
            total_cost,
            session_id,
            thread_id,
            batch_number,
            metadata,
        )

    def _format(self, message: str, args: tuple[object, ...]) -> str:
        return message % args if args else message

    def debug(
        self, message: str, *args: object, component: str = "APP", **metadata: object
    ) -> None:
        _ = (component, metadata)
        self.debug_calls.append(self._format(message, args))

    def info(self, message: str, *args: object, component: str = "APP", **metadata: object) -> None:
        _ = (message, args, component, metadata)

    def warning(
        self, message: str, *args: object, component: str = "APP", **metadata: object
    ) -> None:
        _ = (message, args, component, metadata)

    def error(
        self, message: str, *args: object, component: str = "APP", **metadata: object
    ) -> None:
        _ = (component, metadata)
        self.error_calls.append(self._format(message, args))

    def exception(self, message: str, component: str = "APP", **metadata: object) -> None:
        _ = (component, metadata)
        self.exception_calls.append(message)

    def critical(
        self, message: str, *args: object, component: str = "APP", **metadata: object
    ) -> None:
        _ = (message, args, component, metadata)


class _InputValidator:
    enforce_security_checks: bool = True

    @classmethod
    def validate_tool_arguments(cls, args: JsonObject) -> JsonObject:
        return args


class _RaisingValidator:
    enforce_security_checks: bool = True

    @classmethod
    def validate_tool_arguments(cls, args: JsonObject) -> JsonObject:
        _ = args
        raise ValueError("bad")


class _StrategyRegistry:
    def __init__(self) -> None:
        self.calls: list[tuple[object, dict[str, object]]] = []

    def execute(
        self,
        tool: object,
        args: dict[str, object],
        context: ExecutionContext,
    ) -> object:
        _ = context
        self.calls.append((tool, args))
        return {"ok": True}


class _DependencyResolver:
    def __init__(self) -> None:
        self.calls: list[tuple[object, dict[str, object]]] = []

    def needs_resolution(self, dependencies: list[BaseDependency], args: dict[str, object]) -> bool:
        _ = (dependencies, args)
        return True

    def resolve_all(
        self,
        *,
        tool: ToolType,
        args: dict[str, object],
        dependencies: list[BaseDependency],
        context: ExecutionContext,
        executor: object,
    ) -> dict[str, object]:
        _ = (dependencies, context, executor)
        self.calls.append((tool, args))
        return {**args, "resolved": True}


class _Model(BaseModel):
    value: int


def _func(value: int) -> int:
    return value


def test_basic_tool_execution_service_executes_tools() -> None:
    service = BasicToolExecutionService(logger=_Logger())

    function_tool = FunctionTool(name="fn", description="f", tool_id="fn", func=_func)

    service.rebuild_index([function_tool])

    assert service.execute_tool_call("fn", {"value": 3}) == 3


def test_basic_tool_execution_service_rejects_model_tools() -> None:
    """A model tool reaching the base executor fails closed.

    Model tools execute via ``ModelExecutionStrategy`` (wired into
    ``ToolExecutionService``'s strategy registry). The base
    ``BasicToolExecutionService`` isinstance dispatch never executes them;
    it raises ``ToolExecutionError`` rather than silently dropping the call.
    """
    service = BasicToolExecutionService(logger=_Logger())
    model_tool = ModelTool(name="model", description="m", model=_Model)
    service.rebuild_index([model_tool])

    with pytest.raises(ToolExecutionError, match="base executor received a ModelTool"):
        _ = service.execute_tool_call("model", {"value": 4})


def test_basic_tool_execution_service_rejects_duplicate_ids() -> None:
    service = BasicToolExecutionService(logger=_Logger())

    tool_a = FunctionTool(name="a", description="a", tool_id="dup", func=_func)
    tool_b = FunctionTool(name="b", description="b", tool_id="dup", func=_func)

    with pytest.raises(ValueError, match="dup"):
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

    def before_hook(payload: dict[str, object]) -> None:
        _ = payload
        hook_calls.append("before")

    def after_hook(payload: dict[str, object]) -> None:
        _ = payload
        hook_calls.append("after")

    tool.before_execute = before_hook
    tool.after_execute = after_hook

    class _Scope:
        hook_execution_mode: str = "tool"
        before_execute: staticmethod[[dict[str, object]], None] = staticmethod(before_hook)
        after_execute: staticmethod[[dict[str, object]], None] = staticmethod(after_hook)

        def get_swarm(self) -> None:
            return None

    context = ExecutionContext(scope=_Scope())

    service = ToolExecutionService(
        logger=logger,
        dependency_resolver=cast(DependencyResolver, cast(object, resolver)),
        strategy_registry=cast(StrategyRegistry, cast(object, strategy)),
        input_validator=cast(type[InputValidator], _InputValidator),
    )
    service.rebuild_index([tool])

    result = service.execute_tool_call("fn", {"value": 1}, context)

    assert result == {"ok": True}
    assert resolver.calls
    assert hook_calls == ["before", "before", "after", "after"]


def test_tool_execution_service_applies_metadata_default_args() -> None:
    strategy = _StrategyRegistry()
    tool = FunctionTool(
        name="fn",
        description="fn",
        tool_id="fn",
        func=_func,
        metadata={"default_args": {"value": 7, "unused": "default"}},
    )
    service = ToolExecutionService(
        logger=_Logger(),
        strategy_registry=cast(StrategyRegistry, cast(object, strategy)),
        input_validator=cast(type[InputValidator], _InputValidator),
    )
    service.rebuild_index([tool])

    _ = service.execute_tool_call("fn", {})

    assert strategy.calls[0][1] == {"value": 7}


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
        strategy_registry=cast(StrategyRegistry, cast(object, strategy)),
        input_validator=cast(type[InputValidator], _RaisingValidator),
    )
    service.rebuild_index([agent_tool])

    result = service.execute_tool_call("agent", {"value": 2})

    assert result == {"ok": True}
