# pyright: strict
"""Tests for ``BasicToolExecutionService`` signature-based arg coercion.

When a function tool declares a parameter typed with a Pydantic model
(e.g. ``def optimize_vehicle(sensor_data: VehicleState, ...)``) the LLM
hands the framework a raw dict for that argument — JSON-shaped data
deserialized from the model's tool call. Without coercion, the function
body's natural attribute access (``sensor_data.speed_kmh``) blows up
with ``'dict' object has no attribute 'speed_kmh'``.

The fix in ``_coerce_args_to_signature`` introspects the callable's
signature and runs each value through ``pydantic.TypeAdapter`` so dicts
become Pydantic instances, lists-of-dicts become lists-of-instances,
etc. These tests lock the behavior in place.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import cast

from pydantic import BaseModel, JsonValue

from maivn._internal.core.application_services.tool_execution.basic_tool_execution_service import (
    BasicToolExecutionService,
)
from maivn._internal.core.entities import FunctionTool

# MARK: - Fixtures


class _EngineMetrics(BaseModel):
    rpm: int
    throttle: float


class _SensorReading(BaseModel):
    sensor_id: str
    value: float


class _VehicleState(BaseModel):
    speed_kmh: float
    engine: _EngineMetrics
    sensors: list[_SensorReading]


def _registered_tool(func: Callable[..., object], *, tool_id: str = "tool-x") -> FunctionTool:
    return FunctionTool(name=tool_id, description="test", tool_id=tool_id, func=func)


def _service_with(tool: FunctionTool) -> BasicToolExecutionService:
    service = BasicToolExecutionService()
    service.rebuild_index([tool])
    return service


# MARK: - Coercion


def test_function_tool_coerces_dict_to_pydantic_model_param() -> None:
    """Raw dict from LLM is constructed into the declared Pydantic model."""

    def optimize(sensor_data: _VehicleState) -> str:
        # Attribute access on a Pydantic instance — would TypeError on a dict.
        return f"{sensor_data.speed_kmh}-{sensor_data.engine.rpm}-{len(sensor_data.sensors)}"

    service = _service_with(_registered_tool(optimize))
    result = service.execute_tool_call(
        "tool-x",
        {
            "sensor_data": {
                "speed_kmh": 60.0,
                "engine": {"rpm": 2800, "throttle": 45.0},
                "sensors": [
                    {"sensor_id": "CAN0", "value": 78.5},
                    {"sensor_id": "TEMP-001", "value": 92.3},
                ],
            }
        },
    )
    assert result == "60.0-2800-2"


def test_function_tool_coerces_list_of_dicts_to_list_of_models() -> None:
    """``list[Model]`` annotations get item-wise coercion."""

    def collect(readings: list[_SensorReading]) -> list[float]:
        return [r.value for r in readings]

    service = _service_with(_registered_tool(collect))
    result = service.execute_tool_call(
        "tool-x",
        {
            "readings": [
                {"sensor_id": "a", "value": 1.0},
                {"sensor_id": "b", "value": 2.5},
            ]
        },
    )
    assert result == [1.0, 2.5]


def test_function_tool_preserves_already_correct_instances() -> None:
    """Values that are already the right type pass through unchanged."""
    captured: list[_EngineMetrics] = []

    def consume(engine: _EngineMetrics) -> None:
        captured.append(engine)

    instance = _EngineMetrics(rpm=3000, throttle=50.0)
    service = _service_with(_registered_tool(consume))
    # The signature is ``dict[str, JsonValue]`` but the runtime accepts a real
    # Pydantic instance for the pass-through path; double-cast keeps the
    # contract narrow while exercising the preservation behavior.
    args_with_instance = cast(dict[str, JsonValue], cast(object, {"engine": instance}))
    _ = service.execute_tool_call("tool-x", args_with_instance)

    assert captured == [instance]


def test_function_tool_preserves_scalar_params_with_annotations() -> None:
    """Scalars match their primitive annotation; no spurious coercion."""

    def greet(name: str, count: int) -> str:
        return f"{name}-{count}"

    service = _service_with(_registered_tool(greet))
    assert service.execute_tool_call("tool-x", {"name": "ada", "count": 3}) == "ada-3"


def test_function_tool_falls_back_to_raw_value_when_coercion_fails() -> None:
    """If TypeAdapter rejects the value, leave it alone and let the function decide.

    Keeps the coercion opportunistic — calls that previously worked with
    raw dicts don't suddenly start failing because of a strict-validation
    mismatch in the framework.
    """

    def accept(payload: _EngineMetrics) -> dict[str, object]:
        # Function tolerates a dict directly — would have worked before
        # the coercion change. After the change, coercion FAILS for this
        # incomplete dict (missing 'throttle'), so it falls through and
        # ``payload`` is the raw dict at runtime, not an ``_EngineMetrics``.
        runtime_payload = cast(object, payload)
        if isinstance(runtime_payload, _EngineMetrics):
            return runtime_payload.model_dump()
        return cast(dict[str, object], runtime_payload)

    service = _service_with(_registered_tool(accept))
    result = service.execute_tool_call("tool-x", {"payload": {"rpm": 1500}})
    # Coercion would have raised on missing 'throttle'; we got the raw dict.
    assert result == {"rpm": 1500}


def test_function_tool_skips_params_without_annotation() -> None:
    """Untyped parameters get no coercion (no annotation to drive it)."""

    def passthrough(value: object) -> object:
        return value

    service = _service_with(_registered_tool(passthrough))
    result = service.execute_tool_call(
        "tool-x", {"value": {"arbitrary": "shape", "nested": [1, 2, 3]}}
    )
    assert result == {"arbitrary": "shape", "nested": [1, 2, 3]}
