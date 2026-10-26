"""Drive the control-plane and event-plane routes outside private artifacts.

Covers triggers and scenarios, served agents and agent-work claiming, the
connection-setup CLI, form publishing, the session listing and the automatic
vault's identity check. Request models are filled with non-default values so
the recorded body carries every field the SDK can send.

The terminal commands and the form publisher talk to the API through the
standard library's `http.client`, not through the SDK's httpx transport. Their
drivers swap that connection for one that hands each request to the recording
fake API, so what the CLI sends is recorded exactly like every other call.
"""

# ruff: noqa: SLF001, S105, S106
# pyright: reportPrivateUsage=false

from __future__ import annotations

import asyncio
import contextlib
import email.message
import io
import json
import tempfile
from pathlib import Path
from types import SimpleNamespace
from typing import TYPE_CHECKING, Any
from unittest import mock

import httpx

from maivn import Agent, Trigger
from maivn._internal import automatic_vault
from maivn._internal.claiming import ClaimedWork, RunReport, WorkClaimer, WorkLoop
from maivn._internal.connection_setup_cli import run_connections
from maivn.connection_events import FormPublisher
from tests.contract.api_calls.recording import BASE_URL, FakeApi, Reply

if TYPE_CHECKING:
    from collections.abc import Callable

PROJECT = 'prj_1'
PROCESS = '00000000-0000-4000-8000-000000000011'
UUID_PROJECT = '00000000-0000-4000-8000-000000000003'
NOT_FOUND = Reply(status=404, json={'detail': 'Not found.', 'code': 'not_found'})
TIMESTAMP = '2026-10-09T00:00:00Z'


def _canvas(status: str = 'enabled') -> dict[str, Any]:
    """One scenario canvas as the control plane answers it."""
    return {
        'scenario': {
            'scenario': {'scenario_id': 'scn_1', 'project_id': PROJECT},
            'trigger': {'trigger_id': 'trg_1', 'status': status},
        }
    }


def _trigger_agent(api: FakeApi) -> Agent:
    return Agent(name='triage', client=api.client())


def _script_create(api: FakeApi, builder: Any) -> None:
    """Answer the registration probe with a 404 so `ainvoke` takes the create path."""
    scenario_id = builder.declaration()['scenario_id']
    api.answer('GET', f'/v1/scenarios/{scenario_id}', NOT_FOUND)
    api.answer('POST', '/v1/scenarios', Reply(status=201, json=_canvas()))


async def trigger_sources(api: FakeApi) -> None:
    """Register one trigger per source kind, each through the create path."""
    agent = _trigger_agent(api)
    sources: list[Callable[[], Any]] = [
        agent.on_manual,
        lambda: agent.on_webhook('vault://hooks/secret_1'),
        lambda: agent.cron('0 9 * * *', tz='Europe/London', jitter=30.0, name='Morning'),
        lambda: agent.interval(_seconds(3600), jitter=15.0, name='Hourly'),
        lambda: agent.on_connection('conn_1', event_type='row.insert', table='public.orders'),
        lambda: agent.on_payment('conn_1', status='failed'),
        lambda: agent.on_pull_request(
            'conn_1', action='closed', repository='org/repo', branch='main', merged=True
        ),
        lambda: agent.on_issue('conn_1', action='labeled', repository='org/repo'),
        lambda: agent.on_push('conn_1', repository='org/repo', branch='main'),
        lambda: agent.on_workflow_run(
            'conn_1',
            repository='org/repo',
            branch='main',
            workflow='ci.yml',
            conclusion='failure',
        ),
        lambda: agent.on_invoice('conn_1', event='payment_failed'),
        lambda: agent.on_subscription('conn_1', event='trial_will_end'),
        lambda: agent.on_file_change('watch_1', connection_id='conn_1', operation='modified'),
        lambda: agent.on_form('form_1', connection_id='conn_1'),
        lambda: agent.on_email('conn_1', sender_allowlist=('ops@example.test',)),
        lambda: agent.on_event('session.completed', origin_trigger_id='trg_0'),
        lambda: agent.on_sla('paid', fire_when='condition_missing', deadline_seconds=300),
        lambda: agent.on_record_change('public.orders', connection_id='conn_1', operation='update'),
        lambda: agent.on_memory_change('skill', operation='updated'),
        lambda: agent.on_storage('artifact', operation='inserted'),
        agent.on_reply,
        agent.on_tool_fire,
    ]
    for index, make in enumerate(sources):
        builder = make().key(f'source-{index}').asking('Handle {{event.id}}')
        _script_create(api, builder)
        await builder.ainvoke()


def _seconds(count: int) -> Any:
    from datetime import timedelta  # noqa: PLC0415 - only the interval source needs it.

    return timedelta(seconds=count)


async def trigger_outputs(api: FakeApi) -> None:
    """Register one trigger per output destination."""
    agent = _trigger_agent(api)
    outputs: list[Callable[[Any], Any]] = [
        lambda builder: builder.emit('order.triaged'),
        lambda builder: builder.to_slack('C0001'),
        lambda builder: builder.to_discord('D0001'),
        lambda builder: builder.reply_by_email('thread_1'),
        lambda builder: builder.comment_on_github('org/repo', issue_ref='42'),
        lambda builder: builder.to_webhook('endpoint_1'),
        lambda builder: builder.display_in_app(),
        lambda builder: builder.to_connection('conn_out_1'),
    ]
    for index, choose in enumerate(outputs):
        builder = choose(agent.on_manual().key(f'output-{index}'))
        _script_create(api, builder)
        await builder.ainvoke(['Summarise {{event.id}}', 'Then reply'])


async def trigger_update(api: FakeApi) -> None:
    """Re-register an existing trigger: probe, then update with every mutable field."""
    agent = _trigger_agent(api)
    builder = (
        agent.on_webhook('vault://hooks/secret_1')
        .key('update-1')
        .named('Updated trigger')
        .target('agt_1', version=3)
        .where('$.event.priority == "high"')
        .with_options(
            concurrency=2,
            max_attempts=3,
            initial_delay_seconds=10,
            max_delay_seconds=600,
            backoff='fixed',
            dedupe_window_seconds=3600,
            depth_cap=4,
            fanout_cap=50,
            shadow=True,
            fire_budget=3,
        )
        .to_slack('C0001')
    )
    scenario_id = builder.declaration()['scenario_id']
    api.answer('GET', f'/v1/scenarios/{scenario_id}', Reply(json=_canvas()))
    api.answer('PATCH', f'/v1/scenarios/{scenario_id}', Reply(json=_canvas()))
    await builder.ainvoke('Handle {{event.id}}')


async def trigger_lifecycle(api: FakeApi) -> None:
    """Register a trigger, then pause, resume, fire, time, reply, replay and delete it."""
    final_event: dict[str, Any] = {
        'event_id': 'evt_1',
        'ordinal': 'ord_1',
        'type': 'final',
        'session_id': 'ses_1',
        'root_event_id': 'evt_root',
        'payload': {
            'message': {
                'message_id': 'msg_1',
                'role': 'assistant',
                'content': 'done',
                'ts': TIMESTAMP,
            },
            'usage': {},
            'stop_reason': 'end_turn',
            'tool_calls': {'count': 0, 'names': []},
        },
        'ts': TIMESTAMP,
    }
    session_events = Reply.sse(final_event)
    timer = {
        'timer_id': 'tmr_1',
        'instance_key': 'order:1',
        'condition': 'paid',
        'fire_when': 'condition_missing',
        'started_at': TIMESTAMP,
        'deadline_at': '2026-10-09T00:05:00Z',
        'condition_met_at': None,
        'processed_at': None,
        'process_outcome': None,
        'inserted': True,
    }
    reply = {
        'event_id': 'evt_reply_1',
        'conversation_id': '00000000-0000-4000-8000-000000000021',
        'reply_id': 'reply:1',
        'sequence': 1,
        'received_at': TIMESTAMP,
        'inserted': True,
    }
    session = {
        'session_id': 'ses_1',
        'project_id': PROJECT,
        'started_at': TIMESTAMP,
        'status': 'completed',
        'source': 'triggered',
    }
    fired = {'fire_id': 'fire_1', 'trigger_id': 'trg_1', 'session_id': 'ses_1'}
    builder = _trigger_agent(api).on_manual().key('lifecycle-1').asking('Handle {{event.id}}')
    _script_create(api, builder)
    api.answer('POST', '/v1/scenarios/scn_1/pause', Reply(json=_canvas('paused')))
    api.answer('POST', '/v1/scenarios/scn_1/resume', Reply(json=_canvas()))
    api.answer('GET', '/v1/sessions', Reply(json={'items': [session], 'next_cursor': None}))
    api.answer('GET', '/v1/sessions/ses_1/events', session_events)
    api.answer('POST', '/v1/scenarios/scn_1/sla-timers', Reply(status=201, json=timer))
    api.answer('GET', '/v1/scenarios/scn_1/sla-timers/order:1', Reply(json=timer))
    api.answer('POST', '/v1/scenarios/scn_1/sla-timers/order:1/satisfy', Reply(json=timer))
    api.answer('POST', '/v1/scenarios/scn_1/replies', Reply(status=201, json=reply))
    api.answer('POST', '/v1/scenarios/scn_1/fire', Reply(status=202, json=fired))
    api.answer('DELETE', '/v1/scenarios/scn_1', Reply(json={'success': True}))

    trigger: Trigger = await builder.ainvoke()
    await trigger.apause()
    await trigger.aresume()
    await trigger.ahistory(limit=5)
    _ = [event async for event in trigger.areplay('ses_1')]
    await trigger.aarm('order:1', payload={'order_id': 'ord_1'})
    await trigger.atimer('order:1')
    await trigger.asatisfy('order:1', evidence={'paid': True})
    await trigger.areply('Please continue', conversation_key='ticket/1', reply_id='reply:1')
    await trigger.afire({'subject': 'broken build'})
    _ = [event async for event in trigger.afire_stream({'subject': 'broken build'})]
    await trigger.adelete()


def _served(*, serving: str = 'serving') -> dict[str, Any]:
    return {
        'version': 1,
        'serving': serving,
        'transport': 'worker',
        'drifted': False,
        'last_seen_at': TIMESTAMP,
    }


def _announced() -> Reply:
    return Reply(status=201, json={'process_id': PROCESS, 'served': _served()})


def _serving_agent(api: FakeApi) -> Agent:
    return Agent(
        name='triage',
        description='Triages inbound reports',
        system_prompt='Triage the report.',
        client=api.client(),
    )


async def _unused_runner(work: ClaimedWork) -> str:
    _ = work
    return ''


async def serving_announce_heartbeat_stop(api: FakeApi) -> None:
    """Announce a webhook-transport process, beat once, and stop it."""
    serving_path = f'/v1/projects/{PROJECT}/agents/serving'
    api.answer('POST', serving_path, _announced())
    api.answer('PATCH', f'{serving_path}/{PROCESS}', Reply(status=204))
    api.answer('DELETE', f'{serving_path}/{PROCESS}', Reply(status=204))
    session = _serving_agent(api).serving(
        project_id=PROJECT,
        agent_id='agt_1',
        version=2,
        transport='webhook',
        endpoint_url='https://agent.example.test/hook',
    )

    await session.announce()
    await session.heartbeat()
    await session.stop()


async def serving_re_announce(api: FakeApi) -> None:
    """Re-announce the process when a heartbeat finds the platform has forgotten it."""
    serving_path = f'/v1/projects/{PROJECT}/agents/serving'
    api.answer('POST', serving_path, _announced())
    api.answer('PATCH', f'{serving_path}/{PROCESS}', NOT_FOUND)
    api.answer('DELETE', f'{serving_path}/{PROCESS}', Reply(status=204))
    session = _serving_agent(api).serving(project_id=PROJECT, runner=_unused_runner)

    await session.announce()
    await session.heartbeat()
    await session.stop()


def _claim_envelope(fire_id: str = 'fire_1') -> dict[str, Any]:
    """One hand-over exactly as the event plane encodes it."""
    return {
        'fire_id': fire_id,
        'claim_token': 7,
        'claim_until': '2026-10-09T00:02:00Z',
        'work': {
            'schema_version': 1,
            'fire_id': fire_id,
            'invocation_id': f'inv_{fire_id}',
            'trigger_id': 'trg_1',
            'organization_id': 'org_1',
            'project_id': PROJECT,
            'agent_id': 'agt_1',
            'agent_version': 1,
            'session_id': 'ses_1',
            'root_event_id': 'evt_root',
            'trigger_chain_depth': 0,
            'payload': {'subject': 'a broken build'},
            'message_binding': {'kind': 'template', 'messages': ['Triage {{event.subject}}']},
        },
    }


CLAIMS = '/v1/agent-work/claims'
IDLE_PAGE = Reply(json={'claims': [], 'poll_after_seconds': 3600.0})
REPORT_OK = Reply(json={'fire_id': 'fire_1', 'status': 'ok'})


async def claiming_direct(api: FakeApi) -> None:
    """Claim work, then report every status the platform accepts."""
    api.answer(
        'POST', CLAIMS, Reply(json={'claims': [_claim_envelope()], 'poll_after_seconds': 2.0})
    )
    api.answer('POST', f'{CLAIMS}/fire_1', REPORT_OK)
    claimer = WorkClaimer(api.client(), process_id=PROCESS)

    claims, _wait = await claimer.claim(max_fires=3)
    work = claims[0]
    await claimer.report(work, 'working')
    await claimer.report(
        work,
        'completed',
        output='triaged',
        reason='finished',
        usage={'input_tokens': 10, 'output_tokens': 5, 'model': 'balanced'},
    )
    await claimer.report(work, 'failed', reason='the runner raised')
    await claimer.report(work, 'abandoned', reason='serving process stopped')


async def _run_until(loop: WorkLoop, ready: asyncio.Event) -> None:
    """Run a claim loop until `ready` is set, then cancel it the way Ctrl-C does."""
    task = asyncio.ensure_future(loop.run())
    _ = await ready.wait()
    _ = task.cancel()
    _ = await asyncio.gather(task, return_exceptions=True)


def _stop_on(ready: asyncio.Event, event_name: str) -> Callable[[str, str, str | None], None]:
    def listener(fire_id: str, event: str, detail: str | None) -> None:
        _ = (fire_id, detail)
        if event == event_name:
            ready.set()

    return listener


async def claiming_loop_completed(api: FakeApi) -> None:
    """Run the work a claim loop is handed, report usage, then idle."""
    api.answer(
        'POST',
        CLAIMS,
        Reply(json={'claims': [_claim_envelope()], 'poll_after_seconds': 0.0}),
        IDLE_PAGE,
    )
    api.answer('POST', f'{CLAIMS}/fire_1', REPORT_OK)
    ready = asyncio.Event()

    async def runner(work: ClaimedWork) -> RunReport:
        return RunReport(
            output=f'triaged {work.messages()[0]}',
            usage={'input_tokens': 10, 'output_tokens': 5, 'model': 'balanced'},
        )

    loop = WorkLoop(
        WorkClaimer(api.client(), process_id=PROCESS),
        runner,
        keepalive_interval_seconds=3600.0,
        listener=_stop_on(ready, 'idle'),
    )
    await _run_until(loop, ready)


async def claiming_loop_failed(api: FakeApi) -> None:
    """Report a runner that raises as failed, with its reason."""
    api.answer(
        'POST',
        CLAIMS,
        Reply(json={'claims': [_claim_envelope()], 'poll_after_seconds': 0.0}),
        IDLE_PAGE,
    )
    api.answer('POST', f'{CLAIMS}/fire_1', REPORT_OK)
    ready = asyncio.Event()

    async def runner(work: ClaimedWork) -> str:
        _ = work
        message = 'the runner raised'
        raise RuntimeError(message)

    loop = WorkLoop(
        WorkClaimer(api.client(), process_id=PROCESS),
        runner,
        keepalive_interval_seconds=3600.0,
        listener=_stop_on(ready, 'idle'),
    )
    await _run_until(loop, ready)


async def claiming_loop_abandoned(api: FakeApi) -> None:
    """Hand the claim back as abandoned when the loop stops mid-run."""
    api.answer(
        'POST', CLAIMS, Reply(json={'claims': [_claim_envelope()], 'poll_after_seconds': 0.0})
    )
    api.answer('POST', f'{CLAIMS}/fire_1', REPORT_OK)
    ready = asyncio.Event()

    async def runner(work: ClaimedWork) -> str:
        _ = work
        await asyncio.sleep(3600)
        return ''

    loop = WorkLoop(
        WorkClaimer(api.client(), process_id=PROCESS),
        runner,
        keepalive_interval_seconds=3600.0,
        listener=_stop_on(ready, 'claimed'),
    )
    await _run_until(loop, ready)


async def serving_run(api: FakeApi) -> None:
    """Serve until stopped: announce, claim and report one fire, then stop cleanly."""
    serving_path = f'/v1/projects/{PROJECT}/agents/serving'
    api.answer('POST', serving_path, _announced())
    api.answer('DELETE', f'{serving_path}/{PROCESS}', Reply(status=204))
    api.answer(
        'POST',
        CLAIMS,
        Reply(json={'claims': [_claim_envelope()], 'poll_after_seconds': 0.0}),
        IDLE_PAGE,
    )
    api.answer('POST', f'{CLAIMS}/fire_1', REPORT_OK)
    ready = asyncio.Event()

    async def runner(work: ClaimedWork) -> RunReport:
        _ = work
        return RunReport(output='triaged', usage={'input_tokens': 10, 'output_tokens': 5})

    session = _serving_agent(api).serving(
        project_id=PROJECT,
        heartbeat_interval_seconds=3600.0,
        runner=runner,
        claim_listener=_stop_on(ready, 'idle'),
    )
    task = asyncio.ensure_future(session.run())
    _ = await ready.wait()
    _ = task.cancel()
    _ = await asyncio.gather(task, return_exceptions=True)


class _Connection:
    """Stands in for `http.client.HTTPSConnection`, answering from the fake API."""

    def __init__(self, api: FakeApi, host: str) -> None:
        self._api = api
        self._host = host
        self._request: httpx.Request | None = None

    def request(
        self,
        method: str,
        path: str,
        body: bytes | None = None,
        headers: dict[str, str] | None = None,
    ) -> None:
        self._request = httpx.Request(
            method, f'https://{self._host}{path}', content=body, headers=headers
        )

    def getresponse(self) -> _Response:
        request = self._request
        assert request is not None
        response = asyncio.run(self._api.transport.handle_async_request(request))
        return _Response(response)

    def close(self) -> None:
        """Nothing to release."""


class _Response:
    def __init__(self, response: httpx.Response) -> None:
        self.status = response.status_code
        self._content = response.content
        self._headers = response.headers

    def read(self, amount: int) -> bytes:
        return self._content[:amount]

    def info(self) -> email.message.Message:
        message = email.message.Message()
        for value in self._headers.get_list('set-cookie'):
            message['Set-Cookie'] = value
        return message


@contextlib.contextmanager
def _routed_through(api: FakeApi, stdin: str = '') -> Any:
    """Send the standard library's HTTPS connections to the fake API; feed `stdin`."""

    def connect(host: str, port: int | None = None, timeout: float | None = None) -> _Connection:
        _ = (port, timeout)
        return _Connection(api, host)

    with (
        mock.patch('http.client.HTTPSConnection', connect),
        mock.patch('sys.stdin', io.StringIO(stdin)),
        contextlib.redirect_stdout(io.StringIO()),
    ):
        yield


def _connection(**extra: Any) -> dict[str, Any]:
    return {
        'connection': {
            'connection_id': 'conn_1',
            'project_id': UUID_PROJECT,
            'status': 'active',
            **extra,
        },
        'callback_path': '/v1/connections/conn_1/events',
        'inbound_url': f'{BASE_URL}/v1/connections/conn_1/events',
    }


async def connection_setup_cli(api: FakeApi) -> None:
    """Create one connection per setup shape through the terminal command's code."""
    origin = BASE_URL
    api.answer(
        'GET',
        '/v1/auth/csrf',
        Reply(
            json={'csrf_header_name': 'x-csrf-token', 'csrf_token': 'csrf_1'},
            headers={'set-cookie': 'session=human; Path=/; Secure'},
        ),
    )
    api.answer('POST', '/v1/auth/login', Reply(json={'csrf_header_name': 'x-csrf-token'}))
    api.answer('POST', '/v1/auth/logout', Reply(json={'success': True}))
    local_part = 'p-' + '0123456789abcdef' * 2
    email_fields = {
        'dropzone_local_part': local_part,
        'email_address': f'{local_part}@inbound.example.test',
    }
    # (kind, extra arguments, private fields on stdin, extra connection fields in the reply)
    shapes: list[tuple[str, list[str], dict[str, str], dict[str, Any], bool]] = [
        ('form_webhook', [], {}, {}, True),
        ('email_inbox', [], {}, email_fields, True),
        ('slack_app', [], {'signing_secret': 'signing-secret-1'}, {}, False),
        ('github_app', [], {'webhook_secret': 'webhook-secret-1'}, {}, False),
        (
            'stripe_webhook',
            ['--stripe-mode', 'live'],
            {'webhook_secret': 'webhook-secret-1'},
            {},
            False,
        ),
        (
            'outgoing_webhook',
            [],
            {'endpoint_url': 'https://hooks.example.test/in'},
            {},
            False,
        ),
        (
            'discord_webhook',
            [
                '--discord-application-id',
                '123456789012345678',
                '--discord-public-key',
                'ab' * 32,
                '--discord-protocol',
                'interactions',
            ],
            {},
            {},
            False,
        ),
    ]
    with tempfile.TemporaryDirectory() as folder:
        for index, (kind, extra, private, connection_fields, minted) in enumerate(shapes):
            body = _connection(**connection_fields)
            if minted:
                body['one_time_secret'] = 'one-time-secret-1'
            api.answer(
                'POST', f'/v1/projects/{UUID_PROJECT}/connections', Reply(status=201, json=body)
            )
            arguments = [
                'create',
                '--base-url',
                origin,
                '--project-id',
                UUID_PROJECT,
                '--email',
                'developer@example.test',
                '--name',
                'Terminal setup',
                '--kind',
                kind,
                '--credentials-stdin',
                *extra,
            ]
            if minted:
                destination = Path(folder) / f'private-{index}' / 'connection.json'
                arguments += ['--secret-file', str(destination)]
            stdin = json.dumps({'password': 'a-long-test-password', **private}) + '\n'
            with _routed_through(api, stdin):
                await asyncio.to_thread(run_connections, arguments)


async def form_publisher(api: FakeApi) -> None:
    """Publish one signed form submission to a saved connection's receiver."""
    api.answer('POST', '/v1/connections/conn_1/events', Reply(status=202, json={'accepted': True}))
    publisher = FormPublisher(
        receiver_url=f'{BASE_URL}/v1/connections/conn_1/events',
        connection_id='conn_1',
        secret='form-signing-secret-1',
    )
    with _routed_through(api):
        await asyncio.to_thread(
            publisher.submit, 'form_1', 'submission_1', {'name': 'Ada', 'topic': 'billing'}
        )


class _MissingRecordError(Exception):
    """Stands in for the native vault's missing-record error."""


class _NativeVault:
    """A native vault that keeps its records in memory."""

    def __init__(self, scope: str) -> None:
        self.scope = scope
        self.records: dict[str, bytes] = {}

    @classmethod
    def default_exists(cls, scope: str | None = None, *, path: str | None = None) -> bool:
        _ = (scope, path)
        return True

    @classmethod
    def open_default(
        cls, scope: str, *, path: str | None = None, secret: bytes | None = None
    ) -> _NativeVault:
        _ = (path, secret)
        return cls(scope)

    def merge_value_map(self, tenant: str, record: str, payload: bytes) -> None:
        _ = tenant
        prior = json.loads(self.records.get(record, b'{}'))
        prior.update(json.loads(payload))
        self.records[record] = json.dumps(prior).encode()

    def load_value_map(self, tenant: str, record: str) -> bytes:
        _ = tenant
        try:
            return self.records[record]
        except KeyError:
            raise _MissingRecordError from None

    def purge(self, tenant: str, record: str) -> None:
        _ = tenant
        self.records.pop(record, None)


async def vault_identity(api: FakeApi) -> None:
    """Ask who the caller is before the automatic vault opens local storage."""
    api.answer(
        'GET',
        '/v1/vault/identity',
        Reply(
            json={
                'user_id': '00000000-0000-4000-8000-000000000001',
                'org_id': '00000000-0000-4000-8000-000000000002',
                'project_id': UUID_PROJECT,
            }
        ),
    )
    native = SimpleNamespace(LocalVault=_NativeVault, RecordNotFound=_MissingRecordError)
    with (
        mock.patch.object(automatic_vault, '_native', return_value=native),
        mock.patch('maivn._internal.private_data_store._vault_module', return_value=native),
    ):
        # Each client opens its own store, so each asks for the caller's identity once.
        await api.client()._thread_private_data('thr_1', {'name': 'Ada'})
        await api.client().aforget_private_data('thr_1')


DRIVERS = (
    trigger_sources,
    trigger_outputs,
    trigger_update,
    trigger_lifecycle,
    serving_announce_heartbeat_stop,
    serving_re_announce,
    claiming_direct,
    claiming_loop_completed,
    claiming_loop_failed,
    claiming_loop_abandoned,
    serving_run,
    connection_setup_cli,
    form_publisher,
    vault_identity,
)
