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
