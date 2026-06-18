# pyright: strict
from __future__ import annotations

from collections.abc import Callable
from typing import Annotated, TypeAlias, cast

import pytest
from pydantic import BaseModel, Field, JsonValue

from maivn._internal.core.tool_specs.schema_builder import SchemaBuilder
from maivn._internal.utils.decorators import depends_on_private_data

JsonObject: TypeAlias = dict[str, JsonValue]


def _obj(value: JsonValue | object) -> JsonObject:
    """Cast a JsonValue/object to JsonObject after a runtime check."""
    assert isinstance(value, dict)
    return cast(JsonObject, value)


def _prop(schema: JsonValue | object, name: str) -> JsonObject:
    """Return ``schema["properties"][name]`` as a typed JsonObject."""
    properties = _obj(_obj(schema)["properties"])
    return _obj(properties[name])


def _make_unannotated_tool() -> Callable[..., object]:
    """Build a function whose single parameter has no type annotation.

    The SUT raises ``ValueError`` when a parameter lacks an annotation, so the
    test needs an unannotated callable. Writing ``def tool(missing):`` directly
    inside this strict-typed module would itself trip ``reportMissingParameterType``,
    so we forge the function via ``exec`` instead.
    """
    namespace: dict[str, object] = {}
    exec("def _tool(missing):\n    return missing\n", namespace)
    return cast(Callable[..., object], namespace["_tool"])


class Child(BaseModel):
    value: int


class Parent(BaseModel):
    child: Child
    children: list[Child]
    child_specs: dict[str, object] = Field(..., description="calculated results")


def calculate_child() -> dict[str, object]:
    return {}


def test_schema_builder_creates_function_schema_with_dependency() -> None:
    builder = SchemaBuilder()

    @depends_on_private_data("secret", "secret")
    def tool(secret: str, count: int) -> int:
        return count if secret else 0

    schema = _obj(builder.create_from_function(tool, tool_id="tool-1"))

    assert schema["tool_id"] == "tool-1"
    assert _prop(schema, "secret")["type"] == "data_dependency"
    assert _prop(schema, "count")["type"] == "integer"
    assert _obj(schema["return_type"])["type"] == "integer"


def test_schema_builder_rejects_missing_annotation() -> None:
    builder = SchemaBuilder()
    tool = _make_unannotated_tool()

    with pytest.raises(ValueError):
        _ = builder.create_from_function(tool, tool_id="tool-2")


def test_schema_builder_extracts_bare_string_annotated_description() -> None:
    builder = SchemaBuilder()

    def tool(
        vehicle_id: Annotated[str, "A single vehicle ID like 'VAN-103'."],
        count: int,
    ) -> str:
        return f"{vehicle_id}:{count}"

    schema = _obj(builder.create_from_function(tool, tool_id="tool-annotated-str"))

    vehicle_prop = _prop(schema, "vehicle_id")
    assert vehicle_prop["type"] == "string"
    assert vehicle_prop["description"] == "A single vehicle ID like 'VAN-103'."
    # Bare params still produce a type-only schema.
    count_prop = _prop(schema, "count")
    assert count_prop["type"] == "integer"
    assert "description" not in count_prop


def test_schema_builder_passes_through_pydantic_field_constraints() -> None:
    builder = SchemaBuilder()

    def tool(
        vehicle_id: Annotated[
            str,
            Field(description="Vehicle ID.", min_length=1, pattern=r"^VAN-\d+$"),
        ],
        estimated_km: Annotated[int, Field(description="Trip distance.", gt=0, le=10_000)],
    ) -> dict[str, object]:
        return {"vehicle_id": vehicle_id, "estimated_km": estimated_km}

    schema = _obj(builder.create_from_function(tool, tool_id="tool-annotated-field"))

    vehicle_prop = _prop(schema, "vehicle_id")
    assert vehicle_prop["type"] == "string"
    assert vehicle_prop["description"] == "Vehicle ID."
    assert vehicle_prop["minLength"] == 1
    assert vehicle_prop["pattern"] == r"^VAN-\d+$"

    km_prop = _prop(schema, "estimated_km")
    assert km_prop["type"] == "integer"
    assert km_prop["description"] == "Trip distance."
    assert km_prop["exclusiveMinimum"] == 0
    assert km_prop["maximum"] == 10_000


def test_schema_builder_prefers_field_description_over_bare_string() -> None:
    """If both a bare string and a Field(description=...) are present, Field wins."""
    builder = SchemaBuilder()

    def tool(
        x: Annotated[str, "bare", Field(description="from Field")],
    ) -> str:
        return x

    schema = _obj(builder.create_from_function(tool, tool_id="tool-priority"))

    assert _prop(schema, "x")["description"] == "from Field"


def test_schema_builder_creates_model_schema_with_dependencies() -> None:
    builder = SchemaBuilder()
    builder.set_function_tools([calculate_child])

    schema = _obj(builder.create_from_model(Parent, tool_id="parent-tool"))

    child_schema = _prop(schema, "child")
    assert child_schema["type"] == "tool_dependency"
    assert child_schema["tool_type"] == "model"

    list_schema = _prop(schema, "children")
    assert list_schema["type"] == "array"
    assert _obj(list_schema["items"])["$ref"] == "#/$defs/Child"

    child_specs_schema = _prop(schema, "child_specs")
    assert child_specs_schema["type"] == "tool_dependency"
    assert child_specs_schema["tool_type"] == "func"
