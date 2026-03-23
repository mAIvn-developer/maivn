from __future__ import annotations

import json

from maivn_shared import AgentDependency, BaseDependency, InterruptDependency, ToolDependency

from maivn._internal.core.utils.dependency_utils import normalize_dependencies


class _CustomDependency(BaseDependency):
    pass


class _NamedDependency:
    def __init__(self, name: str) -> None:
        self.name = name


class _FallbackDependency:
    def __str__(self) -> str:
        return "fallback-value"


class _PydanticLikeDependency:
    def __init__(self, payload: dict[str, object]) -> None:
        self.payload = payload

    def model_dump(self, mode: str = "json") -> dict[str, object]:
        assert mode == "json"
        return self.payload


class _BrokenPydanticLikeDependency:
    def model_dump(self, mode: str = "json") -> dict[str, object]:
        raise RuntimeError("cannot dump")

    def __str__(self) -> str:
        return "broken-model"


def test_normalize_dependencies_returns_empty_list_for_empty_inputs() -> None:
    assert normalize_dependencies(None) == []
    assert normalize_dependencies([]) == []


def test_normalize_dependencies_skips_none_entries_inside_dependency_lists() -> None:
    normalized = normalize_dependencies([None, _FallbackDependency()])

    assert normalized == ["fallback-value"]


def test_normalize_dependencies_extracts_identifiers_from_base_dependencies() -> None:
    dependencies = [
        ToolDependency(arg_name="tool", tool_id="tool-1"),
        AgentDependency(arg_name="agent", agent_id="agent-1"),
    ]

    assert normalize_dependencies(dependencies) == ["tool-1", "agent-1"]


def test_normalize_dependencies_serializes_interrupt_dependency_without_handler() -> None:
    dependency = InterruptDependency(
        arg_name="answer",
        prompt="Provide answer",
        input_handler=lambda prompt: prompt,
    )

    normalized = normalize_dependencies([dependency])

    assert len(normalized) == 1
    payload = json.loads(normalized[0])
    assert payload == {
        "dependency_type": "user",
        "arg_name": "answer",
        "prompt": "Provide answer",
    }


def test_normalize_dependencies_serializes_base_dependency_without_identifier_fields() -> None:
    dependency = _CustomDependency(arg_name="custom", dependency_type="tool")

    normalized = normalize_dependencies([dependency])

    assert normalized == ['{"name":"","dependency_type":"tool","arg_name":"custom"}']


def test_normalize_dependencies_uses_model_dump_for_pydantic_like_objects() -> None:
    normalized = normalize_dependencies([_PydanticLikeDependency({"value": 3})])

    assert normalized == ['{"value":3}']


def test_normalize_dependencies_falls_back_to_string_when_model_dump_fails() -> None:
    normalized = normalize_dependencies([_BrokenPydanticLikeDependency()])

    assert normalized == ["broken-model"]


def test_normalize_dependencies_uses_name_attribute_before_string_fallback() -> None:
    normalized = normalize_dependencies([_NamedDependency("named"), _FallbackDependency()])

    assert normalized == ["named", "fallback-value"]
