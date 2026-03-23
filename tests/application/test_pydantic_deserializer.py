from __future__ import annotations

from typing import Any

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

    def debug(self, message: str, *args: Any) -> None:
        self.debug_calls.append(message % args if args else message)

    def warning(self, message: str, *args: Any) -> None:
        self.warning_calls.append(message % args if args else message)


def _func(model: _Model, items: list[_Model], raw: _Model | dict[str, Any]) -> None:
    return None


def test_pydantic_deserializer_converts_dicts() -> None:
    deserializer = PydanticDeserializer(logger=_Logger())

    args = {
        "model": {"value": 1},
        "items": [{"value": 2}],
        "raw": {"value": 3},
    }

    result = deserializer.deserialize_args(_func, args)

    assert isinstance(result["model"], _Model)
    assert result["model"].value == 1
    assert isinstance(result["items"][0], _Model)
    assert isinstance(result["raw"], _Model)


def test_pydantic_deserializer_falls_back_on_invalid() -> None:
    logger = _Logger()
    deserializer = PydanticDeserializer(logger=logger)

    args = {
        "model": {"value": "bad"},
        "items": [{"value": "bad"}],
        "raw": {"value": "bad"},
    }

    result = deserializer.deserialize_args(_func, args)

    assert isinstance(result["model"], dict)
    assert isinstance(result["items"][0], dict)
    assert isinstance(result["raw"], dict)
    assert logger.debug_calls
