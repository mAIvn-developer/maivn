from __future__ import annotations

import pytest

from maivn._internal.core.application_services.tool_execution import (
    tool_execution_service,
)
from maivn._internal.core.entities import FunctionTool

ToolExecutionService = tool_execution_service.ToolExecutionService


def _sample_tool(value: int = 1) -> int:
    return value


def test_rebuild_index_allows_same_tool_id_and_name() -> None:
    tool = FunctionTool(
        name="tool-123",
        description="test tool",
        tool_id="tool-123",
        func=_sample_tool,
    )
    service = ToolExecutionService()
    service.rebuild_index([tool])

    assert service.resolve_tool("tool-123") is tool


def test_rebuild_index_rejects_duplicate_identifier_across_tools() -> None:
    tool_a = FunctionTool(
        name="shared-name",
        description="first tool",
        tool_id="tool-a",
        func=_sample_tool,
    )
    tool_b = FunctionTool(
        name="shared-name",
        description="second tool",
        tool_id="tool-b",
        func=_sample_tool,
    )
    service = ToolExecutionService()

    with pytest.raises(ValueError):
        service.rebuild_index([tool_a, tool_b])
