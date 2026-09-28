"""Unit contracts for durable trigger registration and lifecycle builders."""

from __future__ import annotations

import asyncio
import json
from datetime import timedelta
from http import HTTPStatus
from typing import TYPE_CHECKING, cast

import httpx
import pytest
from maivn_contracts.scenarios import ManualSource, OutputBinding, ScheduleSource
from pydantic import AnyUrl, TypeAdapter

import maivn
from maivn import Agent, Client, ClientConfig, TriggerInvocationBuilder

if TYPE_CHECKING:
    from collections.abc import AsyncIterator, Callable

    from maivn import StreamEvent


def _client() -> Client:
    return Client(
        config=ClientConfig(
            api_key='test-key',
            base_url=AnyUrl('https://data.example'),
        ),
        transport=httpx.MockTransport(
            lambda _request: httpx.Response(
                500, json={'detail': 'Unexpected.', 'code': 'internal_error'}
            )
        ),
    )


def _scenario_response(
    *,
    scenario_id: str,
    trigger_id: str,
    name: str,
    source: dict[str, object],
    status: str = 'enabled',
) -> dict[str, object]:
    return {
        'scenario': {
            'scenario': {
                'scenario_id': scenario_id,
                'project_id': 'prj-sdk',
                'trigger_id': trigger_id,
                'name': name,
            },
            'trigger': {
                'trigger_id': trigger_id,
                'name': name,
                'status': status,
                'source': source,
            },
        }
    }


@pytest.mark.parametrize('operation', ['insert', 'update', 'delete'])
def test_database_record_change_names_the_connection_and_schema_table(operation: str) -> None:
    """A developer's database row selector cannot become a platform-memory trigger."""
    agent = Agent(name='triage', client=_client())
    source = agent.on_record_change(
        'public.orders', connection_id='conn-db', operation=operation
    ).source
    assert source.model_dump(mode='json', exclude_none=True) == {
        'kind': 'external_connection_event',
        'phase': 'phase_2',
        'connection_id': 'conn-db',
        'event_type': f'row.{operation}',
        'table': 'public.orders',
    }


def test_memory_change_has_an_explicit_name_and_keeps_the_legacy_keyword() -> None:
    """Existing record_kind callers and the explicit memory helper retain their wire source."""
    agent = Agent(name='triage', client=_client())
    expected = {
        'kind': 'memory_event',
        'phase': 'phase_3',
        'record_kind': 'insight',
        'operation': 'inserted',
    }
    assert (
        agent.on_memory_change('insight', operation='inserted').source.model_dump(mode='json')
        == expected
    )
    assert (
        agent.on_record_change(record_kind='insight', operation='inserted').source.model_dump(
            mode='json'
        )
        == expected
    )


@pytest.mark.parametrize(
    'table', ['orders', 'public.orders.extra', 'public.orders;drop', 'public.', '']
)
def test_database_record_change_rejects_an_unqualified_or_malformed_table(table: str) -> None:
    """The builder cannot register a broad or malformed database selector."""
    agent = Agent(name='triage', client=_client())
    with pytest.raises(ValueError, match='table'):
        agent.on_record_change(table, connection_id='conn-db', operation='insert')


def test_all_eleven_trigger_sources_share_one_registration_builder() -> None:
    """Source-named helpers serialize the authoritative trigger-source contracts."""
    agent = Agent(name='triage', client=_client())
    cases: tuple[
        tuple[Callable[[], TriggerInvocationBuilder], dict[str, object]],
        ...,
    ] = (
        (
            lambda: agent.cron('*/5 * * * *', jitter=timedelta(seconds=7)),
            {
                'kind': 'schedule',
                'phase': 'phase_1',
                'cron': '*/5 * * * *',
                'jitter_seconds': 7,
            },
        ),
        (
            agent.on_manual,
            {'kind': 'manual', 'phase': 'phase_1'},
        ),
        (
            lambda: agent.on_webhook('vault://triggers/triage'),
            {
                'kind': 'webhook',
                'phase': 'phase_1',
                'secret_ref': 'vault://triggers/triage',
            },
        ),
        (
            lambda: agent.on_connection('github-main', event_type='github.pull_request'),
            {
                'kind': 'external_connection_event',
                'phase': 'phase_2',
                'connection_id': 'github-main',
                'event_type': 'github.pull_request',
            },
        ),
        (
            lambda: agent.on_event(
                'session.completed',
                origin_trigger_id='trg-parent',
            ),
            {
                'kind': 'chained_event',
                'phase': 'phase_3',
                'event_type': 'session.completed',
                'origin_trigger_id': 'trg-parent',
            },
        ),
        (
            lambda: agent.on_sla(
                'ticket.resolved',
                fire_when='condition_missing',
                deadline_seconds=900,
            ),
            {
                'kind': 'sla_timer',
                'phase': 'phase_4',
                'condition': 'ticket.resolved',
                'fire_when': 'condition_missing',
                'deadline_seconds': 900,
            },
        ),
        (
            agent.on_reply,
            {'kind': 'reply_conversation', 'phase': 'phase_4'},
        ),
        (
            agent.on_tool_fire,
            {'kind': 'fire_trigger_tool', 'phase': 'phase_4'},
        ),
        (
            lambda: agent.on_email(
                'email-drop-zone',
                sender_allowlist=('alerts@example.com', '*@trusted.example'),
            ),
            {
                'kind': 'email_to_invoke',
                'phase': 'phase_2',
                'connection_id': 'email-drop-zone',
                'sender_allowlist': ['alerts@example.com', '*@trusted.example'],
            },
        ),
        (
            lambda: agent.on_record_change('insight', operation='inserted'),
            {
                'kind': 'memory_event',
                'phase': 'phase_3',
                'record_kind': 'insight',
                'operation': 'inserted',
            },
        ),
        (
            lambda: agent.on_storage('source', operation='inserted'),
            {
                'kind': 'storage_event',
                'phase': 'phase_3',
                'bucket': 'memory-resources',
                'object_kind': 'source',
                'operation': 'inserted',
            },
        ),
    )

    for build, expected in cases:
        builder = build()
        assert type(builder).__name__ == 'TriggerInvocationBuilder'
        assert builder.source.model_dump(mode='json', exclude_none=True) == expected
        payload = builder.key('automatic').declaration_payload('Process the event.')
        assert payload['agent'] == {'agent_id': 'triage', 'version': 1}
        assert not hasattr(builder, 'stream')
        assert not hasattr(builder, 'astream')
        assert not hasattr(builder, 'batch')
        assert not hasattr(builder, 'abatch')

    low_level = agent.on(ManualSource(kind='manual', phase='phase_1'))
    assert low_level.source.kind == 'manual'


def test_trigger_source_specs_and_guard_options_are_public_sdk_names() -> None:
    """Developers do not need an internal contracts import to use low-level builders."""
    expected = {
        'ScheduleSource',
        'ManualSource',
        'WebhookSource',
        'ExternalConnectionEventSource',
        'ChainedEventSource',
        'SLATimerSource',
        'ReplyConversationSource',
        'FireTriggerToolSource',
        'EmailToInvokeSource',
        'MemoryEventSource',
        'StorageEventSource',
        'TriggerSource',
        'TriggerOptions',
        'RetryPolicy',
    }

    assert expected <= set(maivn.__all__)
    assert all(hasattr(maivn, name) for name in expected)


def test_agent_constructor_accepts_only_the_one_public_origin() -> None:
    """A simple Agent can register triggers without requiring a prebuilt Client.

    There is exactly one public address knob (``base_url``); trigger
    registration goes through the same single entry point as everything else.
    """
    agent = Agent(
        name='configured',
        api_key='test-key',
        base_url='https://data.example',
    )

    assert agent.client is not None
    assert agent.client.config.base_url_text == 'https://data.example'
    assert agent.client.config.base_url_text == 'https://data.example'


def test_explicit_local_schedule_retains_the_in_process_compatibility_builder() -> None:
    """Local scheduling remains available only through an explicit mode."""
    agent = Agent(name='local-only', client=_client())

    builder = agent.interval(timedelta(minutes=5), mode='local')

    assert type(builder).__name__ == 'CronInvocationBuilder'


def test_remote_cron_encodes_a_named_timezone_in_the_durable_expression() -> None:
    """Named timezone convenience survives the move from local to durable schedules."""
    agent = Agent(name='remote-cron', client=_client())

    builder = agent.cron('0 9 * * 1-5', tz='America/Chicago')

    assert isinstance(builder.source, ScheduleSource)
    assert builder.source.cron == 'CRON_TZ=America/Chicago 0 9 * * 1-5'


@pytest.mark.parametrize('schedule', ['cron', 'interval'])
def test_remote_schedules_accept_their_name_option(schedule: str) -> None:
    """The documented naming option also works in the default durable mode."""
    agent = Agent(name='reporter', client=_client())
    builder = (
        agent.cron('0 9 * * *', name='Morning report')
        if schedule == 'cron'
        else agent.interval(timedelta(hours=1), name='Morning report')
    )
    payload = builder.key('report').declaration_payload('Prepare the report.')
    assert payload['name'] == 'Morning report'
    assert builder.named('Revised report').declaration()['name'] == 'Revised report'


def test_invoke_registers_then_upserts_one_stable_trigger_on_the_control_plane() -> None:
    """A stable key owns one scenario identity and updates every mutable builder field."""
    calls: list[tuple[str, str, str, dict[str, object] | None]] = []
    stored: dict[str, object] | None = None

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal stored
        body = (
            None if not request.content else cast('dict[str, object]', json.loads(request.content))
        )
        calls.append((request.method, request.url.host or '', request.url.path, body))
        if request.method == 'GET':
            if stored is None:
                return httpx.Response(HTTPStatus.NOT_FOUND)
            return httpx.Response(HTTPStatus.OK, json=stored)
        if request.method == 'POST':
            assert body is not None
            stored = _scenario_response(
                scenario_id=str(body['scenario_id']),
                trigger_id=str(body['trigger_id']),
                name=str(body['name']),
                # The fixture body is typed `dict[str, object]`, so the nested source map
                # arrives as `object` and only widens back at the call.
                source=dict(body['trigger_source']),  # type: ignore[arg-type]
            )
            return httpx.Response(HTTPStatus.CREATED, json=stored)
        assert request.method == 'PATCH'
        assert body is not None
        assert stored is not None
        scenario = cast('dict[str, object]', stored['scenario'])
        canvas = cast('dict[str, object]', scenario['scenario'])
        trigger = cast('dict[str, object]', scenario['trigger'])
        canvas['name'] = body['name']
        trigger['name'] = body['name']
        trigger['source'] = body['trigger_source']
        return httpx.Response(HTTPStatus.OK, json=stored)

    client = Client(
        config=ClientConfig.from_sources(
            api_key='test-key',
            base_url='https://data.example',
        ),
        transport=httpx.MockTransport(handler),
    )
    agent = Agent(name='triage', client=client)
    builder = (
        agent.on_manual()
        .key('ticket-triage')
        .named('Ticket triage')
        .target('agt-triage', version=3)
        .where('$[?$.priority == "high"]')
        .with_options(
            concurrency=2,
            max_attempts=2,
            initial_delay_seconds=5,
            max_delay_seconds=60,
            backoff='fixed',
            dedupe_window_seconds=3600,
            depth_cap=4,
            fanout_cap=20,
            shadow=True,
            fire_budget=2,
        )
        .emit('ticket.triaged')
    )

    created = builder.invoke('Classify {{event.subject}}.')
    updated = asyncio.run(
        builder.named('Ticket triage v2').ainvoke(
            ('Classify {{event.subject}}.', 'Return the routing decision.')
        )
    )

    assert type(created).__name__ == 'Trigger'
    assert updated.scenario_id == created.scenario_id
    assert updated.trigger_id == created.trigger_id
    assert created.status == 'enabled'
    assert [call[:3] for call in calls] == [
        ('GET', 'data.example', f'/v1/scenarios/{created.scenario_id}'),
        ('POST', 'data.example', '/v1/scenarios'),
        ('GET', 'data.example', f'/v1/scenarios/{created.scenario_id}'),
        ('PATCH', 'data.example', f'/v1/scenarios/{created.scenario_id}'),
    ]
    create_body = calls[1][3]
    update_body = calls[3][3]
    assert create_body is not None
    assert update_body is not None
    assert create_body == {
        'scenario_id': created.scenario_id,
        'trigger_id': created.trigger_id,
        'name': 'Ticket triage',
        'trigger_source': {'kind': 'manual', 'phase': 'phase_1'},
        'trigger_options': {
            'concurrency': 2,
            'retry_policy': {
                'max_attempts': 2,
                'initial_delay_seconds': 5,
                'max_delay_seconds': 60,
                'backoff': 'fixed',
            },
            'dedupe_window_seconds': 3600,
            'depth_cap': 4,
            'fanout_cap': 20,
            'shadow': True,
            'fire_budget': 2,
        },
        'agent': {'agent_id': 'agt-triage', 'version': 3},
        'output_binding': {
            'kind': 'event_emission',
            'target': {'event_type': 'ticket.triaged'},
        },
        'trigger_binding': {
            'kind': 'template',
            'messages': ['Classify {{event.subject}}.'],
        },
        'trigger_filter': '$[?$.priority == "high"]',
    }
    assert update_body == {
        'name': 'Ticket triage v2',
        'trigger_source': {'kind': 'manual', 'phase': 'phase_1'},
        'trigger_options': create_body['trigger_options'],
        'agent': {'agent_id': 'agt-triage', 'version': 3},
        'output_binding': create_body['output_binding'],
        'trigger_binding': {
            'kind': 'template',
            'messages': [
                'Classify {{event.subject}}.',
                'Return the routing decision.',
            ],
        },
        'trigger_filter': '$[?$.priority == "high"]',
    }


def test_attached_trigger_controls_lifecycle_and_streams_one_concrete_fire() -> None:
    """Lifecycle calls and fire-stream session events share the one public origin."""
    calls: list[tuple[str, str, str, dict[str, object] | None]] = []
    scenario_id = 'scn-sdk-lifecycle'
    trigger_id = 'trg-sdk-lifecycle'
    status = 'enabled'
    fire_count = 0
    history_queries: list[str] = []

    # A stub router: one return per route it answers, which is the point of it.
    def handler(request: httpx.Request) -> httpx.Response:  # noqa: PLR0911
        nonlocal status, fire_count
        body = (
            None if not request.content else cast('dict[str, object]', json.loads(request.content))
        )
        calls.append((request.method, request.url.host or '', request.url.path, body))
        if request.url.path.startswith('/v1/sessions/') and request.url.path.endswith('/events'):
            return httpx.Response(
                HTTPStatus.OK,
                headers={'content-type': 'text/event-stream'},
                content=(
                    'id: 1\nevent: final\n'
                    'data: {"event_id":"evt-final","ordinal":"ord-1","type":"final",'
                    f'"session_id":"ses-fire-{fire_count}","root_event_id":"evt-root",'
                    '"payload":{"message":{"message_id":"msg-final","role":"assistant",'
                    '"content":"triggered","ts":"2026-07-28T12:00:00Z"},'
                    '"assistant_id":"orchestrator_agent","usage":{"input_tokens":1,'
                    '"output_tokens":1},"stop_reason":"end_turn",'
                    '"tool_calls":{"count":0,"names":[]}},'
                    '"ts":"2026-07-28T12:00:00Z"}\n\n'
                ),
            )
        if request.method == 'GET' and request.url.path == '/v1/sessions':
            history_queries.append(request.url.query.decode())
            return httpx.Response(
                HTTPStatus.OK,
                json={
                    'items': [
                        {
                            'session_id': 'ses-history',
                            'project_id': 'prj-sdk',
                            'started_at': '2026-07-28T12:00:00Z',
                            'ended_at': '2026-07-28T12:00:01Z',
                            'status': 'completed',
                            'source': 'triggered',
                            'agent_id': 'agt-lifecycle',
                            'swarm_id': None,
                            'trigger_name': 'Lifecycle',
                            'scenario_id': scenario_id,
                            'scenario_name': 'Lifecycle',
                            'origin': None,
                            'usage': {'input_tokens': 1, 'output_tokens': 1},
                            'duration_ms': 1000,
                            'child_count': 0,
                            'has_children': False,
                        }
                    ],
                    'next_cursor': None,
                },
            )
        if request.method == 'POST' and request.url.path.endswith('/pause'):
            status = 'paused'
            return httpx.Response(
                HTTPStatus.OK,
                json=_scenario_response(
                    scenario_id=scenario_id,
                    trigger_id=trigger_id,
                    name='Lifecycle',
                    source={'kind': 'manual', 'phase': 'phase_1'},
                    status=status,
                ),
            )
        if request.method == 'POST' and request.url.path.endswith('/resume'):
            status = 'enabled'
            return httpx.Response(
                HTTPStatus.OK,
                json=_scenario_response(
                    scenario_id=scenario_id,
                    trigger_id=trigger_id,
                    name='Lifecycle',
                    source={'kind': 'manual', 'phase': 'phase_1'},
                    status=status,
                ),
            )
        if request.method == 'POST' and request.url.path.endswith('/fire'):
            fire_count += 1
            return httpx.Response(
                HTTPStatus.ACCEPTED,
                json={
                    'fire_id': f'fire-{fire_count}',
                    'trigger_id': trigger_id,
                    'session_id': f'ses-fire-{fire_count}',
                },
            )
        if request.method == 'DELETE':
            return httpx.Response(HTTPStatus.OK, json={'success': True})
        return httpx.Response(
            HTTPStatus.CREATED,
            json=_scenario_response(
                scenario_id=scenario_id,
                trigger_id=trigger_id,
                name='Lifecycle',
                source={'kind': 'manual', 'phase': 'phase_1'},
                status=status,
            ),
        )

    client = Client(
        config=ClientConfig.from_sources(
            api_key='test-key',
            base_url='https://data.example',
        ),
        transport=httpx.MockTransport(handler),
    )
    agent = Agent(name='lifecycle', client=client)
    trigger = (
        agent.on_manual()
        .key('lifecycle')
        .target('agt-lifecycle', version=1)
        .invoke('Handle {{event.subject}}.')
    )

    assert trigger.pause().status == 'paused'
    assert asyncio.run(trigger.aresume()).status == 'enabled'
    receipt = trigger.fire({'subject': 'sync'})
    streamed = list(trigger.fire_stream({'subject': 'stream'}))
    async_streamed = asyncio.run(_collect(trigger.afire_stream({'subject': 'async-stream'})))
    history = trigger.history(limit=10)
    replayed = list(trigger.replay('ses-history'))
    asyncio.run(trigger.adelete())

    assert receipt.fire_id == 'fire-1'
    assert receipt.trigger_id == trigger_id
    assert receipt.session_id == 'ses-fire-1'
    assert [event.event_type for event in streamed] == ['final']
    assert [event.event_type for event in async_streamed] == ['final']
    assert history[0]['session_id'] == 'ses-history'
    assert [event.event_type for event in replayed] == ['final']
    assert history_queries == [
        f'project_id=prj-sdk&scenario_id={scenario_id}&limit=10',
    ]
    assert (
        'POST',
        'data.example',
        f'/v1/scenarios/{scenario_id}/fire',
        {'payload': {'subject': 'sync'}},
    ) in calls
    assert (
        'GET',
        'data.example',
        '/v1/sessions/ses-fire-2/events',
        None,
    ) in calls
    assert calls[-1] == (
        'DELETE',
        'data.example',
        f'/v1/scenarios/{scenario_id}',
        None,
    )


async def _collect(events: AsyncIterator[StreamEvent]) -> list[StreamEvent]:
    return [event async for event in events]


def test_declaring_a_trigger_records_it_on_the_scope_without_registering() -> None:
    """Declaring lists the trigger on the scope; only invoke talks to the platform."""
    agent = Agent(name='describer', client=_client())

    builder = (
        agent.on_manual().key('nightly-brief').named('Nightly brief').target('agent-1', version=2)
    )

    assert agent.declared_triggers == [builder]
    declaration = builder.declaration()
    assert declaration['key'] == 'nightly-brief'
    assert declaration['name'] == 'Nightly brief'
    assert declaration['trigger_source'] == {'kind': 'manual', 'phase': 'phase_1'}
    assert declaration['agent'] == {'agent_id': 'agent-1', 'version': 2}
    assert declaration['output_binding'] == {'kind': 'display_in_app'}
    scenario_id, trigger_id = maivn.stable_trigger_ids('agent-1', 'nightly-brief')
    assert (declaration['scenario_id'], declaration['trigger_id']) == (scenario_id, trigger_id)
    assert scenario_id.startswith('scn-sdk-')


def test_a_builder_without_a_client_declares_but_refuses_to_register() -> None:
    """A tool describing an app may hold builders with no client; registration must fail loudly."""
    builder = (
        TriggerInvocationBuilder(None, ManualSource(kind='manual', phase='phase_1'))
        .key('nightly-brief')
        .target('agent-1', version=2)
    )

    assert builder.declaration()['scenario_id'] is not None
    with pytest.raises(maivn.MaivnSDKError, match='initialized SDK client'):
        builder.invoke('Summarize the day.')


def test_declarations_keep_their_order_and_inherit_the_owner_target() -> None:
    """A key is enough to identify an owner-bound declaration."""
    agent = Agent(name='describer', client=_client())
    first = agent.on_manual().key('a')
    second = agent.on_manual().key('b').target('agent-1', version=1)

    assert [b.declaration()['key'] for b in agent.declared_triggers] == ['a', 'b']
    assert first.declaration()['agent'] == {'agent_id': 'describer', 'version': 1}
    assert first.declaration()['scenario_id'] is not None
    assert second.declaration()['scenario_id'] is not None


@pytest.mark.parametrize('saved_output', [False, True])
@pytest.mark.parametrize('explicit_target', [False, True])
def test_declaration_payload_is_exactly_what_registration_puts_on_the_wire(
    *,
    saved_output: bool,
    explicit_target: bool,
) -> None:
    """One writer for the registration body: what is declared is what is posted.

    The create body and ``declaration_payload`` were built by two separate
    pieces of code that happened to agree; nothing stopped them drifting. This
    pins them to the same object, so a field added to one can never go missing
    from the other.
    """
    posted: list[dict[str, object]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == 'GET':
            return httpx.Response(HTTPStatus.NOT_FOUND)
        body = cast('dict[str, object]', json.loads(request.content))
        posted.append(body)
        return httpx.Response(
            HTTPStatus.CREATED,
            json=_scenario_response(
                scenario_id=str(body['scenario_id']),
                trigger_id=str(body['trigger_id']),
                name=str(body['name']),
                source=dict(body['trigger_source']),  # type: ignore[arg-type]
            ),
        )

    client = Client(
        config=ClientConfig.from_sources(
            api_key='test-key',
            base_url='https://data.example',
        ),
        transport=httpx.MockTransport(handler),
    )
    agent = Agent(name='triage', client=client)
    builder = (
        agent.on_manual()
        .key('nightly-brief')
        .named('Nightly brief')
        .where('$[?$.urgent == true]')
        .with_options(concurrency=3, max_attempts=2)
        .emit('brief.ready')
    )
    messages = ('Summarize {{event.window}}.', 'Name the three biggest changes.')
    if explicit_target:
        builder = builder.target('agt-brief', version=4)
    if saved_output:
        builder = builder.to_connection('conn_saved')

    declared = builder.declaration_payload(messages)
    _ = builder.invoke(messages)

    assert posted == [declared]
    assert declared['agent'] == (
        {'agent_id': 'agt-brief', 'version': 4}
        if explicit_target
        else {'agent_id': 'triage', 'version': 1}
    )
    assert declared['trigger_binding'] == {'kind': 'template', 'messages': list(messages)}


def test_an_incomplete_declaration_describes_itself_but_refuses_a_payload() -> None:
    """A half-written trigger still lists in Studio; it just cannot be registered.

    ``declaration`` tolerates the unfinished state and answers null ids;
    ``declaration_payload`` has no stable identity to register under and says
    which builder call is missing.
    """
    agent = Agent(name='triage', client=_client())
    builder = agent.on_manual().named('Unfinished')

    declaration = builder.declaration()
    assert declaration['key'] is None
    assert declaration['scenario_id'] is None
    assert 'trigger_binding' not in declaration

    with pytest.raises(ValueError, match=r'requires \.key'):
        _ = builder.declaration_payload('Do the thing.')

    assert builder.key('unfinished').declaration_payload('Do the thing.')['agent'] == {
        'agent_id': 'triage',
        'version': 1,
    }


@pytest.mark.parametrize('scope_type', [Agent, maivn.Swarm])
def test_configured_owner_identity_is_shared_by_triggers(
    scope_type: type[Agent | maivn.Swarm],
) -> None:
    """Set a deployment identity once on the scope, including its pinned version."""
    scope = scope_type(name='Triage', agent_id='triage-prod', version=3, client=_client())
    builder = scope.on_webhook('vault://triggers/triage').key('incoming')
    assert builder.declaration_payload('Process it.')['agent'] == {
        'agent_id': 'triage-prod',
        'version': 3,
    }
    if isinstance(scope, Agent):
        session = scope.serving(project_id='11111111-1111-1111-1111-111111111111')
        assert session.manifest.agent_id == 'triage-prod'
        assert session.manifest.version == scope.version
    assert builder.target('other-agent', version=2).declaration()['agent'] == {
        'agent_id': 'other-agent',
        'version': 2,
    }


def test_standalone_builder_still_requires_an_explicit_target() -> None:
    """Only a scope-bound builder has an owner to infer."""
    builder = TriggerInvocationBuilder(_client(), ManualSource(kind='manual', phase='phase_1'))
    with pytest.raises(ValueError, match=r'requires \.target'):
        builder.key('standalone').declaration_payload('Process it.')


def test_swarm_trigger_can_register_after_its_first_member_is_added() -> None:
    """Declaring before assembling a swarm must not permanently capture a missing client."""
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if request.method == 'GET':
            return httpx.Response(404)
        return httpx.Response(
            201,
            json=_scenario_response(
                scenario_id='scn-1',
                trigger_id='trg-1',
                name='Late member',
                source={'kind': 'manual', 'phase': 'phase_1'},
            ),
        )

    swarm = maivn.Swarm(name='team')
    builder = swarm.on_manual().key('late-member').asking('Run.')
    client = Client(api_key='test-key', transport=httpx.MockTransport(handler))
    swarm.add_agent(Agent(name='member', client=client))
    assert builder.invoke().trigger_id == 'trg-1'
    assert [request.method for request in requests] == ['GET', 'POST']


def test_output_binding_builders_emit_exact_contract_payloads() -> None:
    """Each output helper gives developers a complete contract-valid binding."""
    cases: tuple[
        tuple[
            Callable[[TriggerInvocationBuilder], TriggerInvocationBuilder],
            dict[str, object],
        ],
        ...,
    ] = (
        (
            lambda builder: builder.to_slack(' slack-channel '),
            {
                'kind': 'slack_message',
                'target': {'channel_ref': 'slack-channel'},
            },
        ),
        (
            lambda builder: builder.to_discord(' discord-channel '),
            {
                'kind': 'discord_message',
                'target': {'channel_ref': 'discord-channel'},
            },
        ),
        (
            lambda builder: builder.reply_by_email(' email-thread '),
            {
                'kind': 'email_reply',
                'target': {'thread_ref': 'email-thread'},
            },
        ),
        (
            lambda builder: builder.comment_on_github(
                ' github-repository ',
                issue_ref=' github-issue ',
            ),
            {
                'kind': 'github_comment',
                'target': {
                    'repository_ref': 'github-repository',
                    'issue_ref': 'github-issue',
                },
            },
        ),
        (
            lambda builder: builder.to_webhook(' webhook-endpoint '),
            {
                'kind': 'webhook',
                'target': {'endpoint_ref': 'webhook-endpoint'},
            },
        ),
        (
            lambda builder: builder.emit(' summary.completed '),
            {
                'kind': 'event_emission',
                'target': {'event_type': 'summary.completed'},
            },
        ),
        (
            lambda builder: builder.display_in_app(),
            {'kind': 'display_in_app'},
        ),
    )
    adapter: TypeAdapter[OutputBinding] = TypeAdapter(OutputBinding)

    for configure_output, expected in cases:
        builder = (
            TriggerInvocationBuilder(None, ManualSource(kind='manual', phase='phase_1'))
            .key('output-test')
            .target('agent-1', version=1)
            .to_connection('conn_saved')
        )

        payload = configure_output(builder).declaration_payload('Run.')
        assert 'output_connection' not in payload
        output_binding = payload['output_binding']

        assert output_binding == expected
        assert adapter.validate_python(output_binding).model_dump(mode='json') == expected


def test_saved_output_is_safe_selector_in_discovery_and_registration() -> None:
    """A saved destination must not be confused with a private endpoint reference."""
    builder = (
        TriggerInvocationBuilder(None, ManualSource(kind='manual', phase='phase_1'))
        .key('saved-output')
        .target('agent-1', version=1)
        .asking('Run.')
        .to_webhook('private-endpoint-reference')
        .to_connection('conn_saved')
    )
    for payload in (builder.declaration(), builder.declaration_payload()):
        assert payload['output_connection'] == {'connection_id': 'conn_saved'}
        assert 'output_binding' not in payload
        assert 'private-endpoint-reference' not in json.dumps(payload)
    with pytest.raises(ValueError, match='endpoint_ref'):
        _ = builder.to_webhook('')
    assert builder.declaration_payload()['output_connection'] == {'connection_id': 'conn_saved'}


@pytest.mark.parametrize('connection_id', ['', '../connection', 'https://example.test', 'a' * 201])
def test_saved_output_rejects_invalid_connection_identity(connection_id: str) -> None:
    """Reject URLs and unsafe identifiers before registration reaches the server."""
    builder = TriggerInvocationBuilder(None, ManualSource(kind='manual', phase='phase_1'))
    with pytest.raises(ValueError, match='connection_id'):
        _ = builder.to_connection(connection_id)


def test_a_declaration_can_carry_its_own_prompt_so_nobody_has_to_invent_one() -> None:
    """`asking(...)` puts the message template in the declaration, not only in `invoke`.

    A trigger is the app called from an event instead of from a person, so
    something must stand in for what the person would have typed. That was
    reachable only through `invoke(messages)`, which also registers and so needs
    a client - leaving an app that merely declares its triggers with nowhere to
    put its prompt, and any tool registering that declaration later obliged to
    invent one. An invented prompt makes the registered trigger differ from the
    app's own code, which is the whole thing this surface exists to prevent.
    """
    agent = Agent(name='briefer', client=_client())
    builder = (
        agent.cron('0 9 * * 1-5')
        .key('weekday-brief')
        .named('Weekday brief')
        .target('sample-agent', version=1)
        .asking('Summarize what changed since yesterday.')
    )

    declaration = builder.declaration()
    assert declaration['trigger_binding'] == {
        'kind': 'template',
        'messages': ['Summarize what changed since yesterday.'],
    }
    assert builder.declaration_payload()['trigger_binding'] == declaration['trigger_binding']


def test_an_explicit_message_beats_the_declared_one() -> None:
    """Passing messages to `invoke` still wins, so existing call sites keep their meaning."""
    agent = Agent(name='briefer', client=_client())
    builder = (
        agent.on_manual().key('brief').target('sample-agent', version=1).asking('Declared prompt.')
    )

    payload = builder.declaration_payload('Explicit prompt.')

    assert payload['trigger_binding'] == {'kind': 'template', 'messages': ['Explicit prompt.']}


def test_registering_without_any_prompt_says_both_ways_to_supply_one() -> None:
    """The error has to name the fix, since the declaration looks otherwise complete."""
    agent = Agent(name='briefer', client=_client())
    builder = agent.on_manual().key('brief').target('sample-agent', version=1)

    # Absent rather than null, matching how a declaration omits what it has not been told.
    assert 'trigger_binding' not in builder.declaration()

    with pytest.raises(ValueError, match=r'\.asking\(\.\.\.\)'):
        _ = builder.declaration_payload()
