# pyright: strict
from __future__ import annotations

from typing import TypeAlias, cast

from pydantic import BaseModel, JsonValue

from maivn._internal.core.tool_specs.flattener import ToolFlattener
from maivn._internal.utils.decorators import depends_on_private_data

JsonObject: TypeAlias = dict[str, JsonValue]


def _obj(value: JsonValue | object) -> JsonObject:
    """Cast a JsonValue/object to JsonObject after a runtime check."""
    assert isinstance(value, dict)
    return cast(JsonObject, value)


def _list(value: JsonValue | object) -> list[JsonValue]:
    """Cast a JsonValue/object to a list after a runtime check."""
    assert isinstance(value, list)
    return cast(list[JsonValue], value)


class Simple(BaseModel):
    value: int


class Alpha(BaseModel):
    value: int


class Beta(BaseModel):
    enabled: bool


class Container(BaseModel):
    union_field: Alpha | Beta
    optional_field: Alpha | None = None
    alpha_items: list[Alpha]
    alpha_map: dict[str, Alpha]


class VehiclePowertrain(BaseModel):
    engine: str


class VehicleChassis(BaseModel):
    frame: str


class VehicleSpec(BaseModel):
    powertrain: VehiclePowertrain
    chassis: VehicleChassis


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
    assert set(second.tags or []) == {"alpha", "beta"}


def test_extract_tool_dependencies_recurses() -> None:
    schema: JsonObject = {
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
    by_id: dict[JsonValue, JsonObject] = {dep["tool_id"]: dep for dep in deps}

    assert by_id["t1"]["property_name"] == ""
    assert by_id["t2"]["property_name"] == "[]"
    assert by_id["t3"]["property_name"] == "[*]"
    assert by_id["t4"]["property_name"] == ""


def test_flatten_model_tool_resolves_union_and_optional_variants() -> None:
    flattener = ToolFlattener()

    specs = flattener.flatten_model_tool(Container, agent_id="agent-1")
    container = next(spec for spec in specs if spec.name == "Container")

    args_schema = _obj(container.args_schema)
    properties = _obj(args_schema["properties"])
    union_variants = _list(_obj(properties["union_field"])["anyOf"])
    optional_variants = _list(_obj(properties["optional_field"])["anyOf"])
    array_items = _obj(properties["alpha_items"])["items"]
    map_values = _obj(properties["alpha_map"])["additionalProperties"]

    assert {cast(str, _obj(variant)["$ref"]) for variant in union_variants} == {
        "#/$defs/Alpha",
        "#/$defs/Beta",
    }
    assert _obj(optional_variants[0])["$ref"] == "#/$defs/Alpha"
    assert optional_variants[1] == {"type": "null"}
    assert _obj(array_items)["$ref"] == "#/$defs/Alpha"
    assert _obj(map_values)["$ref"] == "#/$defs/Alpha"
    assert "$defs" in args_schema
    assert set(_obj(args_schema["$defs"])) == {"Alpha", "Beta"}
    assert {spec.name for spec in specs if spec.schema_only} == {"Alpha", "Beta"}


def test_flatten_model_tool_keeps_nested_private_data_dependency_on_schema_only_spec() -> None:
    @depends_on_private_data("manufacturer", "manufacturer")
    class NestedMotor(BaseModel):
        manufacturer: str
        max_power_w: float

    class NestedRobot(BaseModel):
        motor: NestedMotor

    flattener = ToolFlattener()

    specs = flattener.flatten_model_tool(NestedRobot, agent_id="agent-1")
    robot = next(spec for spec in specs if spec.name == "NestedRobot")
    motor = next(spec for spec in specs if spec.name == "NestedMotor")

    args_schema = _obj(robot.args_schema)
    properties = _obj(args_schema["properties"])
    motor_prop = _obj(properties["motor"])
    motor_schema = _obj(motor.args_schema)
    motor_properties = _obj(motor_schema["properties"])

    assert motor_prop["type"] == "tool_dependency"
    assert motor_prop["tool_name"] == "NestedMotor"
    assert motor.schema_only is True
    assert _obj(motor_properties["manufacturer"])["type"] == "data_dependency"
    assert "manufacturer" not in _list(motor_schema["required"])


def test_flatten_model_tool_emits_first_level_model_dependencies() -> None:
    flattener = ToolFlattener()

    specs = flattener.flatten_model_tool(
        VehicleSpec,
        agent_id="agent-1",
        final_tool=True,
    )
    vehicle = next(spec for spec in specs if spec.name == "VehicleSpec")

    args_schema = _obj(vehicle.args_schema)
    properties = _obj(args_schema["properties"])

    assert vehicle.final_tool is True
    assert vehicle.schema_only is False
    assert _obj(properties["powertrain"])["tool_name"] == "VehiclePowertrain"
    assert _obj(properties["powertrain"])["type"] == "tool_dependency"
    assert _obj(properties["chassis"])["tool_name"] == "VehicleChassis"
    assert _obj(properties["chassis"])["type"] == "tool_dependency"
    assert {spec.name for spec in specs if spec.schema_only} == {
        "VehiclePowertrain",
        "VehicleChassis",
    }
