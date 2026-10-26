# pyright: reportPrivateUsage=false, reportUnusedFunction=false
"""Decision runs: open/complete, session linking, tool-time linking and DecisionApp."""

# ruff: noqa: D103, PLR2004, EM101 - behavioural tests named for what they prove.

from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, cast

import httpx
import pytest

from maivn import Agent
from maivn._internal.client import _stream_with_local_tools
from maivn._internal.models import StreamEvent
from maivn._internal.tool_runtime import LocalToolRuntime
from maivn.decisions import (
    DecisionAnswer,
    DecisionApp,
    DecisionChoice,
    DecisionClient,
    DecisionConfig,
    DecisionRequest,
    DecisionStatus,
    decision_run,
)
from tests.unit.decision_platform import API_KEY, BASE_URL, PLATFORM_MODEL, decided

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

    from typing_extensions import Self

from maivn import Client

RUN_ID = 'ses-' + 'a' * 32


@dataclass
class _Platform:
    """Offline platform for decision runs and linked decisions."""

    decision_status: int = 200
    complete_status: int = 200
    log: list[tuple[str, dict[str, Any]]] = field(default_factory=list)

    async def _handle(self, request: httpx.Request) -> httpx.Response:
        body: dict[str, Any] = json.loads(request.content)
        self.log.append((request.url.path, body))
        assert request.headers['authorization'] == f'Bearer {API_KEY}'
        if request.url.path == '/v1/decisions/runs':
            return httpx.Response(201, json={'session_id': RUN_ID})
        if request.url.path == f'/v1/decisions/runs/{RUN_ID}/complete':
            return httpx.Response(self.complete_status, json={})
        assert request.url.path == '/v1/decisions'
        if self.decision_status != 200:
            return httpx.Response(self.decision_status, json={'code': 'x'})
        return httpx.Response(200, json=decided(body, lambda _q, _s: ('yes', 0.95)))

    def client(self) -> Client:
        return Client(
            api_key=API_KEY,
            base_url=BASE_URL,
            transport=httpx.MockTransport(self._handle),
            private_data_store=None,
            auto_detect_timezone=False,
        )

    @property
    def decisions(self) -> list[dict[str, Any]]:
        return [body for path, body in self.log if path == '/v1/decisions']

    @property
    def completions(self) -> list[str]:
        return [body['status'] for path, body in self.log if path.endswith('/complete')]


def _request() -> DecisionRequest:
    return DecisionRequest(
        name='route',
        state='public example',
        instructions='Choose',
        privacy='public',
        choices=(
            DecisionChoice(key='yes', description='yes'),
            DecisionChoice(key='no', description='no'),
        ),
    )


async def _baseline(_request: DecisionRequest) -> DecisionAnswer:
    return DecisionAnswer(value='no', provider='local', token_category=None)


def _decider(platform: _Platform, *, session_id: str | None = None) -> DecisionClient:
    return DecisionClient(
        fallback=_baseline,
        config=DecisionConfig(),
        authorize=lambda _request: True,
        client=platform.client(),
        session_id=session_id,
    )


def test_run_opens_links_decisions_and_completes() -> None:
    platform = _Platform()
    with decision_run('Triage', client=platform.client()) as opened:
        assert opened.session_id == RUN_ID
        result = _decider(platform).decide(_request())
    assert result.value == 'yes'
    assert result.primary.model == PLATFORM_MODEL
    assert platform.log[0] == ('/v1/decisions/runs', {'name': 'Triage'})
    assert platform.decisions[0]['session_id'] == RUN_ID
    assert platform.completions == ['completed']
    # Outside the run, nothing links.
    _decider(platform).decide(_request())
    assert 'session_id' not in platform.decisions[-1]


def test_run_completes_failed_when_an_exception_escapes() -> None:
    platform = _Platform()
    with pytest.raises(KeyError), decision_run('Triage', client=platform.client()):
        raise KeyError('boom')
    assert platform.completions == ['failed']


def test_completion_failure_does_not_mask_the_users_exception() -> None:
    platform = _Platform(complete_status=500)
    with pytest.raises(KeyError), decision_run('Triage', client=platform.client()):
        raise KeyError('boom')
    # And a clean exit with a failing completion does not raise either.
    with decision_run('Triage', client=platform.client()):
        pass
    assert platform.completions == ['failed', 'completed']


def test_async_run_links_and_completes() -> None:
    platform = _Platform()

    async def scenario() -> None:
        async with decision_run('Triage', client=platform.client()) as opened:
            assert opened.session_id == RUN_ID
            await _decider(platform).adecide(_request())
        with pytest.raises(ValueError, match='bad'):
            async with decision_run('Triage', client=platform.client()):
                raise ValueError('bad')

    asyncio.run(scenario())
    assert platform.decisions[0]['session_id'] == RUN_ID
    assert platform.completions == ['completed', 'failed']


def test_explicit_session_beats_the_decision_run() -> None:
    platform = _Platform()
    with decision_run('Triage', client=platform.client()):
        _decider(platform, session_id='ses-explicit').decide(_request())
    assert platform.decisions[0]['session_id'] == 'ses-explicit'


def _tool_start(call_id: str, tool: str) -> StreamEvent:
    return StreamEvent(
        position=1,
        event_type='system_tool_start',
        data={
            'payload': {
                'tool_call': {
                    'call_id': call_id,
                    'spec_ref': {'tool_id': tool, 'namespace': 'sdk', 'version': 'v1'},
                    'arguments': {},
                    'lineage': {'session_id': 'ses-invoke', 'invocation_id': 'inv-1'},
                }
            },
        },
    )


class _StubToolHttp:
    async def __aenter__(self) -> Self:
        return self

    async def __aexit__(self, *_exc: object) -> None:
        return None

    async def post(self, path: str, body: dict[str, object]) -> dict[str, object]:
        _ = path, body
        return {}


def test_decisions_inside_local_tools_link_to_the_invoke_session() -> None:
    platform = _Platform()
    decider = _decider(platform)
    agent = Agent(name='Router', api_key='mvn_test_key')

    @agent.toolify(name='sync_tool', description='Decide synchronously.')
    def sync_tool() -> str:
        return decider.decide(_request()).value

    @agent.toolify(name='async_tool', description='Decide asynchronously.')
    async def async_tool() -> str:
        return (await decider.adecide(_request())).value

    async def source() -> AsyncIterator[StreamEvent]:
        yield _tool_start('c1', 'sync_tool')
        yield _tool_start('c2', 'async_tool')

    async def collect() -> list[StreamEvent]:
        return [
            event
            async for event in _stream_with_local_tools(
                events=source(),
                tool_runtime=LocalToolRuntime(agent.compile_tools(), private_data=None),
                tool_http=cast('Any', _StubToolHttp()),
                session_id='ses-invoke',
            )
        ]

    asyncio.run(collect())
    assert [body['session_id'] for body in platform.decisions] == ['ses-invoke', 'ses-invoke']

    # The link ends with tool execution; an explicit decision run still wins over it.
    _decider(platform).decide(_request())
    assert 'session_id' not in platform.decisions[-1]


@pytest.mark.parametrize('status', [404, 409])
def test_unknown_or_finished_run_falls_back_instead_of_raising(status: int) -> None:
    platform = _Platform(decision_status=status)
    result = _decider(platform, session_id='ses-gone').decide(_request())
    assert result.source == 'fallback'
    assert result.value == 'no'
    assert result.primary.status == DecisionStatus.UNAVAILABLE


def test_decision_app_runs_inside_a_run_and_returns_text_and_session() -> None:
    platform = _Platform()

    def triage_tickets() -> str:
        return f'verdict={_decider(platform).decide(_request()).value}'

    app = DecisionApp(triage_tickets)
    assert app.name == 'Triage tickets'
    result = app.execute(client=platform.client())
    assert (result.text, result.session_id) == ('verdict=yes', RUN_ID)
    assert platform.log[0] == ('/v1/decisions/runs', {'name': 'Triage tickets'})
    assert platform.decisions[0]['session_id'] == RUN_ID
    assert platform.completions == ['completed']


def test_async_decision_app_and_failure_completion() -> None:
    platform = _Platform()

    async def report() -> str:
        return 'ok'

    def broken() -> str:
        raise RuntimeError('nope')

    async def scenario() -> None:
        result = await DecisionApp(report, name='Named').aexecute(client=platform.client())
        assert (result.text, result.session_id) == ('ok', RUN_ID)
        with pytest.raises(RuntimeError, match='nope'):
            await DecisionApp(broken).aexecute(client=platform.client())

    asyncio.run(scenario())
    assert platform.log[0] == ('/v1/decisions/runs', {'name': 'Named'})
    assert platform.completions == ['completed', 'failed']
    # A coroutine function also works through the sync entry point.
    assert DecisionApp(report).execute(client=platform.client()).text == 'ok'


def test_session_id_is_unavailable_until_the_run_opens() -> None:
    platform = _Platform()
    with pytest.raises(RuntimeError, match='not open'):
        _ = decision_run('Triage', client=platform.client()).session_id
    assert platform.log == []


def test_a_run_without_a_platform_session_id_refuses_to_open() -> None:
    def reply(request: httpx.Request) -> httpx.Response:
        assert request.url.path == '/v1/decisions/runs'
        return httpx.Response(201, json={'session_id': ''})

    client = Client(
        api_key=API_KEY,
        base_url=BASE_URL,
        transport=httpx.MockTransport(reply),
        private_data_store=None,
        auto_detect_timezone=False,
    )
    with pytest.raises(RuntimeError, match='did not return a decision run session id'):
        decision_run('Triage', client=client).__enter__()


def test_a_run_closed_from_another_context_still_unlinks_and_completes() -> None:
    platform = _Platform()
    opened = decision_run('Triage', client=platform.client())

    async def scenario() -> None:
        await opened.__aenter__()
        # A task runs in a copy of the context, so the entering token cannot be reset there.
        await asyncio.create_task(opened.__aexit__(None, None, None))

    asyncio.run(scenario())
    assert platform.completions == ['completed']


def test_an_anonymous_decision_app_gets_a_generic_name() -> None:
    assert DecisionApp(lambda: 'ok').name == 'Decision app'
