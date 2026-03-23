from __future__ import annotations

from dataclasses import dataclass

from maivn._internal.core.application_services.state_compilation.tool_normalization import (
    normalize_tools_for_structured_output,
)
from maivn._internal.core.entities import FunctionTool


def _func() -> str:
    return "ok"


def test_normalize_tools_for_structured_output() -> None:
    tool_a = FunctionTool(
        name="a",
        description="a",
        tool_id="a",
        func=_func,
        final_tool=True,
    )
    tool_b = FunctionTool(name="b", description="b", tool_id="b", func=_func)
    structured = FunctionTool(name="structured", description="s", tool_id="s", func=_func)

    normalized = normalize_tools_for_structured_output([tool_a, tool_b], structured)

    assert normalized[-1].name == "structured"
    assert normalized[-1].final_tool is True
    assert all(not tool.final_tool for tool in normalized[:-1])


@dataclass
class _PlainTool:
    name: str
    description: str
    final_tool: bool = False


def test_normalize_tools_handles_plain_objects() -> None:
    plain = _PlainTool(name="plain", description="plain", final_tool=True)
    structured = _PlainTool(name="structured", description="s", final_tool=False)

    normalized = normalize_tools_for_structured_output([plain], structured)

    assert normalized[-1].final_tool is True


class _FakeModel:
    """Fake model class for testing."""

    pass


@dataclass
class _ModelTool:
    name: str
    description: str
    model: type
    final_tool: bool = False


def test_normalize_tools_reuses_existing_tool_with_same_model() -> None:
    """When an existing tool uses the same model class as structured_output,
    the existing tool should be promoted to final_tool=True and no duplicate added.
    """
    existing = _ModelTool(
        name="auto_setup_summary",
        description="existing tool",
        model=_FakeModel,
        final_tool=True,
    )
    structured = _ModelTool(
        name="AutoSetupSummary",
        description="structured output",
        model=_FakeModel,
        final_tool=True,
    )

    normalized = normalize_tools_for_structured_output([existing], structured)

    # Should NOT add duplicate - only 1 tool in result
    assert len(normalized) == 1
    # The existing tool should be the one kept
    assert normalized[0].name == "auto_setup_summary"
    assert normalized[0].final_tool is True


def test_normalize_tools_adds_structured_when_no_model_match() -> None:
    """When no existing tool shares the model class, structured_output is added."""

    class _OtherModel:
        pass

    existing = _ModelTool(
        name="some_tool",
        description="existing tool",
        model=_OtherModel,
        final_tool=False,
    )
    structured = _ModelTool(
        name="StructuredOutput",
        description="structured output",
        model=_FakeModel,
        final_tool=True,
    )

    normalized = normalize_tools_for_structured_output([existing], structured)

    # Should have 2 tools - existing + structured
    assert len(normalized) == 2
    assert normalized[0].name == "some_tool"
    assert normalized[0].final_tool is False
    assert normalized[-1].name == "StructuredOutput"
    assert normalized[-1].final_tool is True
