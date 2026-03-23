from __future__ import annotations

from maivn_shared import ToolSpec

from maivn._internal.core.application_services.state_compilation.dependency_updates import (
    deduplicate_tool_specs,
    update_tool_dependency_references,
)
from maivn._internal.core.entities import FunctionTool


def _func() -> str:
    return "ok"


def test_update_tool_dependency_references_updates_nested_schema() -> None:
    tool = FunctionTool(name="tool", description="t", tool_id="tool", func=_func)

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

    props = spec.args_schema["properties"]
    assert props["dep"]["tool_name"] == "tool"
    assert props["nested"]["items"]["tool_name"] == "tool"


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
