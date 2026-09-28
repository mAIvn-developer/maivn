"""Contracts for announcing a locally running agent and staying announced.

Owner ruling 2026-09-04: we do not host agents. Publishing a version is a
running process announcing itself, so the tests that matter here are about what
crosses the wire (identity and a fingerprint, never the definition) and about
what a serving process survives (a blipped network, a platform that forgot it).

There is no live control plane to talk to - its half of this contract is being
built in parallel - so every test drives a stubbed transport that answers the
pinned routes.
"""

from __future__ import annotations

import asyncio
import json
from http import HTTPStatus
from typing import TYPE_CHECKING, Any
from uuid import UUID

import httpx
import pytest
from maivn_contracts.agents.fingerprint import definition_digest
from pydantic import AnyUrl

from maivn import Agent, Client, ClientConfig, ConfigurationError, MaivnHTTPError
from maivn._internal import serving
from maivn._internal.claiming import CLAIMS_PATH
from maivn._internal.serving import (
    HEARTBEAT_INTERVAL_SECONDS,
    ServingEvent,
    ServingSession,
    build_serving_manifest,
    process_instance_id,
)

if TYPE_CHECKING:
    from collections.abc import Sequence

_PROJECT_ID = '11111111-1111-1111-1111-111111111111'
_CONTROL_ORIGIN = 'https://control.example'
_SERVING_PATH = f'/v1/projects/{_PROJECT_ID}/agents/serving'
_INSTRUCTIONS = 'You are the triage agent. The escalation runbook is secret.'
_TOOL_DESCRIPTION = 'Look up one case by its internal reference.'

# The control plane forgets a process it has not heard from within
# DEFAULT_STALE_AFTER, which is 90 seconds in its own agents/serving.py. It is
# duplicated as a literal because the SDK is forbidden from importing a service
# package; the point of the assertion below is that the interval keeps room for
# a missed beat inside that window.
_PLATFORM_STALE_AFTER_SECONDS = 90.0

# Named so the assertions read as counts of attempts rather than magic numbers.
_TWO_RETRIES_THEN_SUCCESS = 3
_ANNOUNCE_THEN_RE_ANNOUNCE = 2

# Everything ServingManifest is allowed to carry. Anything outside this set is
# definition material that must never reach the platform.
_ALLOWED_MANIFEST_FIELDS = frozenset(
    {
        'agent_id',
        'version',
        'name',
        'description',
        'transport',
        'definition_digest',
        'tool_names',
        'endpoint_url',
        'sdk_version',
    },
)


class _ControlPlaneStub:
    """A scripted stand-in for the control plane's three serving routes."""

    def __init__(
        self,
        *,
        heartbeat_statuses: Sequence[int] = (),
        announce_statuses: Sequence[int] = (),
        stop_statuses: Sequence[int] = (),
    ) -> None:
        """Queue one status per call; anything unqueued succeeds."""
        self.calls: list[tuple[str, str]] = []
        self.claim_calls: list[tuple[str, str]] = []
        self.announce_bodies: list[dict[str, Any]] = []
        self.announce_texts: list[str] = []
        self._heartbeat = list(heartbeat_statuses)
        self._announce = list(announce_statuses)
        self._stop = list(stop_statuses)
        self._announced = 0

    def __call__(self, request: httpx.Request) -> httpx.Response:
        """Answer one recorded serving request."""
        if request.url.path.startswith(CLAIMS_PATH):
            # Not a control-plane route. A serving process also pulls its work
            # from the event plane, and these tests are about the announcement
            # half; recording those calls here would make every assertion below
            # about the other half of the process's life.
            self.claim_calls.append((request.method, request.url.path))
            return httpx.Response(HTTPStatus.OK, json={'claims': [], 'poll_after_seconds': 60.0})
        self.calls.append((request.method, request.url.path))
        if request.method == 'POST':
            return self._announce_response(request)
        if request.method == 'PATCH':
            return self._empty(_take(self._heartbeat, HTTPStatus.NO_CONTENT))
        if request.method == 'DELETE':
            return self._empty(_take(self._stop, HTTPStatus.NO_CONTENT))
        return httpx.Response(
            HTTPStatus.METHOD_NOT_ALLOWED,
            json={'detail': 'Method not allowed.', 'code': 'method_not_allowed'},
        )

    @property
    def methods(self) -> list[str]:
        """Return the HTTP verbs seen, in order."""
        return [method for method, _path in self.calls]

    def paths_for(self, method: str) -> list[str]:
        """Return the paths seen for one HTTP verb, in order."""
        return [path for seen, path in self.calls if seen == method]

    def _announce_response(self, request: httpx.Request) -> httpx.Response:
        text = request.content.decode('utf-8')
        self.announce_texts.append(text)
        self.announce_bodies.append(json.loads(text))
        status = _take(self._announce, HTTPStatus.CREATED)
        if status != HTTPStatus.CREATED:
            return httpx.Response(
                status, json={'detail': 'Announce refused.', 'code': 'announce_refused'}
            )
        self._announced += 1
        return httpx.Response(
            HTTPStatus.CREATED,
            json={
                'process_id': f'prc-{self._announced}',
                'served': {'version': 1, 'serving': 'serving'},
            },
        )

    @staticmethod
    def _empty(status: int) -> httpx.Response:
        if status == HTTPStatus.NO_CONTENT:
            return httpx.Response(status)
        return httpx.Response(status, json={'detail': f'Status {status}.', 'code': 'refused'})


def _take(queue: list[int], default: int) -> int:
    return queue.pop(0) if queue else default


def _lookup_case(case_reference: str) -> dict[str, str]:
    """Stand in for a developer's tool; its schema must never reach the platform."""
    return {'case': case_reference}


def _agent(stub: _ControlPlaneStub, *, model: str = 'balanced', tools: bool = True) -> Agent:
    agent = Agent(
        name='triage',
        description='Triages inbound reports',
        system_prompt=_INSTRUCTIONS,
        model=model,
        client=Client(
            config=ClientConfig(api_key='test-key', base_url=AnyUrl(_CONTROL_ORIGIN)),
            transport=httpx.MockTransport(stub),
        ),
    )
    if tools:
        _ = agent.add_tool(
            _lookup_case,
            name='lookup_case',
            description=_TOOL_DESCRIPTION,
        )
    return agent


def _session(agent: Agent, **options: Any) -> ServingSession:
    return agent.serving(project_id=_PROJECT_ID, **options)


def test_announce_matches_the_pinned_wire_contract() -> None:
    """One POST to the serving route, carrying the instance id and the manifest."""
    stub = _ControlPlaneStub()
    session = _session(_agent(stub))

    served = asyncio.run(session.announce())

    assert stub.calls == [('POST', _SERVING_PATH)]
    body = stub.announce_bodies[0]
    assert set(body) == {'instance_id', 'manifest'}
    assert body['instance_id'] == session.instance_id
    assert str(UUID(body['instance_id'])) == session.instance_id
    assert session.process_id == 'prc-1'
    assert served.serving == 'serving'


def test_announced_manifest_carries_no_definition_material() -> None:
    """Only the display subset crosses the wire - never the runnable definition.

    Asserted on the serialised body rather than on the model, because a stub
    will happily accept an object that is fine in Python and wrong on the wire.
    """
    stub = _ControlPlaneStub()
    session = _session(_agent(stub))

    _ = asyncio.run(session.announce())

    text = stub.announce_texts[0]
    body = stub.announce_bodies[0]
    manifest = body['manifest']
    assert set(manifest) <= _ALLOWED_MANIFEST_FIELDS
    assert manifest['tool_names'] == ['lookup_case']
    # The instructions, the model settings and the tool schema all reach the
    # fingerprint and stop there.
    assert _INSTRUCTIONS not in text
    assert _TOOL_DESCRIPTION not in text
    assert 'balanced' not in text
    assert 'case_reference' not in text
    for forbidden in ('instructions', 'system_prompt', 'input_schema', 'model'):
        assert forbidden not in text


def test_fingerprint_covers_the_whole_definition_not_only_the_prompt() -> None:
    """Editing the model or the tool list is drift, exactly as an edited prompt is."""
    base = build_serving_manifest(_agent(_ControlPlaneStub()), agent_id='triage', version=1)
    other_model = build_serving_manifest(
        _agent(_ControlPlaneStub(), model='fast'),
        agent_id='triage',
        version=1,
    )
    no_tools = build_serving_manifest(
        _agent(_ControlPlaneStub(), tools=False),
        agent_id='triage',
        version=1,
    )

    assert base.definition_digest != other_model.definition_digest
    assert base.definition_digest != no_tools.definition_digest
    # One writer: the SDK computes exactly what the shared contract computes.
    assert base.definition_digest == definition_digest(
        instructions=_INSTRUCTIONS,
        model={'response': 'balanced'},
        tool_names=('lookup_case',),
    )


def test_heartbeat_404_re_announces_instead_of_stopping() -> None:
    """A platform that forgot this process gets told about it again."""
    stub = _ControlPlaneStub(heartbeat_statuses=[HTTPStatus.NOT_FOUND])
    session = _session(_agent(stub))

    asyncio.run(session.announce())
    landed = asyncio.run(session.heartbeat())

    assert landed
    assert stub.methods == ['POST', 'PATCH', 'POST']
    assert session.process_id == 'prc-2'
    # The re-announcement is the SAME machine coming back, so it reuses the
    # instance id rather than presenting itself as a second one.
    instance_ids = {body['instance_id'] for body in stub.announce_bodies}
    assert instance_ids == {session.instance_id}
    assert len(stub.announce_bodies) == _ANNOUNCE_THEN_RE_ANNOUNCE


def test_transient_failure_is_retried_with_backoff(monkeypatch: pytest.MonkeyPatch) -> None:
    """Two 503s in a row cost a retry each, not the beat."""
    monkeypatch.setattr(serving, 'INITIAL_RETRY_BACKOFF_SECONDS', 0.0)
    stub = _ControlPlaneStub(
        heartbeat_statuses=[
            HTTPStatus.SERVICE_UNAVAILABLE,
            HTTPStatus.SERVICE_UNAVAILABLE,
            HTTPStatus.NO_CONTENT,
        ],
    )
    session = _session(_agent(stub))

    asyncio.run(session.announce())
    landed = asyncio.run(session.heartbeat())

    assert landed
    assert len(stub.paths_for('PATCH')) == _TWO_RETRIES_THEN_SUCCESS


def test_the_serving_loop_survives_an_exhausted_retry_budget(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A developer's agent must not stop serving because their Wi-Fi blipped."""
    monkeypatch.setattr(serving, 'INITIAL_RETRY_BACKOFF_SECONDS', 0.0)
    stub = _ControlPlaneStub(
        heartbeat_statuses=[HTTPStatus.SERVICE_UNAVAILABLE] * serving.MAX_RETRY_ATTEMPTS,
    )
    events: list[ServingEvent] = []
    session = _session(_agent(stub), listener=events.append)

    asyncio.run(session.announce())
    missed = asyncio.run(session.heartbeat())
    recovered = asyncio.run(session.heartbeat())

    assert missed is False
    assert recovered is True
    assert 'unreachable' in {event.kind for event in events}


def test_a_connection_failure_is_retried_rather_than_fatal(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Connection refused is the blipped-network case, not a reason to give up."""
    monkeypatch.setattr(serving, 'INITIAL_RETRY_BACKOFF_SECONDS', 0.0)
    attempts: list[str] = []

    def refuse_then_accept(request: httpx.Request) -> httpx.Response:
        attempts.append(request.method)
        if request.method == 'POST':
            return httpx.Response(
                HTTPStatus.CREATED,
                json={
                    'process_id': 'prc-1',
                    'served': {'version': 1, 'serving': 'serving'},
                },
            )
        if len(attempts) < _TWO_RETRIES_THEN_SUCCESS:
            message = 'connection refused'
            raise httpx.ConnectError(message)
        return httpx.Response(HTTPStatus.NO_CONTENT)

    agent = Agent(
        name='triage',
        client=Client(
            config=ClientConfig(api_key='test-key', base_url=AnyUrl(_CONTROL_ORIGIN)),
            transport=httpx.MockTransport(refuse_then_accept),
        ),
    )
    session = _session(agent)

    asyncio.run(session.announce())

    assert asyncio.run(session.heartbeat()) is True


def test_instance_id_is_one_per_process_and_never_regenerated() -> None:
    """Two machines serving one version must stay two rows on the platform."""
    stub = _ControlPlaneStub(heartbeat_statuses=[HTTPStatus.NOT_FOUND])
    session = _session(_agent(stub))

    asyncio.run(session.announce())
    first = session.instance_id
    _ = asyncio.run(session.heartbeat())
    _ = asyncio.run(session.heartbeat())

    assert session.instance_id == first
    assert {body['instance_id'] for body in stub.announce_bodies} == {first}

    # A fresh process is the only thing that earns a new id.
    process_instance_id.cache_clear()
    restarted = _session(_agent(_ControlPlaneStub()))
    assert restarted.instance_id != first


def test_a_clean_stop_sends_the_delete() -> None:
    """Leaving the context manager forgets the process rather than aging it out."""
    stub = _ControlPlaneStub()
    session = _session(_agent(stub))

    async def announce_and_leave() -> None:
        async with session:
            assert session.process_id == 'prc-1'

    asyncio.run(announce_and_leave())

    assert stub.methods == ['POST', 'DELETE']
    assert stub.paths_for('DELETE') == [f'{_SERVING_PATH}/prc-1']
    # Idempotent: a second stop has nothing left to forget.
    asyncio.run(session.stop())
    assert stub.methods == ['POST', 'DELETE']


def test_serve_stops_cleanly_when_the_process_is_cancelled() -> None:
    """Ctrl-C is a clean stop, not a stale row the platform has to expire."""
    stub = _ControlPlaneStub()
    agent = _agent(stub)

    async def serve_then_cancel() -> None:
        beat = asyncio.Event()

        def watch(event: ServingEvent) -> None:
            if event.kind == 'heartbeat':
                beat.set()

        task = asyncio.ensure_future(
            agent.serve(
                project_id=_PROJECT_ID,
                heartbeat_interval_seconds=0.0,
                listener=watch,
            ),
        )
        await asyncio.wait_for(beat.wait(), timeout=5.0)
        _ = task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

    asyncio.run(serve_then_cancel())

    assert stub.methods[0] == 'POST'
    assert stub.methods[-1] == 'DELETE'


def test_a_refusal_is_terminal_and_is_not_retried() -> None:
    """401 stops the loop and says why; retrying a refusal only delays the news."""
    stub = _ControlPlaneStub(heartbeat_statuses=[HTTPStatus.UNAUTHORIZED])
    session = _session(_agent(stub))

    asyncio.run(session.announce())
    with pytest.raises(MaivnHTTPError) as refusal:
        _ = asyncio.run(session.heartbeat())

    assert refusal.value.status_code == HTTPStatus.UNAUTHORIZED
    assert len(stub.paths_for('PATCH')) == 1


def test_a_refusal_during_serve_still_stops_cleanly() -> None:
    """The DELETE goes out even when the loop ends on a refusal."""
    stub = _ControlPlaneStub(heartbeat_statuses=[HTTPStatus.FORBIDDEN])
    agent = _agent(stub)

    with pytest.raises(MaivnHTTPError):
        asyncio.run(agent.serve(project_id=_PROJECT_ID, heartbeat_interval_seconds=0.0))

    assert stub.methods == ['POST', 'PATCH', 'DELETE']


def test_a_missing_project_id_names_the_environment_variable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Never guessed and never a silent no-op: the error says which variable to set."""
    monkeypatch.delenv('MAIVN_PROJECT_ID', raising=False)
    agent = _agent(_ControlPlaneStub())

    with pytest.raises(ConfigurationError, match='MAIVN_PROJECT_ID'):
        _ = agent.serving()


def test_the_project_id_falls_back_to_the_environment_variable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A developer who exported the variable does not repeat it on every call."""
    monkeypatch.setenv('MAIVN_PROJECT_ID', _PROJECT_ID)
    stub = _ControlPlaneStub()
    agent = Agent(
        name='triage',
        client=Client(
            config=ClientConfig.from_sources(api_key='test-key', base_url=_CONTROL_ORIGIN),
            transport=httpx.MockTransport(stub),
        ),
    )

    asyncio.run(agent.serving().announce())

    assert stub.paths_for('POST') == [_SERVING_PATH]


def test_webhook_transport_requires_an_endpoint_url() -> None:
    """A webhook manifest without a URL announces a version nothing can call."""
    agent = _agent(_ControlPlaneStub())

    with pytest.raises(ValueError, match='endpoint_url'):
        _ = agent.serving(project_id=_PROJECT_ID, transport='webhook')


def test_worker_transport_rejects_an_endpoint_url() -> None:
    """A worker is reached through the platform, so a URL of its own is a mistake."""
    agent = _agent(_ControlPlaneStub())

    with pytest.raises(ValueError, match='endpoint_url'):
        _ = agent.serving(
            project_id=_PROJECT_ID,
            endpoint_url='https://worker.example/invoke',
        )


def test_webhook_transport_announces_its_endpoint() -> None:
    """The paired form is what actually reaches the wire."""
    stub = _ControlPlaneStub()
    session = _session(
        _agent(stub),
        transport='webhook',
        endpoint_url='https://hook.example/invoke',
    )

    asyncio.run(session.announce())

    manifest = stub.announce_bodies[0]['manifest']
    assert manifest['transport'] == 'webhook'
    assert manifest['endpoint_url'] == 'https://hook.example/invoke'


def test_the_heartbeat_interval_leaves_room_for_a_missed_beat() -> None:
    """Three beats still land inside the platform's staleness window."""
    assert HEARTBEAT_INTERVAL_SECONDS > 0
    assert HEARTBEAT_INTERVAL_SECONDS * _TWO_RETRIES_THEN_SUCCESS < _PLATFORM_STALE_AFTER_SECONDS


def test_serving_announces_with_the_client_it_is_given_not_the_agents_own() -> None:
    """Studio serves an app it has open, so the announcement is Studio's, not the app's.

    Who announces and who runs are different questions. The announcement is a
    claim about a *process* - its credential, its heartbeat, its lifetime - and
    when Studio serves an app that process is Studio. The agent keeps running
    against whatever client the app gave it.

    Asserted by which stub receives the POST, not by reading a private
    attribute: the point is where the request goes.
    """
    app_stub = _ControlPlaneStub()
    studio_stub = _ControlPlaneStub()
    app_client = Client(
        config=ClientConfig(api_key='the-app-key', base_url=AnyUrl(_CONTROL_ORIGIN)),
        transport=httpx.MockTransport(app_stub),
    )
    studio_client = Client(
        config=ClientConfig(api_key='the-studio-key', base_url=AnyUrl(_CONTROL_ORIGIN)),
        transport=httpx.MockTransport(studio_stub),
    )
    agent = Agent(name='triage', client=app_client)

    session = agent.serving(project_id=_PROJECT_ID, client=studio_client)
    _ = asyncio.run(session.announce())

    assert [method for method, _path in studio_stub.calls] == ['POST']
    assert app_stub.calls == []
    # The agent is untouched: it still runs against its own client.
    assert agent.client is app_client


def test_a_stub_client_swallows_its_own_announcement() -> None:
    """The trap that cost an afternoon, pinned so it cannot cost another.

    A `Client` shares one transport across every origin unless told otherwise,
    so a mock meant to fake the data plane silently intercepts the
    control-plane announce too. The bundled sample is exactly this shape, and
    its announcement came back as the stub's own 404 - indistinguishable from
    the control plane refusing a stranger, which is what sent three people
    looking at API keys and project bindings instead of at the client.

    This is why `serving(client=...)` exists rather than Studio simply reusing
    the app's client.
    """
    sample_like = Client(
        config=ClientConfig(
            api_key='unused-sample-agent-key',
            base_url=AnyUrl('http://studio-sample-agent.local'),
        ),
        transport=httpx.MockTransport(
            lambda _request: httpx.Response(
                HTTPStatus.NOT_FOUND, json={'detail': 'unknown sample-agent route'}
            )
        ),
    )
    agent = Agent(name='triage', client=sample_like)
    session = agent.serving(project_id=_PROJECT_ID)

    with pytest.raises(MaivnHTTPError) as refused:
        _ = asyncio.run(session.announce())

    # The stub answered, not the platform - and the two are indistinguishable
    # from here, which is the whole problem.
    assert refused.value.status_code == int(HTTPStatus.NOT_FOUND)
