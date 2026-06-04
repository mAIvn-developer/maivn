# pyright: strict
from __future__ import annotations

from typing import TypeAlias, cast

import pytest
from pydantic import JsonValue

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

JsonObject: TypeAlias = dict[str, JsonValue]


class DummyScope(BaseScope):
    pass


def other_tool_func() -> str:
    return "ok"


def _tool_id(fn: object) -> str:
    return cast(str, getattr(fn, "tool_id"))  # noqa: B009 - dynamic attr from decorator metadata


def test_scope_rejects_duplicate_private_data_names_for_private_data_objects() -> None:
    with pytest.raises(ValueError, match='duplicate private_data name: "customer_email"'):
        _ = DummyScope(
            name="test",
            private_data=cast(
                dict[object, object],
                cast(
                    object,
                    [
                        PrivateData(name="customer_email", value="alice@example.com"),
                        PrivateData(name="customer_email", value="bob@example.com"),
                    ],
                ),
            ),
        )


def test_scope_rejects_duplicate_private_data_names_for_dict_entries() -> None:
    with pytest.raises(ValueError, match='duplicate private_data name: "customer_email"'):
        _ = DummyScope(
            name="test",
            private_data=cast(
                dict[object, object],
                cast(
                    object,
                    [
                        {"name": "customer_email", "value": "alice@example.com"},
                        {"name": "customer_email", "value": "bob@example.com"},
                    ],
                ),
            ),
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
    def builder_tool(arg_a: object, arg_b: object) -> str:
        return f"{arg_a}:{arg_b}"

    tool = scope.get_tool(_tool_id(builder_tool))
    assert tool is not None

    deps = list(tool.dependencies)

    assert any(dep.dependency_type == "tool" and dep.arg_name == "arg_a" for dep in deps)

    assert any(dep.dependency_type == "data" and dep.arg_name == "arg_b" for dep in deps)


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
    def builder_tool(arg_a: object, arg_b: object) -> str:
        return f"{arg_a}:{arg_b}"

    @depends_on_private_data("data_key", "arg_b")
    @depends_on_tool(other_tool, "arg_a")
    @scope.toolify(description="Decorator tool")
    def decorator_tool(arg_a: object, arg_b: object) -> str:
        return f"{arg_a}:{arg_b}"

    builder = scope.get_tool(_tool_id(builder_tool))
    decorator = scope.get_tool(_tool_id(decorator_tool))

    assert builder is not None
    assert decorator is not None

    builder_deps = [d.model_dump(mode="json") for d in builder.dependencies]
    decorator_deps = [d.model_dump(mode="json") for d in decorator.dependencies]

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

    tool = scope.get_tool(_tool_id(builder_tool))
    assert tool is not None
    assert tool.dependencies == []

    execution_controls = cast(JsonObject, tool.metadata.get("execution_controls", {}))
    await_for_entries = cast(list[JsonObject], execution_controls["await_for"])
    reevaluate_entries = cast(list[JsonObject], execution_controls["reevaluate"])
    assert await_for_entries[0]["tool_name"] == "other_tool"
    assert await_for_entries[0]["timing"] == "after"
    assert await_for_entries[0]["instance_control"] == "all"
    assert reevaluate_entries[0]["tool_name"] == "other_tool"
    assert reevaluate_entries[0]["timing"] == "before"
    assert reevaluate_entries[0]["instance_control"] == "each"

    spec = ToolSpecFactory().create(agent_id=scope.id, tool=tool)
    assert spec.metadata is not None
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

    builder = scope.get_tool(_tool_id(builder_tool))
    decorator = scope.get_tool(_tool_id(decorator_tool))

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

    tool = scope.get_tool(_tool_id(builder_tool))
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
    assert spec.metadata is not None
    assert spec.metadata.get("arg_policies") == tool.metadata.get("arg_policies")
    args_schema = cast(JsonObject, spec.args_schema)
    properties = cast(JsonObject, args_schema["properties"])
    query_schema = cast(JsonObject, properties["query"])
    assert query_schema["compose_artifact_policy"] == {
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

    builder = scope.get_tool(_tool_id(builder_tool))
    decorator = scope.get_tool(_tool_id(decorator_tool))

    assert builder is not None
    assert decorator is not None
    assert builder.metadata.get("arg_policies") == decorator.metadata.get("arg_policies")
