from __future__ import annotations

from typing import Any, TypedDict

from maivn_shared import ToolCall

# MARK: Tool Event Typings

ToolCallPayload = ToolCall


class ToolEventValue(TypedDict, total=False):
    tool_calls: list[ToolCallPayload]
    tool_call: ToolCallPayload
    barrier: bool
    task_list: list[Any]
    batch_id: str


class ToolEventPayload(TypedDict, total=False):
    id: str
    value: ToolEventValue


class UpdateEventPayload(TypedDict, total=False):
    expected_results: int
    action_type: str
    action_id: str
    action_name: str
    status: str
    error: str
    result: Any
    assistant_id: str
    streaming_content: str
