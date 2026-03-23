from __future__ import annotations

from typing import Any

import pytest
from pydantic import BaseModel, Field

from maivn._internal.core.tool_specs.schema_builder import SchemaBuilder
from maivn._internal.utils.decorators import depends_on_private_data


class Child(BaseModel):
    value: int


class Parent(BaseModel):
    child: Child
    children: list[Child]
    child_specs: dict[str, Any] = Field(..., description="calculated results")


def calculate_child() -> dict[str, Any]:
    return {}


def test_schema_builder_creates_function_schema_with_dependency() -> None:
    builder = SchemaBuilder()

    @depends_on_private_data("secret", "secret")
    def tool(secret: str, count: int) -> int:
        return count

    schema = builder.create_from_function(tool, tool_id="tool-1")

    assert schema["tool_id"] == "tool-1"
    assert schema["properties"]["secret"]["type"] == "data_dependency"
    assert schema["properties"]["count"]["type"] == "integer"
    assert schema["return_type"]["type"] == "integer"


def test_schema_builder_rejects_missing_annotation() -> None:
    builder = SchemaBuilder()

    def tool(missing):
        return missing

    with pytest.raises(ValueError):
        builder.create_from_function(tool, tool_id="tool-2")


def test_schema_builder_creates_model_schema_with_dependencies() -> None:
    builder = SchemaBuilder()
    builder.set_function_tools([calculate_child])

    schema = builder.create_from_model(Parent, tool_id="parent-tool")

    child_schema = schema["properties"]["child"]
    assert child_schema["type"] == "tool_dependency"
    assert child_schema["tool_type"] == "model"

    list_schema = schema["properties"]["children"]
    assert list_schema["type"] == "array"
    assert list_schema["items"]["type"] == "tool_dependency"

    child_specs_schema = schema["properties"]["child_specs"]
    assert child_specs_schema["type"] == "tool_dependency"
    assert child_specs_schema["tool_type"] == "func"
