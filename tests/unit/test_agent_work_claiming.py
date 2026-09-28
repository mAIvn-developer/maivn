"""Contracts for a serving process pulling, running and reporting its work.

Announcing makes a process reachable; this is what actually reaches it. The
worker transport is pull by default, so a process that only heartbeats is an
agent that looks healthy and never receives anything - which is exactly the gap
these tests close.

There is no live platform here. One stubbed transport answers the control
plane's three serving routes, the event plane's two claim routes, and the data
plane's invoke, so a claim can be followed all the way to a real agent run and
back.
"""

from __future__ import annotations

import asyncio
import json
from http import HTTPStatus
from typing import TYPE_CHECKING, Any

import httpx
import pytest
from pydantic import AnyUrl

from maivn import Agent, Client, ClientConfig, MaivnHTTPError
from maivn._internal.claiming import (
    CLAIMS_PATH,
    ClaimedWork,
    RunReport,
    WorkClaimer,
    WorkLoop,
    usage_report,
)

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence

    from maivn._internal.claiming import ClaimEvent

_PROJECT_ID = '11111111-1111-1111-1111-111111111111'
_ORIGIN = 'https://platform.example'
_SERVING_PATH = f'/v1/projects/{_PROJECT_ID}/agents/serving'
_FIRE_ID = 'fire-1'
_TEMPLATE = 'Triage {{event.subject}} from {{event.sender.name}}'

# Named so the assertions read as counts of attempts rather than magic numbers.
_TWO_REFUSALS_THEN_WORK = 3
_TWO_FIRES = 2
# A guard on the busy-wait below: the keepalive is what this test waits for, and
# a broken one must fail the assertion rather than spin the suite.
_KEEPALIVE_REPORT_BUDGET = 20


def _claim_envelope(fire_id: str = _FIRE_ID, token: int = 7) -> dict[str, Any]:
    """One hand-over exactly as the event plane encodes it."""
    return {
        'fire_id': fire_id,
        'claim_token': token,
        'claim_until': '2026-09-04T12:02:00Z',
        'work': {
            'schema_version': 1,
            'fire_id': fire_id,
            'invocation_id': f'inv-{fire_id}',
            'trigger_id': 'trg-a',
            'organization_id': 'org-a',
            'project_id': _PROJECT_ID,
            'agent_id': 'triage',
            'agent_version': 1,
            'session_id': f'ses-{fire_id}',
            'root_event_id': 'evt-1',
            'trigger_chain_depth': 0,
            'payload': {'subject': 'a broken build', 'sender': {'name': 'CI'}},
            'message_binding': {'kind': 'template', 'messages': [_TEMPLATE]},
        },
    }


class _PlatformStub:
    """One transport standing in for all three planes a serving process talks to."""

    def __init__(
        self,
        *,
        claim_pages: Sequence[object] = (),
        report_statuses: Sequence[int] = (),
        final_text: str = 'triaged',
        final_usage: Mapping[str, object] | None = None,
    ) -> None:
        """Queue what the platform hands over and how it answers each report."""
        self.serving_calls: list[tuple[str, str]] = []
        self.claim_calls: list[dict[str, Any]] = []
        self.reports: list[dict[str, Any]] = []
        self.invokes: list[dict[str, Any]] = []
        self._claim_pages = list(claim_pages)
        self._report_statuses = list(report_statuses)
        self._final_text = final_text
        self._final_usage = dict(final_usage or {})
        self._announced = 0

    def __call__(self, request: httpx.Request) -> httpx.Response:
        """Answer one request on whichever plane it was addressed to."""
        path = request.url.path
        if path == CLAIMS_PATH:
            return self._claim(request)
        if path.startswith(CLAIMS_PATH + '/'):
            return self._report(request)
        if path == '/v1/invoke':
            return self._invoke(request)
        if path.startswith('/v1/sessions/'):
            return self._events()
        return self._serving(request)

    def _claim(self, request: httpx.Request) -> httpx.Response:
        self.claim_calls.append(_body(request))
        page: object = self._claim_pages.pop(0) if self._claim_pages else []
        if isinstance(page, int):
            return httpx.Response(page, json={'detail': 'Claim refused.', 'code': 'claim_refused'})
        return httpx.Response(
            HTTPStatus.OK,
            json={'claims': page, 'poll_after_seconds': 0.0},
        )

    def _report(self, request: httpx.Request) -> httpx.Response:
        body = _body(request)
        self.reports.append(body)
        status = self._report_statuses.pop(0) if self._report_statuses else HTTPStatus.OK
        if status != HTTPStatus.OK:
            return httpx.Response(
                status, json={'detail': 'This work claim is no longer held.', 'code': 'claim_lost'}
            )
        return httpx.Response(
            HTTPStatus.OK,
            json={'fire_id': body.get('fire_id', ''), 'status': 'ok'},
        )

    def _invoke(self, request: httpx.Request) -> httpx.Response:
        self.invokes.append(_body(request))
        return httpx.Response(
            HTTPStatus.ACCEPTED,
            json={'session_id': 'ses-run', 'stream_position': 0},
        )

    def _events(self) -> httpx.Response:
        payload = json.dumps(
            {
                'event_id': 'evt-final',
                'ordinal': 'ord-1',
                'type': 'final',
                'session_id': 'ses-run',
                'root_event_id': 'evt-root',
                'payload': {
                    'message': {
                        'message_id': 'msg-final',
                        'role': 'assistant',
                        'content': self._final_text,
                        'ts': '2026-09-04T12:00:00Z',
                    },
                    'usage': self._final_usage,
                    'stop_reason': 'end_turn',
                    'tool_calls': {'count': 0, 'names': []},
                },
                'ts': '2026-09-04T12:00:00Z',
            },
        )
        return httpx.Response(
            HTTPStatus.OK,
            headers={'content-type': 'text/event-stream'},
            content=f'id: 1\nevent: final\ndata: {payload}\n\n',
        )

    def _serving(self, request: httpx.Request) -> httpx.Response:
        self.serving_calls.append((request.method, request.url.path))
        if request.method == 'POST':
            self._announced += 1
            return httpx.Response(
                HTTPStatus.CREATED,
                json={
                    'process_id': f'prc-{self._announced}',
                    'served': {'version': 1, 'serving': 'serving'},
                },
            )
        return httpx.Response(HTTPStatus.NO_CONTENT)

    @property
    def serving_methods(self) -> list[str]:
        """Return the control-plane verbs seen, in order."""
        return [method for method, _path in self.serving_calls]

    def reports_of(self, status: str) -> list[dict[str, Any]]:
        """Return every report of one kind."""
        return [report for report in self.reports if report.get('status') == status]


def _body(request: httpx.Request) -> dict[str, Any]:
    return json.loads(request.content.decode('utf-8'))


def _agent(stub: _PlatformStub) -> Agent:
    return Agent(
        name='triage',
        description='Triages inbound reports',
        system_prompt='You are the triage agent.',
        client=Client(
            config=ClientConfig(api_key='test-key', base_url=AnyUrl(_ORIGIN)),
            transport=httpx.MockTransport(stub),
        ),
    )


async def _serve_until(
    agent: Agent,
    stub: _PlatformStub,
    ready: asyncio.Event,
    **options: Any,
) -> None:
    """Run agent.serve() until `ready` fires, then Ctrl-C it."""
    _ = stub
    task = asyncio.ensure_future(
        agent.serve(project_id=_PROJECT_ID, heartbeat_interval_seconds=0.0, **options),
    )
    try:
        await asyncio.wait_for(ready.wait(), timeout=5.0)
    finally:
        _ = task.cancel()
        # Bounded on purpose: a serving process that swallows its own
        # cancellation must fail this suite rather than hang it.
        _ = await asyncio.wait_for(
            asyncio.gather(task, return_exceptions=True),
            timeout=5.0,
        )


def test_a_claimed_fire_runs_against_the_developers_agent_and_is_reported() -> None:
    """The whole loop: claim, render what the trigger asked, run it here, report back."""
    stub = _PlatformStub(claim_pages=[[_claim_envelope()]], final_text='triaged the build')
    agent = _agent(stub)
    done = asyncio.Event()

    def watch(_fire_id: str, event: ClaimEvent, _detail: str | None) -> None:
        if event == 'completed':
            done.set()

    asyncio.run(_serve_until(agent, stub, done, claim_listener=watch))

    assert stub.claim_calls[0]['process_id'] == 'prc-1'
    # The agent ran against what the trigger declared, rendered from the event.
    assert len(stub.invokes) == 1
    sent = json.dumps(stub.invokes[0])
    assert 'Triage a broken build from CI' in sent
    completed = stub.reports_of('completed')
    assert len(completed) == 1
    assert completed[0]['claim_token'] == _claim_envelope()['claim_token']
    assert completed[0]['output'] == 'triaged the build'
    assert stub.serving_methods[-1] == 'DELETE'


def test_the_heartbeat_keeps_running_while_the_agent_is_working() -> None:
    """Neither loop starves the other: staying announced does not wait on a run."""
    stub = _PlatformStub(claim_pages=[[_claim_envelope()]])
    agent = _agent(stub)
    running = asyncio.Event()
    release = asyncio.Event()
    beats_during_work: list[int] = []

    async def slow_runner(_work: ClaimedWork) -> str:
        running.set()
        await release.wait()
        return 'eventually'

    async def drive() -> None:
        task = asyncio.ensure_future(
            agent.serve(
                project_id=_PROJECT_ID,
                heartbeat_interval_seconds=0.0,
                runner=slow_runner,
            ),
        )
        await asyncio.wait_for(running.wait(), timeout=5.0)
        before = stub.serving_methods.count('PATCH')
        # The run is still in flight. If the heartbeat shared this coroutine it
        # would be stuck behind it, and this count would never move.
        for _ in range(50):
            await asyncio.sleep(0)
        beats_during_work.append(stub.serving_methods.count('PATCH') - before)
        release.set()
        await asyncio.sleep(0)
        _ = task.cancel()
        _ = await asyncio.wait_for(
            asyncio.gather(task, return_exceptions=True),
            timeout=5.0,
        )

    asyncio.run(drive())

    assert beats_during_work[0] > 0
    assert stub.serving_methods[-1] == 'DELETE'


def test_ctrl_c_hands_back_an_unfinished_claim_and_still_stops_cleanly() -> None:
    """A fire taken but not finished becomes reclaimable rather than lost."""
    stub = _PlatformStub(claim_pages=[[_claim_envelope()]])
    agent = _agent(stub)
    running = asyncio.Event()

    async def never_finishes(_work: ClaimedWork) -> str:
        running.set()
        await asyncio.Event().wait()
        return 'unreachable'

    asyncio.run(_serve_until(agent, stub, running, runner=never_finishes))

    abandoned = stub.reports_of('abandoned')
    assert len(abandoned) == 1
    assert abandoned[0]['claim_token'] == _claim_envelope()['claim_token']
    assert not stub.reports_of('completed')
    assert stub.serving_methods[-1] == 'DELETE'
    assert stub.serving_calls[-1] == ('DELETE', f'{_SERVING_PATH}/prc-1')


def test_a_transient_claim_failure_costs_a_pause_and_not_the_process() -> None:
    """One bad network moment must not take the serving process down with it."""
    stub = _PlatformStub(
        claim_pages=[
            int(HTTPStatus.SERVICE_UNAVAILABLE),
            int(HTTPStatus.SERVICE_UNAVAILABLE),
            [_claim_envelope()],
        ],
    )
    agent = _agent(stub)
    done = asyncio.Event()

    def watch(_fire_id: str, event: ClaimEvent, _detail: str | None) -> None:
        if event == 'completed':
            done.set()

    asyncio.run(_serve_until(agent, stub, done, claim_listener=watch))

    assert len(stub.claim_calls) >= _TWO_REFUSALS_THEN_WORK
    assert len(stub.reports_of('completed')) == 1
    assert stub.serving_methods[-1] == 'DELETE'


def test_a_refused_claim_stops_the_whole_process_and_says_so() -> None:
    """A revoked key is not a blip: it ends the run loudly and still stops cleanly."""
    stub = _PlatformStub(claim_pages=[int(HTTPStatus.FORBIDDEN)])
    agent = _agent(stub)

    async def drive() -> None:
        await agent.serve(project_id=_PROJECT_ID, heartbeat_interval_seconds=30.0)

    with pytest.raises(MaivnHTTPError) as refusal:
        asyncio.run(drive())

    assert refusal.value.status_code == HTTPStatus.FORBIDDEN
    assert stub.serving_methods[-1] == 'DELETE'


def test_an_agent_that_raises_is_a_failed_run_not_a_dead_loop() -> None:
    """A developer's agent may raise; that ends the run, never the process."""
    stub = _PlatformStub(claim_pages=[[_claim_envelope('fire-1')], [_claim_envelope('fire-2', 9)]])
    agent = _agent(stub)
    second = asyncio.Event()
    seen: list[str] = []

    async def sometimes_raises(work: ClaimedWork) -> str:
        seen.append(work.fire_id)
        if work.fire_id == 'fire-1':
            message = 'the tool exploded'
            raise RuntimeError(message)
        second.set()
        return 'recovered'

    asyncio.run(_serve_until(agent, stub, second, runner=sometimes_raises))

    failed = stub.reports_of('failed')
    assert len(failed) == 1
    assert 'the tool exploded' in failed[0]['reason']
    assert seen == ['fire-1', 'fire-2']


def test_a_lost_claim_is_not_reported_again_and_does_not_stop_the_loop() -> None:
    """Somebody else finished it. Say nothing more and carry on."""
    stub = _PlatformStub(
        claim_pages=[[_claim_envelope('fire-1')], [_claim_envelope('fire-2', 9)]],
        report_statuses=[int(HTTPStatus.CONFLICT)],
    )
    agent = _agent(stub)
    second = asyncio.Event()
    released: list[str] = []

    async def quick(_work: ClaimedWork) -> str:
        return 'done'

    def watch(fire_id: str, event: ClaimEvent, _detail: str | None) -> None:
        if event == 'released':
            released.append(fire_id)
        if fire_id == 'fire-2' and event == 'completed':
            second.set()

    asyncio.run(_serve_until(agent, stub, second, runner=quick, claim_listener=watch))

    assert released == ['fire-1']
    # One report per fire: a lost claim is not retried into somebody else's run.
    assert len(stub.reports_of('completed')) == _TWO_FIRES
    assert stub.serving_methods[-1] == 'DELETE'


def test_a_long_run_keeps_its_lease_alive() -> None:
    """A run longer than the lease says 'working' rather than losing the fire."""
    stub = _PlatformStub()
    client = Client(
        config=ClientConfig(api_key='test-key', base_url=AnyUrl(_ORIGIN)),
        transport=httpx.MockTransport(stub),
    )
    release = asyncio.Event()
    extended = asyncio.Event()

    async def slow(_work: ClaimedWork) -> str:
        await release.wait()
        return 'done'

    def watch(_fire_id: str, _event: ClaimEvent, _detail: str | None) -> None:
        return

    async def drive() -> None:
        loop = WorkLoop(
            WorkClaimer(client, process_id='prc-1'),
            slow,
            keepalive_interval_seconds=0.0,
            listener=watch,
        )
        work = ClaimedWork(
            fire_id=_FIRE_ID,
            claim_token=7,
            invocation_id='inv-1',
            trigger_id='trg-a',
            agent_id='triage',
            agent_version=1,
            session_id='ses-1',
            root_event_id='evt-1',
        )
        task = asyncio.ensure_future(loop._perform(work))  # pyright: ignore[reportPrivateUsage]  # noqa: SLF001 - the keepalive is
        # deliberately private; this test is the one place its behaviour is pinned.
        while not stub.reports_of('working'):
            await asyncio.sleep(0)
            if len(stub.reports) > _KEEPALIVE_REPORT_BUDGET:
                break
        extended.set()
        release.set()
        await task

    asyncio.run(drive())

    assert extended.is_set()
    assert stub.reports_of('working')
    assert len(stub.reports_of('completed')) == 1


def test_the_binding_the_trigger_declared_is_what_the_agent_is_asked() -> None:
    """Rendering happens here because only this process can do it."""
    work = ClaimedWork(
        fire_id=_FIRE_ID,
        claim_token=1,
        invocation_id='inv-1',
        trigger_id='trg-a',
        agent_id='triage',
        agent_version=1,
        session_id='ses-1',
        root_event_id='evt-1',
        payload={'subject': 'a broken build', 'sender': {'name': 'CI'}, 'count': 3},
        binding={'kind': 'template', 'messages': [_TEMPLATE, 'seen {{event.count}} times']},
    )

    assert work.messages() == ['Triage a broken build from CI', 'seen 3 times']


def test_an_unrenderable_binding_hands_the_agent_the_event_itself() -> None:
    """A binding this SDK cannot apply must not silently produce an empty prompt."""
    payload = {'subject': 'a broken build'}
    callable_binding = ClaimedWork(
        fire_id=_FIRE_ID,
        claim_token=1,
        invocation_id='inv-1',
        trigger_id='trg-a',
        agent_id='triage',
        agent_version=1,
        session_id='ses-1',
        root_event_id='evt-1',
        payload=payload,
        binding={'kind': 'callable', 'module_path': 'app.bindings', 'qualified_name': 'build'},
    )
    no_binding = ClaimedWork(
        fire_id=_FIRE_ID,
        claim_token=1,
        invocation_id='inv-1',
        trigger_id='trg-a',
        agent_id='triage',
        agent_version=1,
        session_id='ses-1',
        root_event_id='evt-1',
        payload=payload,
    )

    for work in (callable_binding, no_binding):
        assert work.messages() == [json.dumps(payload, sort_keys=True)]


def test_claimed_work_renders_whole_event_and_treats_field_text_literally() -> None:
    """The serving boundary shares the hosted renderer without expanding inserted text."""
    payload = {'text': '{{event.secret}}', 'secret': 'value'}
    work = ClaimedWork(
        fire_id=_FIRE_ID,
        claim_token=1,
        invocation_id='inv-1',
        trigger_id='trg-a',
        agent_id='triage',
        agent_version=1,
        session_id='ses-1',
        root_event_id='evt-1',
        payload=payload,
        binding={
            'kind': 'template',
            'messages': ['{{event.text}} {{event.secret}}', '{{event}}'],
        },
    )
    assert work.messages() == ['{{event.secret}} value', json.dumps(payload, sort_keys=True)]


def test_a_placeholder_with_nothing_behind_it_is_left_visible() -> None:
    """A prompt that quietly loses half its question is harder to notice than a hole."""
    work = ClaimedWork(
        fire_id=_FIRE_ID,
        claim_token=1,
        invocation_id='inv-1',
        trigger_id='trg-a',
        agent_id='triage',
        agent_version=1,
        session_id='ses-1',
        root_event_id='evt-1',
        payload={'subject': 'a broken build'},
        binding={'kind': 'template', 'messages': ['Triage {{event.subject}} for {{event.owner}}']},
    )

    assert work.messages() == ['Triage a broken build for {{event.owner}}']


def test_a_webhook_transport_process_never_claims() -> None:
    """A process the platform pushes to must not also pull, or it runs a fire twice."""
    stub = _PlatformStub(claim_pages=[[_claim_envelope()]])
    session = _agent(stub).serving(
        project_id=_PROJECT_ID,
        transport='webhook',
        endpoint_url='https://agent.example/hooks/triage',
        heartbeat_interval_seconds=0.0,
    )

    async def announce_and_beat() -> None:
        async with session:
            _ = await session.heartbeat()

    asyncio.run(announce_and_beat())

    assert stub.claim_calls == []
    assert stub.serving_methods == ['POST', 'PATCH', 'DELETE']


def test_the_default_runner_reports_what_the_run_consumed() -> None:
    """A served run meters like any other, and only this process can count it.

    Owner ruling 2026-09-04: every run persists in the ledger, served runs
    included - and a run in the ledger that no meter can see is a run the
    platform bills nobody for. The tokens were spent here, against this
    process's own client, so the report is the only place they can come from.
    """
    stub = _PlatformStub(
        claim_pages=[[_claim_envelope()]],
        final_usage={'input_tokens': 1200, 'output_tokens': 340, 'cache_read_tokens': 64},
    )
    agent = _agent(stub)
    done = asyncio.Event()

    def watch(_fire_id: str, event: ClaimEvent, _detail: str | None) -> None:
        if event == 'completed':
            done.set()

    asyncio.run(_serve_until(agent, stub, done, claim_listener=watch))

    completed = stub.reports_of('completed')
    assert len(completed) == 1
    assert completed[0]['usage'] == {
        'input_tokens': 1200,
        'output_tokens': 340,
        'cache_read_input_tokens': 64,
    }


def test_a_runner_that_returns_a_string_reports_no_usage() -> None:
    """A runner that cannot count says nothing rather than inventing a zero.

    The field is omitted, not sent as null: the report body is strict about
    fields it does not know, so an SDK talking to a platform that predates
    usage reporting must not lose a completed run over an empty meter.
    """
    stub = _PlatformStub(claim_pages=[[_claim_envelope()]])
    agent = _agent(stub)
    done = asyncio.Event()

    async def plain_runner(_work: ClaimedWork) -> str:
        return 'answered without counting'

    def watch(_fire_id: str, event: ClaimEvent, _detail: str | None) -> None:
        if event == 'completed':
            done.set()

    asyncio.run(_serve_until(agent, stub, done, runner=plain_runner, claim_listener=watch))

    completed = stub.reports_of('completed')
    assert len(completed) == 1
    assert completed[0]['output'] == 'answered without counting'
    assert 'usage' not in completed[0]


def test_a_nonsense_reading_is_dropped_here_and_the_run_still_lands() -> None:
    """Neither end may lose a finished run over a bad meter reading.

    The platform validates this again on arrival, and has to - a third-party
    client can post anything. But a figure this SDK already knows is unusable
    should never go out as though this process stood behind it.
    """
    stub = _PlatformStub(claim_pages=[[_claim_envelope()]])
    agent = _agent(stub)
    done = asyncio.Event()

    async def confused_runner(_work: ClaimedWork) -> RunReport:
        return RunReport(
            output='the answer',
            usage={'input_tokens': 'lots', 'output_tokens': -5, 'cache_read_tokens': 3.5},
        )

    def watch(_fire_id: str, event: ClaimEvent, _detail: str | None) -> None:
        if event == 'completed':
            done.set()

    asyncio.run(_serve_until(agent, stub, done, runner=confused_runner, claim_listener=watch))

    completed = stub.reports_of('completed')
    assert len(completed) == 1
    assert completed[0]['output'] == 'the answer'
    assert 'usage' not in completed[0]


def test_a_partly_usable_reading_keeps_the_counts_that_survive() -> None:
    """One bad bucket loses that bucket, not the whole reading."""
    report = usage_report(
        {'input_tokens': 900, 'output_tokens': 'unknown', 'model': ' a-model '},
    )

    assert report == {'input_tokens': 900, 'model': 'a-model'}


def test_usage_report_speaks_the_names_invoke_already_publishes() -> None:
    """InvokeResponse.usage has published the short cache names since v1."""
    report = usage_report({'cache_read_tokens': 7, 'cache_creation_tokens': 3})

    assert report == {'cache_read_input_tokens': 7, 'cache_creation_input_tokens': 3}


def test_usage_report_says_nothing_when_no_count_survives() -> None:
    """A model name with no tokens behind it is not a meter reading."""
    assert usage_report(None) is None
    assert usage_report({}) is None
    assert usage_report({'model': 'a-model'}) is None
    assert usage_report({'input_tokens': True}) is None
