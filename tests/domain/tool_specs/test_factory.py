from __future__ import annotations

from typing import Any

import pytest
from maivn_shared import DataDependency
from pydantic import BaseModel

from maivn._internal.core.entities import AgentTool, FunctionTool, McpTool, ModelTool
from maivn._internal.core.entities.tools.base_tool import BaseTool
from maivn._internal.core.tool_specs.factory import ToolSpecFactory


class _Payload(BaseModel):
    value: int


class _UnsupportedTool(BaseTool):
    def is_executable(self) -> bool:
        return False


def _identity(value: int) -> int:
    return value


def test_tool_spec_factory_creates_function_tool_specs_and_applies_flags() -> None:
    factory = ToolSpecFactory()
    dependency = DataDependency(arg_name="payload", data_key="payload")
    tool = FunctionTool(name="alpha", description="Alpha tool", tool_id="alpha", func=_identity)

    spec = factory.create(
        agent_id="agent-1",
        tool=tool,
        dependencies=[dependency],
        always_execute=True,
        final_tool=True,
    )

    assert tool.dependencies == [dependency]
    assert spec.name == "alpha"
    assert spec.always_execute is True
    assert spec.final_tool is True


def test_tool_spec_factory_deduplicates_specs_within_factory_instance() -> None:
    factory = ToolSpecFactory()
    tool = FunctionTool(name="alpha", description="Alpha tool", tool_id="alpha", func=_identity)

    first = factory.create_all(agent_id="agent-1", tool=tool)
    second = factory.create_all(agent_id="agent-1", tool=tool)

    assert first[-1] is second[-1]


def test_tool_spec_factory_reset_cache_returns_fresh_spec_instances() -> None:
    factory = ToolSpecFactory()
    tool = FunctionTool(name="alpha", description="Alpha tool", tool_id="alpha", func=_identity)

    first = factory.create(agent_id="agent-1", tool=tool)
    factory.reset_cache()
    second = factory.create(agent_id="agent-1", tool=tool)

    assert first is not second


def test_tool_spec_factory_creates_agent_tool_with_target_agent_metadata() -> None:
    factory = ToolSpecFactory()
    tool = AgentTool(
        name="delegate",
        description="Delegate tool",
        tool_id="delegate",
        func=_identity,
        target_agent_id="agent-2",
    )

    spec = factory.create(agent_id="agent-1", tool=tool)

    assert spec.tool_type == "agent"
    assert spec.metadata["target_agent_id"] == "agent-2"


def test_tool_spec_factory_creates_model_tool_specs() -> None:
    factory = ToolSpecFactory()
    tool = ModelTool(name="payload", description="Payload model", model=_Payload)

    spec = factory.create(agent_id="agent-1", tool=tool)

    assert spec.tool_type == "model"
    assert spec.name == "payload"
    assert "value" in spec.args_schema["properties"]


def test_nested_pydantic_models_do_not_inherit_always_execute_or_final_tool() -> None:
    """Nested Pydantic models referenced by a parent tool's schema (e.g. a list-item
    type inside a `failures: list[Failure]` field) are NOT independently schedulable
    tools. They must be flattened into ToolSpecs WITHOUT inheriting the parent's
    `always_execute=True` or `final_tool=True` flags.

    Without this guard, registering one tool with `always_execute=True` would
    silently flag every nested schema as required, polluting the orchestrator's
    `always_execute_tools` state with phantom names that the LLM cannot schedule
    (they have no agent invocation surface), triggering false re-prompt loops
    in the assignment_agent's coverage validator.
    """

    class _Inner(BaseModel):
        kind: str
        message: str

    class _Outer(BaseModel):
        target: str
        items: list[_Inner]

    factory = ToolSpecFactory()
    tool = ModelTool(
        name="outer_report",
        description="Outer report",
        model=_Outer,
    )

    specs = factory.create_all(
        agent_id="agent-1",
        tool=tool,
        always_execute=True,
        final_tool=True,
    )

    by_name = {spec.name: spec for spec in specs}
    assert "outer_report" in by_name, "Outer model must be registered"
    assert by_name["outer_report"].always_execute is True
    assert by_name["outer_report"].final_tool is True

    # Inner nested model should be present (for schema $defs purposes) but
    # MUST NOT carry the parent's always_execute/final_tool flags.
    nested_specs = [spec for spec in specs if spec.name != "outer_report"]
    for nested in nested_specs:
        assert nested.always_execute is False, (
            f"Nested model {nested.name!r} inherited always_execute=True from "
            f"parent. This causes phantom 'missing always_execute tool' "
            f"validator warnings because nested models are schema definitions, "
            f"not standalone scheduable tools."
        )
        assert nested.final_tool is False, (
            f"Nested model {nested.name!r} inherited final_tool=True from parent."
        )


def test_tool_spec_factory_creates_mcp_tool_spec_with_metadata() -> None:
    factory = ToolSpecFactory()
    tool = McpTool(
        name="remote_search",
        description="Remote search",
        tool_id="mcp-1",
        server_name="search-server",
        mcp_tool_name="search",
        args_schema={"type": "object", "properties": {"query": {"type": "string"}}},
        default_args={"limit": 10},
        output_schema={"type": "object"},
        annotations={"scope": "read"},
    )

    spec = factory.create(agent_id="agent-1", tool=tool, final_tool=True)

    assert spec.tool_type == "mcp"
    assert spec.final_tool is True
    assert spec.metadata == {
        "mcp_server": "search-server",
        "mcp_tool_name": "search",
        "default_args": {"limit": 10},
        "output_schema": {"type": "object"},
        "annotations": {"scope": "read"},
    }


def test_tool_spec_factory_rejects_unsupported_tool_types() -> None:
    factory = ToolSpecFactory()
    tool = _UnsupportedTool(name="unsupported", description="Unsupported tool")

    with pytest.raises(ValueError, match="Unsupported tool type"):
        factory.create_all(agent_id="agent-1", tool=tool)


def test_tool_spec_factory_create_raises_when_create_all_returns_no_specs(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    factory = ToolSpecFactory()
    tool = FunctionTool(name="alpha", description="Alpha tool", tool_id="alpha", func=_identity)

    def _return_no_specs(**_: Any) -> list[Any]:
        return []

    monkeypatch.setattr(factory, "create_all", _return_no_specs)

    with pytest.raises(ValueError, match="No ToolSpecs could be created"):
        factory.create(agent_id="agent-1", tool=tool)
