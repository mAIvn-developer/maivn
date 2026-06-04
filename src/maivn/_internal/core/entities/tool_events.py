# pyright: strict
from __future__ import annotations

from typing import TypedDict

from maivn_shared import ToolCall
from pydantic import JsonValue

# MARK: Tool Event Typings

ToolCallPayload = ToolCall


class ToolEventValue(TypedDict, total=False):
    tool_calls: list[ToolCallPayload]
    tool_call: ToolCallPayload
    barrier: bool
    task_list: list[JsonValue]
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
    result: JsonValue
    assistant_id: str
    streaming_content: str
