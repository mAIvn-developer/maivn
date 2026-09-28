"""Stripe presets declare and register the exact supported connection event types."""

from __future__ import annotations

import json
from typing import TYPE_CHECKING, cast

import httpx
import pytest

from maivn import Agent, Client, ClientConfig, TriggerInvocationBuilder

if TYPE_CHECKING:
    from collections.abc import Callable

CASES: tuple[tuple[Callable[[Agent], TriggerInvocationBuilder], str], ...] = (
    (lambda agent: agent.on_payment('conn_stripe'), 'payment_intent.succeeded'),
    (
        lambda agent: agent.on_payment('conn_stripe', status='failed'),
        'payment_intent.payment_failed',
    ),
    (lambda agent: agent.on_invoice('conn_stripe'), 'invoice.paid'),
    (
        lambda agent: agent.on_invoice('conn_stripe', event='payment_failed'),
        'invoice.payment_failed',
    ),
    (lambda agent: agent.on_subscription('conn_stripe'), 'customer.subscription.created'),
    (
        lambda agent: agent.on_subscription('conn_stripe', event='updated'),
        'customer.subscription.updated',
    ),
    (
        lambda agent: agent.on_subscription('conn_stripe', event='deleted'),
        'customer.subscription.deleted',
    ),
    (
        lambda agent: agent.on_subscription('conn_stripe', event='trial_will_end'),
        'customer.subscription.trial_will_end',
    ),
)


@pytest.mark.parametrize(('build', 'event_type'), CASES)
def test_each_declaration_is_inert_and_registration_sends_the_exact_source(
    build: Callable[[Agent], TriggerInvocationBuilder],
    event_type: str,
) -> None:
    """Observe the public client's transport, not only a builder's internal attributes."""
    calls: list[httpx.Request] = []

    def receive(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        if request.method == 'GET':
            return httpx.Response(404)
        assert request.method == 'POST'
        assert request.url.host == 'data.example'
        assert request.url.path == '/v1/scenarios'
        payload = json.loads(request.content)
        return httpx.Response(
            201,
            json={
                'scenario': {
                    'scenario': {
                        'scenario_id': payload['scenario_id'],
                        'project_id': 'prj-sdk',
                        'trigger_id': payload['trigger_id'],
                        'name': payload['name'],
                    },
                    'trigger': {
                        'trigger_id': payload['trigger_id'],
                        'name': payload['name'],
                        'status': 'enabled',
                        'source': payload['trigger_source'],
                    },
                }
            },
        )

    client = Client(
        config=ClientConfig.from_sources(
            api_key='test-key',
            base_url='https://data.example',
        ),
        transport=httpx.MockTransport(receive),
    )
    agent = Agent('payments', client=client)
    builder = build(agent).key('stripe-event').target('payment-agent', version=1)
    expected = {
        'kind': 'external_connection_event',
        'phase': 'phase_2',
        'connection_id': 'conn_stripe',
        'event_type': event_type,
    }
    assert calls == []
    assert agent.declared_triggers == [builder]
    assert builder.declaration()['trigger_source'] == expected
    trigger = builder.invoke('Handle the selected Stripe event.')
    assert [request.method for request in calls] == ['GET', 'POST']
    assert json.loads(calls[-1].content)['trigger_source'] == expected
    assert trigger.status == 'enabled'


@pytest.mark.parametrize(
    ('method', 'keyword'),
    [
        ('on_payment', 'status'),
        ('on_invoice', 'event'),
        ('on_subscription', 'event'),
    ],
)
@pytest.mark.parametrize('invalid', [True, False, 0, None, '', 'charge.succeeded', 'PAID', [], {}])
def test_invalid_presets_refuse_before_declaring_a_trigger(
    method: str,
    keyword: str,
    invalid: object,
) -> None:
    """Finite preset options are enforced at runtime, including untyped Python callers."""
    agent = Agent('payments', api_key='test-key')
    call = cast('Callable[..., TriggerInvocationBuilder]', getattr(agent, method))
    with pytest.raises(ValueError, match=keyword):
        call('conn_stripe', **{keyword: invalid})
    assert agent.declared_triggers == []


def test_generic_connection_remains_available_for_other_supported_providers() -> None:
    """Adding Stripe conveniences does not constrain the existing generic connection API."""
    agent = Agent('payments', api_key='test-key')
    assert (
        agent.on_connection('conn_generic', event_type='github.push').source.model_dump()[
            'event_type'
        ]
        == 'github.push'
    )
