# pyright: strict
from __future__ import annotations

from typing import cast

from maivn_shared import ToolSpec

from maivn._internal.core.application_services.state_compilation.dependency_updates import (
    deduplicate_tool_specs,
    update_tool_dependency_references,
)
from maivn._internal.core.entities import FunctionTool


def _func() -> str:
    return "ok"


def test_update_tool_dependency_references_canonicalizes_name_refs() -> None:
    tool = FunctionTool(name="tool", description="t", tool_id="uuid-tool", func=_func)

    spec = ToolSpec(
        tool_id="spec",
        agent_id="agent",
        name="spec",
        description="spec",
        tags=[],
        tool_type="func",
        args_schema={
            "properties": {
                "dep": {
                    "type": "tool_dependency",
                    "tool_id": "tool",
                    "tool_name": "unknown",
                },
                "nested": {
                    "type": "array",
                    "items": {
                        "type": "tool_dependency",
                        "tool_id": "tool",
                        "tool_name": "unknown",
                    },
                },
            }
        },
    )

    update_tool_dependency_references([spec], [tool])

    args_schema = cast(dict[str, object], spec.args_schema)
    properties = cast(dict[str, dict[str, object]], args_schema["properties"])
    assert properties["dep"]["tool_id"] == "uuid-tool"
    assert properties["dep"]["tool_name"] == "tool"
    nested_items = cast(dict[str, object], properties["nested"]["items"])
    assert nested_items["tool_id"] == "uuid-tool"
    assert nested_items["tool_name"] == "tool"


def test_update_tool_dependency_references_updates_return_type() -> None:
    tool = FunctionTool(name="producer", description="t", tool_id="uuid-producer", func=_func)
    spec = ToolSpec(
        tool_id="spec",
        agent_id="agent",
        name="spec",
        description="spec",
        tags=[],
        tool_type="func",
        args_schema={
            "properties": {},
            "return_type": {
                "type": "tool_dependency",
                "tool_id": "producer",
                "tool_name": "unknown",
            },
        },
    )

    update_tool_dependency_references([spec], [tool])

    args_schema = cast(dict[str, object], spec.args_schema)
    return_type = cast(dict[str, object], args_schema["return_type"])
    assert return_type["tool_id"] == "uuid-producer"
    assert return_type["tool_name"] == "producer"


def test_update_tool_dependency_references_rejects_ambiguous_name_ref() -> None:
    tool_a = FunctionTool(name="dup", description="a", tool_id="uuid-a", func=_func)
    tool_b = FunctionTool(name="dup", description="b", tool_id="uuid-b", func=_func)
    spec = ToolSpec(
        tool_id="spec",
        agent_id="agent",
        name="spec",
        description="spec",
        tags=[],
        tool_type="func",
        args_schema={
            "properties": {
                "dep": {
                    "type": "tool_dependency",
                    "tool_id": "dup",
                },
            }
        },
    )

    try:
        update_tool_dependency_references([spec], [tool_a, tool_b])
    except ValueError as exc:
        assert "Ambiguous tool dependency reference 'dup'" in str(exc)
    else:
        raise AssertionError("expected duplicate tool name to raise")


def test_deduplicate_tool_specs_preserves_order() -> None:
    spec_a = ToolSpec(
        tool_id="a",
        agent_id="agent",
        name="a",
        description="a",
        tags=[],
        tool_type="func",
        args_schema={},
    )
    spec_b = ToolSpec(
        tool_id="b",
        agent_id="agent",
        name="b",
        description="b",
        tags=[],
        tool_type="func",
        args_schema={},
    )
    spec_dup = ToolSpec(
        tool_id="a",
        agent_id="agent",
        name="a",
        description="a",
        tags=[],
        tool_type="func",
        args_schema={},
    )

    deduped = deduplicate_tool_specs([spec_a, spec_b, spec_dup])

    assert [spec.tool_id for spec in deduped] == ["a", "b"]
