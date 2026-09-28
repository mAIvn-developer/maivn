# pyright: reportPrivateUsage=false
"""Privacy guards across arrival-form publication and caller hydration."""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING

import pytest

from maivn._internal.client import (
    _hydrated_client_events,
    _telemetry_client_events,
)
from maivn._internal.models import StreamEvent
from maivn.telemetry import register_listener
from maivn.telemetry._registry import reset_listeners_for_testing

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

_PLACEHOLDER = '{_{account_number}_}'
_RESOLVED = 'ACCOUNT-PRIVATE-4242'


def setup_function() -> None:
    """Start each privacy test with no process-wide listeners."""
    reset_listeners_for_testing()


def teardown_function() -> None:
    """Remove process-wide listeners after each privacy test."""
    reset_listeners_for_testing()


def _final_event(content: str) -> StreamEvent:
    return StreamEvent(
        position=1,
        event_type='final',
        data={
            'event_id': 'evt-private',
            'type': 'final',
            'session_id': 'ses-private',
            'payload': {
                'message': {
                    'message_id': 'msg-private',
                    'role': 'assistant',
                    'content': content,
                    'ts': '2026-09-01T12:00:00Z',
                },
                'result': {'account': content},
                'usage': {'input_tokens': 2, 'output_tokens': 1},
            },
            'ts': '2026-09-01T12:00:00Z',
        },
    )


@pytest.mark.parametrize('arrival_content', [_PLACEHOLDER, _RESOLVED])
def test_arrival_form_never_changes_the_metadata_only_listener_surface(
    arrival_content: str,
) -> None:
    """Placeholder and server-resolved arrivals emit identical safe metadata."""
    observed: list[str] = []
    register_listener(lambda event: observed.append(event.model_dump_json()))

    async def source() -> AsyncIterator[StreamEvent]:
        yield _final_event(arrival_content)

    async def collect() -> list[StreamEvent]:
        return [
            event
            async for event in _hydrated_client_events(
                _telemetry_client_events(source()),
                private_data={'account_number': _RESOLVED},
            )
        ]

    returned = asyncio.run(collect())
    returned_text = returned[0].model_dump_json()

    assert len(observed) == 1
    assert _PLACEHOLDER not in observed[0]
    assert _RESOLVED not in observed[0]
    assert 'content' not in observed[0]
    assert _RESOLVED in returned_text
