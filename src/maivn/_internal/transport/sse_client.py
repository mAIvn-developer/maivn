"""Server-Sent Events parsing for the mAIvn SDK.

Adapted from the v1 urllib SSE parser. The v2 SDK consumes SSE
frames over httpx and exposes typed ``StreamEvent`` records.
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING, Any, TypeAlias, cast

from maivn._internal.models import StreamEvent

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

DEFAULT_EVENT_NAME = 'message'
EVENT_FIELD_PREFIX = 'event:'
DATA_FIELD_PREFIX = 'data:'
ID_FIELD_PREFIX = 'id:'

JsonObject: TypeAlias = dict[str, Any]


class SseParseError(ValueError):
    """Raised when an SSE frame cannot be parsed into a stream event."""

    def __init__(self, reason: str) -> None:
        """Build the parse error."""
        super().__init__(reason)


def parse_sse_frame(frame: bytes | str) -> StreamEvent:
    """Parse one complete SSE frame into a typed SDK stream event."""
    text = frame.decode('utf-8', errors='replace') if isinstance(frame, bytes) else frame
    event_id: int | None = None
    event_type = DEFAULT_EVENT_NAME
    data_lines: list[str] = []

    for line in text.replace('\r\n', '\n').replace('\r', '\n').split('\n'):
        if line.startswith(ID_FIELD_PREFIX):
            event_id = _parse_event_id(line)
        elif line.startswith(EVENT_FIELD_PREFIX):
            event_type = line.split(':', 1)[1].strip() or DEFAULT_EVENT_NAME
        elif line.startswith(DATA_FIELD_PREFIX):
            data_lines.append(line.split(':', 1)[1].strip())

    if event_id is None:
        message = 'SSE frame is missing an id field'
        raise SseParseError(message)

    data = '\n'.join(data_lines)
    return StreamEvent(
        position=event_id,
        event_type=event_type,
        data=_parse_data(data),
    )


async def iter_sse_events(lines: AsyncIterator[str]) -> AsyncIterator[StreamEvent]:
    """Yield stream events from an async iterator of decoded SSE lines."""
    frame_lines: list[str] = []
    async for line in lines:
        if line.startswith(':'):
            continue
        if line:
            frame_lines.append(line)
            continue
        if frame_lines:
            yield parse_sse_frame('\n'.join(frame_lines) + '\n\n')
            frame_lines.clear()
    if frame_lines:
        yield parse_sse_frame('\n'.join(frame_lines) + '\n\n')


def _parse_event_id(line: str) -> int:
    raw_position = line.split(':', 1)[1].strip()
    if not raw_position.isdecimal():
        message = 'SSE id field must be a non-negative integer'
        raise SseParseError(message)
    return int(raw_position)


def _parse_data(data: str) -> JsonObject:
    if not data:
        return {}
    try:
        value = json.loads(data)
    except json.JSONDecodeError as exc:
        message = 'SSE data field must contain a JSON object'
        raise SseParseError(message) from exc
    if not isinstance(value, dict):
        message = 'SSE data field must contain a JSON object'
        raise SseParseError(message)
    return cast('JsonObject', value)


__all__ = ['SseParseError', 'iter_sse_events', 'parse_sse_frame']
