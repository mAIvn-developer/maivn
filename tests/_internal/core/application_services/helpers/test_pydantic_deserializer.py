# pyright: strict
from __future__ import annotations

from typing import cast

from maivn_shared.infrastructure.logging import LoggerProtocol
from pydantic import BaseModel

from maivn._internal.core.application_services.helpers.pydantic_deserializer import (
    PydanticDeserializer,
)


class _Model(BaseModel):
    value: int


class _Logger:
    def __init__(self) -> None:
        self.debug_calls: list[str] = []
        self.warning_calls: list[str] = []

    def debug(self, message: str, *args: object) -> None:
        self.debug_calls.append(message % args if args else message)

    def warning(self, message: str, *args: object) -> None:
        self.warning_calls.append(message % args if args else message)


def _func(model: _Model, items: list[_Model], raw: _Model | dict[str, object]) -> None:
    del model, items, raw  # signature only — used for type-hint inspection
    return None


def _primitive_func(count: int, ratio: float, enabled: bool, bounds: tuple[int, int]) -> None:
    del count, ratio, enabled, bounds  # signature only - used for type-hint inspection
    return None


def _defaulted_primitive_func(count: int = 100, label: str = "default") -> None:
    del count, label  # signature only - used for type-hint inspection
    return None


def test_pydantic_deserializer_converts_dicts() -> None:
    deserializer = PydanticDeserializer(logger=cast(LoggerProtocol, cast(object, _Logger())))

    args: dict[str, object] = {
        "model": {"value": 1},
        "items": [{"value": 2}],
        "raw": {"value": 3},
    }

    result = deserializer.deserialize_args(_func, args)

    model_value = result["model"]
    assert isinstance(model_value, _Model)
    assert model_value.value == 1
    items_value = result["items"]
    assert isinstance(items_value, list)
    assert isinstance(cast(list[object], items_value)[0], _Model)
    assert isinstance(result["raw"], _Model)


def test_pydantic_deserializer_coerces_primitive_annotations() -> None:
    deserializer = PydanticDeserializer(logger=cast(LoggerProtocol, cast(object, _Logger())))

    result = deserializer.deserialize_args(
        _primitive_func,
        {
            "count": "100",
            "ratio": "1.25",
            "enabled": "true",
            "bounds": ["1800", "3200"],
        },
    )

    assert result == {
        "count": 100,
        "ratio": 1.25,
        "enabled": True,
        "bounds": (1800, 3200),
    }


def test_pydantic_deserializer_preserves_invalid_primitive_values() -> None:
    deserializer = PydanticDeserializer(logger=cast(LoggerProtocol, cast(object, _Logger())))

    result = deserializer.deserialize_args(
        _primitive_func,
        {
            "count": "not-an-int",
            "ratio": "bad-float",
            "enabled": "not-bool",
            "bounds": ["1800", "bad"],
        },
    )

    assert result == {
        "count": "not-an-int",
        "ratio": "bad-float",
        "enabled": "not-bool",
        "bounds": ["1800", "bad"],
    }


def test_pydantic_deserializer_omits_invalid_defaulted_primitive_values() -> None:
    deserializer = PydanticDeserializer(logger=cast(LoggerProtocol, cast(object, _Logger())))

    result = deserializer.deserialize_args(
        _defaulted_primitive_func,
        {
            "count": "use the default",
            "label": "explicit",
        },
    )

    assert result == {"label": "explicit"}


def test_pydantic_deserializer_falls_back_on_invalid() -> None:
    logger = _Logger()
    deserializer = PydanticDeserializer(logger=cast(LoggerProtocol, cast(object, logger)))

    args: dict[str, object] = {
        "model": {"value": "bad"},
        "items": [{"value": "bad"}],
        "raw": {"value": "bad"},
    }

    result = deserializer.deserialize_args(_func, args)

    assert isinstance(result["model"], dict)
    items_value = result["items"]
    assert isinstance(items_value, list)
    assert isinstance(cast(list[object], items_value)[0], dict)
    assert isinstance(result["raw"], dict)
    assert logger.debug_calls


def test_pydantic_deserializer_handles_future_annotations_with_cross_refs() -> None:
    """Regression: modules using ``from __future__ import annotations`` carry
    forward-ref annotations. Pydantic models defined there are
    "not fully defined" until ``model_rebuild()`` runs against the module
    namespace. The deserializer must rebuild on demand and pass the model's
    own module globals as the resolution namespace - otherwise instantiation
    silently fails and the function receives a raw ``dict``."""
    import sys
    import types
    from collections.abc import Callable

    module = types.ModuleType("future_annotations_demo")
    sys.modules["future_annotations_demo"] = module
    source = (
        "from __future__ import annotations\n"
        + "from pydantic import BaseModel\n"
        + "class Inner(BaseModel):\n"
        + "    x: int\n"
        + "class Outer(BaseModel):\n"
        + "    inner: Inner\n"
        + "def take(arg: Outer) -> int:\n"
        + "    return arg.inner.x\n"
    )
    try:
        exec(source, module.__dict__)  # noqa: S102 - controlled test snippet builds throwaway module

        deserializer = PydanticDeserializer()
        take_fn = cast("Callable[..., object]", module.__dict__["take"])
        outer_cls = cast(type[BaseModel], module.__dict__["Outer"])
        result = deserializer.deserialize_args(
            take_fn,
            {"arg": {"inner": {"x": 42}}},
        )

        arg_value = result["arg"]
        assert isinstance(arg_value, outer_cls)
        assert take_fn(**result) == 42
    finally:
        _ = sys.modules.pop("future_annotations_demo", None)
