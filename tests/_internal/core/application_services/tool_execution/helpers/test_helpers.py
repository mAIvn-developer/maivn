# pyright: strict
from __future__ import annotations

from typing import cast

import pytest
from pydantic import BaseModel

from maivn._internal.core.application_services.tool_execution.argument_utils import (
    get_allowed_parameters,
    prune_arguments,
)
from maivn._internal.core.application_services.tool_execution.helpers.argument_validator import (
    ArgumentValidator,
)
from maivn._internal.core.application_services.tool_execution.helpers.function_executor import (
    FunctionExecutor,
)
from maivn._internal.core.entities import FunctionTool, McpTool, ModelTool
from maivn._internal.core.exceptions import ArgumentValidationError, ToolExecutionError


class _Model(BaseModel):
    value: int


def _func(a: int, b: int) -> int:
    return a + b


def _func_kwargs(a: int, **kwargs: object) -> int:
    _ = kwargs
    return a


def test_argument_utils_function_and_model() -> None:
    func_tool = FunctionTool(name="fn", description="f", tool_id="t1", func=_func)
    model_tool = ModelTool(name="model", description="m", model=_Model)
    mcp_tool = McpTool(
        name="mcp",
        description="mcp",
        server_name="server",
        mcp_tool_name="tool",
    )

    assert get_allowed_parameters(func_tool) == {"a", "b"}
    assert get_allowed_parameters(model_tool) == {"value"}
    assert get_allowed_parameters(mcp_tool) is None

    func_tool_kwargs = FunctionTool(
        name="fn2",
        description="f",
        tool_id="t2",
        func=_func_kwargs,
    )
    assert get_allowed_parameters(func_tool_kwargs) is None


def test_prune_arguments_drops_extra_keys() -> None:
    func_tool = FunctionTool(name="fn", description="f", tool_id="t1", func=_func)

    filtered, dropped = prune_arguments(func_tool, {"a": 1, "b": 2, "c": 3})

    assert filtered == {"a": 1, "b": 2}
    assert dropped == ["c"]


def test_argument_validator_rejects_missing_arg() -> None:
    validator = ArgumentValidator()

    with pytest.raises(ValueError, match="Invalid arguments for function"):
        validator.validate(_func, {"a": 1})


def test_function_executor_happy_path() -> None:
    executor = FunctionExecutor()
    tool = FunctionTool(name="fn", description="f", tool_id="t1", func=_func)

    assert executor.execute(tool, {"a": 1, "b": 2}) == 3


def test_function_executor_reports_argument_errors() -> None:
    executor = FunctionExecutor()
    tool = FunctionTool(name="fn", description="f", tool_id="t1", func=_func)

    with pytest.raises(ArgumentValidationError):
        _ = executor.execute(tool, {"a": 1})


def test_function_executor_requires_callable() -> None:
    executor = FunctionExecutor()

    class _BadTool:
        name: str = "bad"
        func: object = None

    # Pattern 2: deliberately pass a tool stand-in that does not satisfy
    # FunctionTool's runtime contract to exercise the executor's guard path.
    bad_tool = cast(FunctionTool, cast(object, _BadTool()))

    with pytest.raises(ToolExecutionError):
        _ = executor.execute(bad_tool, {})
