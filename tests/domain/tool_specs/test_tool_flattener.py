from __future__ import annotations

from pydantic import BaseModel

from maivn._internal.core.tool_specs.flattener import ToolFlattener


class Simple(BaseModel):
    value: int


class Alpha(BaseModel):
    value: int


class Beta(BaseModel):
    enabled: bool


class Container(BaseModel):
    union_field: Alpha | Beta
    optional_field: Alpha | None = None


def simple_tool(data: Simple) -> str:
    return f"{data.value}"


def test_flatten_function_tool_includes_model_specs() -> None:
    flattener = ToolFlattener()

    specs = flattener.flatten_function_tool(simple_tool, agent_id="agent-1")

    tool_types = {spec.tool_type for spec in specs}
    assert tool_types == {"func", "model"}


def test_flatten_model_tool_merges_metadata() -> None:
    flattener = ToolFlattener()

    first = flattener.flatten_model_tool(Simple, agent_id="agent-1")[0]
    second = flattener.flatten_model_tool(
        Simple,
        agent_id="agent-1",
        name="Renamed",
        description="Updated description",
        always_execute=True,
        final_tool=True,
        tags=["alpha", "beta"],
    )[0]

    assert first is second
    assert second.name == "Renamed"
    assert second.description == "Updated description"
    assert second.always_execute is True
    assert second.final_tool is True
    assert set(second.tags) == {"alpha", "beta"}


def test_extract_tool_dependencies_recurses() -> None:
    schema = {
        "properties": {
            "direct": {
                "type": "tool_dependency",
                "tool_id": "t1",
                "tool_name": "tool-1",
                "tool_type": "func",
            },
            "array": {
                "type": "array",
                "items": {
                    "type": "tool_dependency",
                    "tool_id": "t2",
                    "tool_name": "tool-2",
                    "tool_type": "func",
                },
            },
            "map": {
                "type": "object",
                "additionalProperties": {
                    "type": "tool_dependency",
                    "tool_id": "t3",
                    "tool_name": "tool-3",
                    "tool_type": "func",
                },
            },
            "union": {
                "anyOf": [
                    {
                        "type": "tool_dependency",
                        "tool_id": "t4",
                        "tool_name": "tool-4",
                        "tool_type": "func",
                    },
                    {"type": "string"},
                ]
            },
        }
    }

    deps = ToolFlattener.extract_tool_dependencies(schema)
    by_id = {dep["tool_id"]: dep for dep in deps}

    assert by_id["t1"]["property_name"] == ""
    assert by_id["t2"]["property_name"] == "[]"
    assert by_id["t3"]["property_name"] == "[*]"
    assert by_id["t4"]["property_name"] == ""


def test_flatten_model_tool_resolves_union_and_optional_variants() -> None:
    flattener = ToolFlattener()

    specs = flattener.flatten_model_tool(Container, agent_id="agent-1")
    container = next(spec for spec in specs if spec.name == "Container")

    union_variants = container.args_schema["properties"]["union_field"]["anyOf"]
    optional_variants = container.args_schema["properties"]["optional_field"]["anyOf"]

    assert {variant["tool_name"] for variant in union_variants} == {"Alpha", "Beta"}
    assert optional_variants[0]["tool_name"] == "Alpha"
    assert optional_variants[1] == {"type": "null"}
