"""Registered trigger source producers use typed requests and safe durable receipts."""

from __future__ import annotations

import asyncio
import json
from typing import TYPE_CHECKING, cast

import httpx
import pytest
from pydantic import ValidationError

from maivn import Client, ClientConfig, Trigger

if TYPE_CHECKING:
    from maivn._internal.transport.http import JsonObject

TIMER = {
    'timer_id': 'sla-timer-fixture',
    'instance_key': 'order:1',
    'condition': 'paid',
    'fire_when': 'condition_missing',
    'started_at': '2026-09-05T00:00:00Z',
    'deadline_at': '2026-09-05T00:00:05Z',
    'condition_met_at': None,
    'processed_at': None,
    'process_outcome': None,
    'inserted': True,
}
REPLY = {
    'event_id': 'conversation-reply-fixture',
    'conversation_id': '00000000-0000-0000-0000-000000000001',
    'reply_id': 'reply:1',
    'sequence': 1,
    'received_at': '2026-09-05T00:00:00Z',
    'inserted': True,
}


def test_registered_source_producers_sync_and_async_wire() -> None:
    """Eight ergonomic helpers submit only their canonical source-owned inputs."""
    calls: list[tuple[str, str, object]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(
            (
                request.method,
                request.url.path,
                json.loads(request.content) if request.content else None,
            )
        )
        return httpx.Response(200, json=REPLY if request.url.path.endswith('/replies') else TIMER)

    client = Client(
        config=ClientConfig.from_sources(
            api_key='public-test-key',
            base_url='https://data.example',
        ),
        transport=httpx.MockTransport(handler),
    )
    trigger = Trigger(
        client,
        cast(
            'JsonObject',
            {
                'scenario': {
                    'scenario': {'scenario_id': 'scn-fixture', 'project_id': 'project-fixture'},
                    'trigger': {'trigger_id': 'trg-fixture'},
                }
            },
        ),
    )
    assert trigger.arm('order:1', payload={'order': 1}).timer_id == TIMER['timer_id']
    assert trigger.timer('order:1').instance_key == 'order:1'
    assert trigger.satisfy('order:1', evidence={'paid': True}).condition == 'paid'
    assert (
        trigger.reply('Please continue', conversation_key='ticket/1', reply_id='reply:1').event_id
        == REPLY['event_id']
    )

    async def exercise() -> None:
        await trigger.aarm('order:1', payload={'order': 1})
        await trigger.atimer('order:1')
        await trigger.asatisfy('order:1', evidence={'paid': True})
        await trigger.areply('Please continue', conversation_key='ticket/1', reply_id='reply:1')

    asyncio.run(exercise())
    expected = [
        (
            'POST',
            '/v1/scenarios/scn-fixture/sla-timers',
            {'instance_key': 'order:1', 'payload': {'order': 1}},
        ),
        ('GET', '/v1/scenarios/scn-fixture/sla-timers/order:1', None),
        (
            'POST',
            '/v1/scenarios/scn-fixture/sla-timers/order:1/satisfy',
            {'evidence': {'paid': True}},
        ),
        (
            'POST',
            '/v1/scenarios/scn-fixture/replies',
            {'conversation_key': 'ticket/1', 'reply_id': 'reply:1', 'content': 'Please continue'},
        ),
    ]
    assert calls == expected * 2
    before = len(calls)
    with pytest.raises(ValidationError):
        trigger.arm('unsafe/key')
    with pytest.raises(ValidationError):
        trigger.reply('Message', conversation_key='ticket', reply_id=' ')
    assert len(calls) == before
