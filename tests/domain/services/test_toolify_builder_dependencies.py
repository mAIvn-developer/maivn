from __future__ import annotations

from typing import Any

import pytest

from maivn import (
    PrivateData,
    compose_artifact_policy,
    depends_on_await_for,
    depends_on_private_data,
    depends_on_reevaluate,
    depends_on_tool,
)
from maivn._internal.api.base_scope import BaseScope
from maivn._internal.core.tool_specs.factory import ToolSpecFactory


class DummyScope(BaseScope):
    pass


def other_tool_func() -> str:
    return "ok"


def test_scope_rejects_duplicate_private_data_names_for_private_data_objects() -> None:
    with pytest.raises(ValueError, match='duplicate private_data name: "customer_email"'):
        DummyScope(
            name="test",
            private_data=[
                PrivateData(name="customer_email", value="alice@example.com"),
                PrivateData(name="customer_email", value="bob@example.com"),
            ],
        )


def test_scope_rejects_duplicate_private_data_names_for_dict_entries() -> None:
    with pytest.raises(ValueError, match='duplicate private_data name: "customer_email"'):
        DummyScope(
            name="test",
            private_data=[
                {"name": "customer_email", "value": "alice@example.com"},
                {"name": "customer_email", "value": "bob@example.com"},
            ],
        )


def test_toolify_builder_registers_dependencies() -> None:
    scope = DummyScope(name="test")

    @scope.toolify(description="Other tool")
    def other_tool() -> str:
        return other_tool_func()

    @(
        scope.toolify(description="Builder tool")
        .depends_on_tool(other_tool, "arg_a")
        .depends_on_private_data("data_key", "arg_b")
    )
    def builder_tool(arg_a: Any, arg_b: Any) -> str:
        return f"{arg_a}:{arg_b}"

    tool_id = builder_tool.tool_id
    tool = scope.get_tool(tool_id)
    assert tool is not None

    deps = list(getattr(tool, "dependencies", []))

    assert any(
        getattr(dep, "dependency_type", None) == "tool"
        and getattr(dep, "arg_name", None) == "arg_a"
        for dep in deps
    )

    assert any(
        getattr(dep, "dependency_type", None) == "data"
        and getattr(dep, "arg_name", None) == "arg_b"
        for dep in deps
    )


def test_toolify_builder_matches_decorator_style() -> None:
    scope = DummyScope(name="test")

    @scope.toolify(description="Other tool")
    def other_tool() -> str:
        return other_tool_func()

    @(
        scope.toolify(description="Builder tool")
        .depends_on_tool(other_tool, "arg_a")
        .depends_on_private_data("data_key", "arg_b")
    )
    def builder_tool(arg_a: Any, arg_b: Any) -> str:
        return f"{arg_a}:{arg_b}"

    @depends_on_private_data("data_key", "arg_b")
    @depends_on_tool(other_tool, "arg_a")
    @scope.toolify(description="Decorator tool")
    def decorator_tool(arg_a: Any, arg_b: Any) -> str:
        return f"{arg_a}:{arg_b}"

    builder_id = builder_tool.tool_id
    decorator_id = decorator_tool.tool_id

    builder = scope.get_tool(builder_id)
    decorator = scope.get_tool(decorator_id)

    assert builder is not None
    assert decorator is not None

    builder_deps = [d.model_dump(mode="json") for d in getattr(builder, "dependencies", [])]
    decorator_deps = [d.model_dump(mode="json") for d in getattr(decorator, "dependencies", [])]

    assert builder_deps == decorator_deps


def test_toolify_builder_registers_execution_controls_in_metadata() -> None:
    scope = DummyScope(name="test")

    @scope.toolify(description="Other tool")
    def other_tool() -> str:
        return other_tool_func()

    @(
        scope.toolify(description="Builder tool")
        .depends_on_await_for(other_tool, timing="after", instance_control="all")
        .depends_on_reevaluate(other_tool, timing="before", instance_control="each")
    )
    def builder_tool() -> str:
        return "ok"

    tool = scope.get_tool(builder_tool.tool_id)
    assert tool is not None
    assert getattr(tool, "dependencies", []) == []

    execution_controls = tool.metadata.get("execution_controls", {})
    assert execution_controls["await_for"][0]["tool_name"] == "other_tool"
    assert execution_controls["await_for"][0]["timing"] == "after"
    assert execution_controls["await_for"][0]["instance_control"] == "all"
    assert execution_controls["reevaluate"][0]["tool_name"] == "other_tool"
    assert execution_controls["reevaluate"][0]["timing"] == "before"
    assert execution_controls["reevaluate"][0]["instance_control"] == "each"

    spec = ToolSpecFactory().create(agent_id=scope.id, tool=tool)
    spec_controls = spec.metadata.get("execution_controls", {})
    assert spec_controls == execution_controls


def test_toolify_builder_execution_controls_match_decorator_style() -> None:
    scope = DummyScope(name="test")

    @scope.toolify(description="Other tool")
    def other_tool() -> str:
        return other_tool_func()

    @(
        scope.toolify(description="Builder tool")
        .depends_on_await_for(other_tool, timing="after", instance_control="each")
        .depends_on_reevaluate(other_tool, timing="after", instance_control="all")
    )
    def builder_tool() -> str:
        return "ok"

    @depends_on_reevaluate(other_tool, timing="after", instance_control="all")
    @depends_on_await_for(other_tool, timing="after", instance_control="each")
    @scope.toolify(description="Decorator tool")
    def decorator_tool() -> str:
        return "ok"

    builder = scope.get_tool(builder_tool.tool_id)
    decorator = scope.get_tool(decorator_tool.tool_id)

    assert builder is not None
    assert decorator is not None
    assert builder.metadata.get("execution_controls") == decorator.metadata.get(
        "execution_controls"
    )


def test_toolify_builder_registers_compose_artifact_policy_in_metadata_and_schema() -> None:
    scope = DummyScope(name="test")

    @(
        scope.toolify(description="Builder tool").compose_artifact_policy(
            "query", mode="require", approval="explicit"
        )
    )
    def builder_tool(query: str) -> str:
        return query

    tool = scope.get_tool(builder_tool.tool_id)
    assert tool is not None

    assert tool.metadata.get("arg_policies") == {
        "query": {
            "compose_artifact": {
                "mode": "require",
                "approval": "explicit",
            }
        }
    }

    spec = ToolSpecFactory().create(agent_id=scope.id, tool=tool)
    assert spec.metadata.get("arg_policies") == tool.metadata.get("arg_policies")
    assert spec.args_schema["properties"]["query"]["compose_artifact_policy"] == {
        "mode": "require",
        "approval": "explicit",
    }


def test_toolify_builder_compose_artifact_policy_matches_decorator_style() -> None:
    scope = DummyScope(name="test")

    @(scope.toolify(description="Builder tool").compose_artifact_policy("query", mode="forbid"))
    def builder_tool(query: str) -> str:
        return query

    @compose_artifact_policy("query", mode="forbid")
    @scope.toolify(description="Decorator tool")
    def decorator_tool(query: str) -> str:
        return query

    builder = scope.get_tool(builder_tool.tool_id)
    decorator = scope.get_tool(decorator_tool.tool_id)

    assert builder is not None
    assert decorator is not None
    assert builder.metadata.get("arg_policies") == decorator.metadata.get("arg_policies")
