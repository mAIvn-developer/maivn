from __future__ import annotations

from types import SimpleNamespace
from typing import ForwardRef

from pydantic import BaseModel

from maivn._internal.core.tool_specs.type_utils import (
    extract_nested_models,
    is_pydantic_model,
    resolve_forward_ref,
    safe_resolve_string_type,
)


class ModelA(BaseModel):
    value: int


class ModelB(BaseModel):
    name: str


def test_safe_resolve_string_type_and_forward_ref() -> None:
    globals_dict = {
        "ModelA": ModelA,
        "namespace": SimpleNamespace(Inner=ModelB),
    }

    assert safe_resolve_string_type("ModelA", globals_dict) is ModelA
    assert safe_resolve_string_type("namespace.Inner", globals_dict) is ModelB
    assert safe_resolve_string_type("int", globals_dict) is int
    assert safe_resolve_string_type("Missing", globals_dict) is None

    forward = ForwardRef("ModelA")
    assert resolve_forward_ref(forward, globals_dict) is ModelA


def test_extract_nested_models_and_is_pydantic() -> None:
    assert is_pydantic_model(ModelA) is True
    assert is_pydantic_model("ModelA") is False

    nested = extract_nested_models(list[ModelA] | ModelB)
    assert ModelA in nested
    assert ModelB in nested
