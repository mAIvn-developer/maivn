"""The SDK forwards only positioned, root-owned task list events."""

from __future__ import annotations

import asyncio
from typing import cast

from maivn import StreamEvent
from maivn.events import EventBridge, forward_normalized_event, normalize_stream_event

_ITEMS = [{'id': 'collect', 'title': 'Collect inputs', 'status': 'in_progress'}]


def _event(**overrides: object) -> StreamEvent:
    return StreamEvent.model_validate(
        {
            'event_type': 'task_list_updated',
            'position': 42,
            'data': {
                'event_id': 'evt-task-list',
                'ordinal': 'actor-7',
                'type': 'task_list_updated',
                'session_id': 'ses-root',
                'root_event_id': 'evt-root',
                'correlation_id': 'call-7',
                'trigger_chain_depth': 0,
                'job_depth': 0,
                'job_index': 0,
                'actor': 'agent',
                'mode': 'hosted',
                'payload': {'items': _ITEMS},
                'ts': '2026-11-07T00:00:00Z',
                **overrides,
            },
        }
    )


def test_task_list_normalization_preserves_durable_position_for_ui_replay() -> None:
    """The frontend projection uses the durable cursor rather than the bridge ID."""

    async def run() -> None:
        bridge = EventBridge('app-session', audience='frontend_safe')
        (normalized,) = normalize_stream_event(_event())
        await forward_normalized_event(normalized, bridge=bridge)
        (event,) = bridge.get_history()
        assert event['type'] == 'task_list_updated'
        assert cast('dict[str, object]', event['data'])['task_list'] == {
            'session_id': 'ses-root',
            'position': 42,
            'items': _ITEMS,
        }

    asyncio.run(run())


def test_task_list_normalization_discards_descendant_and_invalid_snapshots() -> None:
    """Malformed and child snapshots never reach the frontend bridge."""
    assert normalize_stream_event(_event(parent_session_id='ses-parent')) == []
    assert normalize_stream_event(_event(parent_assignment_uuid='assignment-parent')) == []
    assert normalize_stream_event(_event(payload={'items': [{'id': 'collect'}]})) == []
