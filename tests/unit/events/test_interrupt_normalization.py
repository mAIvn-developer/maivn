"""Canonical durable interrupts retain their complete typed payload in Studio replay."""

from __future__ import annotations

from typing import TYPE_CHECKING

from maivn.events._models import AppEvent, NormalizedStreamState
from maivn.events._normalize.context import NormalizationOptions
from maivn.events._normalize.lifecycle_events import handle_interrupt_required_event

if TYPE_CHECKING:
    from pydantic import JsonValue


def test_canonical_interrupt_required_normalizes_to_durable_brain_event() -> None:
    """A durable tool-argument checkpoint must not collapse to the legacy prompt card."""
    interrupt: dict[str, JsonValue] = {
        'kind': 'tool_argument',
        'checkpoint_id': 'tool-argument-1',
        'thread_id': 'thread-1',
        'session_id': 'session-1',
        'prompt_source': 'agent_generated',
        'question': 'Which deployment ticket should I use?',
        'response_schema': {'type': 'string'},
        'requested_at': '2026-08-04T12:00:00Z',
        'tool_name': 'deploy',
        'argument_name': 'ticket_id',
    }

    payloads = handle_interrupt_required_event(
        {'interrupt': interrupt},
        NormalizedStreamState(),
        NormalizationOptions(),
    )

    event = AppEvent.model_validate(payloads[0])
    assert event.event_name == 'brain.session.interrupted'
    assert event.event_kind == 'interrupt'
    assert event.interrupt is not None
    assert event.interrupt.model_dump()['kind'] == 'tool_argument'
    assert event.interrupt.model_dump()['question'] == 'Which deployment ticket should I use?'
    assert event.model_extra is not None
    assert event.model_extra['payload'] == {'interrupt': interrupt}
