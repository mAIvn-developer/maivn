"""Public contract tests for v1-visible SDK stream events."""

from __future__ import annotations

import pytest

from maivn._internal.models import StreamEvent
from maivn._internal.reporting.stream_events import StreamEventProjector


@pytest.mark.parametrize(
    ('canonical_name', 'visible_name'),
    [
        ('status_message_chunk', 'update'),
        ('progress_update', 'progress_update'),
        ('system_tool_start', 'system_tool_start'),
        ('system_tool_chunk', 'system_tool_chunk'),
        ('system_tool_complete', 'system_tool_complete'),
        ('system_tool_error', 'system_tool_error'),
        ('final', 'final'),
        ('error', 'error'),
    ],
)
def test_stream_event_name_contract(canonical_name: str, visible_name: str) -> None:
    """The frozen v1 names are stable while the canonical v2 envelope stays intact."""
    event = StreamEvent(
        position=1,
        event_type=canonical_name,
        data={
            'type': canonical_name,
            'payload': {
                'message_id': 'msg-1',
                'content_delta': 'text',
            },
        },
    )

    projected = StreamEventProjector().project(event)

    assert projected.name == visible_name
    assert projected.event_type == canonical_name
    assert projected.data is event.data
