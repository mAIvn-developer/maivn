"""Validate terminal messages, retaining identity from their canonical event envelope."""

from __future__ import annotations

from typing import TYPE_CHECKING

from maivn_contracts.messages import Message

if TYPE_CHECKING:
    from collections.abc import Mapping


def message_from_event(payload: Mapping[str, object], envelope: Mapping[str, object]) -> Message:
    """Complete omitted message metadata from recorded event facts, never the current clock."""
    message = dict(payload)
    event_id = envelope.get('event_id')
    timestamp = envelope.get('ts')
    if 'message_id' not in message and isinstance(event_id, str) and event_id:
        message['message_id'] = f'msg-event-{event_id}'
    if 'ts' not in message and isinstance(timestamp, str) and timestamp:
        message['ts'] = timestamp
    return Message.model_validate(message)
