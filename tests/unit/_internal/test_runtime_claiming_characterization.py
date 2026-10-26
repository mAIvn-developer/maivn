"""Characterize work claiming: claim parsing, report failures and keepalive outcomes."""

from __future__ import annotations

import asyncio
import json
from http import HTTPStatus
from typing import TYPE_CHECKING, Any, cast

import httpx
import pytest
from pydantic import AnyUrl

from maivn import Client, ClientConfig, MaivnSDKError
from maivn._internal.claiming import (
    CLAIMS_PATH,
    IDLE_POLL_SECONDS,
    ClaimedWork,
    WorkClaimer,
    WorkLoop,
)

if TYPE_CHECKING:
    from collections.abc import Callable

    from maivn._internal.claiming import ClaimEvent, WorkRunner

_Event = tuple[str, str, 'str | None']


def _envelope(fire_id: str = 'fire-1', **work: object) -> dict[str, Any]:
    body: dict[str, Any] = {
        'invocation_id': f'inv-{fire_id}',
        'trigger_id': 'trg-a',
        'agent_id': 'triage',
        'agent_version': 1,
        'session_id': f'ses-{fire_id}',
        'root_event_id': 'evt-1',
    }
    body.update(work)
    return {'fire_id': fire_id, 'claim_token': 7, 'work': body}


class _Platform:
    """Scripted claim and report routes; each queue entry answers one request."""

    def __init__(
        self,
        claims: list[object],
        reports: list[object] | None = None,
    ) -> None:
        self.claims = claims
        self.reports = list(reports or [])
        self.report_bodies: list[dict[str, Any]] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        if request.url.path == CLAIMS_PATH:
            page: object = self.claims.pop(0) if self.claims else []
            if isinstance(page, BaseException):
                raise page
            return httpx.Response(HTTPStatus.OK, json=page)
        body = json.loads(request.content.decode('utf-8'))
        self.report_bodies.append(body)
        answer = self.reports.pop(0) if self.reports else HTTPStatus.OK
        if isinstance(answer, BaseException):
            raise answer
        if answer != HTTPStatus.OK:
            status = int(cast('int', answer))
            return httpx.Response(status, json={'detail': f'Report status {status}.', 'code': 'x'})
        return httpx.Response(HTTPStatus.OK, json={'status': 'ok'})

    def statuses(self) -> list[str]:
        return [body['status'] for body in self.report_bodies]


def _claimer(platform: _Platform) -> WorkClaimer:
    client = Client(
        config=ClientConfig(api_key='test-key', base_url=AnyUrl('https://platform.example')),
        transport=httpx.MockTransport(platform),
    )
    return WorkClaimer(client, process_id='prc-1')


def _page(*claims: object, poll_after: object = 0.0) -> dict[str, object]:
    return {'claims': list(claims), 'poll_after_seconds': poll_after}


def _run_loop(
    platform: _Platform,
    runner: WorkRunner,
    *,
    stop_when: Callable[[list[_Event]], bool],
    keepalive: float = 30.0,
) -> list[_Event]:
    events: list[_Event] = []

    async def drive() -> None:
        done = asyncio.Event()

        def listen(fire_id: str, event: ClaimEvent, detail: str | None) -> None:
            events.append((fire_id, event, detail))
            if stop_when(events):
                done.set()

        loop = WorkLoop(
            _claimer(platform),
            runner,
            idle_poll_seconds=0.0,
            keepalive_interval_seconds=keepalive,
            listener=listen,
        )
        task = asyncio.ensure_future(loop.run())
        try:
            await asyncio.wait_for(done.wait(), timeout=5.0)
        finally:
            _ = task.cancel()
            _ = await asyncio.wait_for(asyncio.gather(task, return_exceptions=True), 5.0)

    asyncio.run(drive())
    return events


async def _answer(_work: ClaimedWork) -> str:
    return 'answer'


def _completed(events: list[_Event]) -> bool:
    return any(event == 'completed' for _fire, event, _detail in events)


# --- claim parsing -----------------------------------------------------------------


def test_claim_parses_optional_fields_and_default_poll_interval() -> None:
    """Missing payload, binding and lease become defaults; no poll hint means idle."""
    platform = _Platform([{'claims': [_envelope(claim_until=5)]}])
    claimer = _claimer(platform)
    claims, wait = asyncio.run(claimer.claim())
    assert claimer.process_id == 'prc-1'
    assert wait == IDLE_POLL_SECONDS
    assert claims == [
        ClaimedWork(
            fire_id='fire-1',
            claim_token=7,
            invocation_id='inv-fire-1',
            trigger_id='trg-a',
            agent_id='triage',
            agent_version=1,
            session_id='ses-fire-1',
            root_event_id='evt-1',
        )
    ]


def test_claim_page_without_a_claim_list_is_empty() -> None:
    """A non-list claims field reads as no work with the advertised poll interval."""
    platform = _Platform([{'claims': 'none', 'poll_after_seconds': 3}])
    assert asyncio.run(_claimer(platform).claim()) == ([], 3.0)


@pytest.mark.parametrize(
    ('entry', 'message'),
    [
        ('not-an-object', 'agent work claim was not an object'),
        ({'fire_id': 'f', 'claim_token': 1, 'work': 'x'}, 'agent work claim carried no hand-over'),
        ({**_envelope(), 'fire_id': ''}, 'agent work claim omitted fire_id'),
        ({**_envelope(), 'claim_token': True}, 'agent work claim omitted claim_token'),
        (_envelope(agent_version='1'), 'agent work claim omitted agent_version'),
        (_envelope(session_id=None), 'agent work claim omitted session_id'),
    ],
)
def test_malformed_claims_are_refused(entry: object, message: str) -> None:
    """Every required claim field is checked with a field-naming message."""
    platform = _Platform([{'claims': [entry]}])
    with pytest.raises(MaivnSDKError, match=f'^{message}$'):
        asyncio.run(_claimer(platform).claim())


def test_empty_template_binding_hands_the_agent_the_event() -> None:
    """A template binding with no messages falls back to the canonical payload JSON."""
    work = ClaimedWork(
        fire_id='f',
        claim_token=1,
        invocation_id='i',
        trigger_id='t',
        agent_id='a',
        agent_version=1,
        session_id='s',
        root_event_id='r',
        payload={'b': 1, 'a': {'x': 2}},
        binding={'kind': 'template', 'messages': []},
    )
    assert work.messages() == ['{"a": {"x": 2}, "b": 1}']


# --- the work loop -----------------------------------------------------------------


def test_transport_failure_while_claiming_costs_a_pause() -> None:
    """A transport error on claim emits unreachable, then the loop claims again."""
    platform = _Platform([httpx.DecodingError('claim garbled'), _page(_envelope())])
    events = _run_loop(platform, _answer, stop_when=_completed)
    assert events == [
        ('', 'unreachable', 'claim garbled'),
        ('fire-1', 'claimed', None),
        ('fire-1', 'completed', None),
    ]
    assert platform.statuses() == ['completed']


def test_held_tracks_the_running_claim() -> None:
    """While a runner works, the loop reports that claim as held."""
    platform = _Platform([_page(_envelope())])
    observed: list[str | None] = []
    loop_box: list[WorkLoop] = []

    async def runner(work: ClaimedWork) -> str:
        held = loop_box[0].held
        observed.append(None if held is None else held.fire_id)
        return work.fire_id

    async def drive() -> None:
        done = asyncio.Event()

        def listen(_fire: str, event: ClaimEvent, _detail: str | None) -> None:
            if event == 'completed':
                done.set()

        loop = WorkLoop(_claimer(platform), runner, idle_poll_seconds=0.0, listener=listen)
        loop_box.append(loop)
        task = asyncio.ensure_future(loop.run())
        await asyncio.wait_for(done.wait(), 5.0)
        assert loop.held is None
        _ = task.cancel()
        _ = await asyncio.gather(task, return_exceptions=True)

    asyncio.run(drive())
    assert observed == ['fire-1']
    assert platform.report_bodies[0]['output'] == 'fire-1'


@pytest.mark.parametrize(
    ('answer', 'detail'),
    [
        (HTTPStatus.BAD_REQUEST, 'mAIvn API returned 400: Report status 400.'),
        (httpx.DecodingError('report garbled'), 'report garbled'),
    ],
)
def test_non_transient_report_failure_is_reported_once(answer: object, detail: str) -> None:
    """A report the platform will not accept is given up on, not retried."""
    platform = _Platform([_page(_envelope())], reports=[answer])
    events = _run_loop(platform, _answer, stop_when=_completed)
    assert events == [
        ('fire-1', 'claimed', None),
        ('fire-1', 'unreachable', detail),
        ('fire-1', 'completed', None),
    ]
    assert platform.statuses() == ['completed']


def test_transient_report_failure_is_retried() -> None:
    """A 503 on the final report is retried after a backoff and then lands."""
    platform = _Platform([_page(_envelope())], reports=[HTTPStatus.SERVICE_UNAVAILABLE])
    events = _run_loop(platform, _answer, stop_when=_completed)
    assert events == [('fire-1', 'claimed', None), ('fire-1', 'completed', None)]
    assert platform.statuses() == ['completed', 'completed']


@pytest.mark.parametrize(
    ('keepalive_answer', 'expected_event'),
    [
        (HTTPStatus.CONFLICT, None),
        (HTTPStatus.BAD_REQUEST, ('fire-1', 'unreachable', None)),
    ],
)
def test_keepalive_failures_stop_or_continue_the_extension(
    keepalive_answer: HTTPStatus, expected_event: _Event | None
) -> None:
    """A lost lease ends the keepalive silently; other failures are reported."""
    platform = _Platform([_page(_envelope())], reports=[keepalive_answer])
    # A lost lease stops after one extension; any other failure keeps extending.
    wanted = 1 if keepalive_answer == HTTPStatus.CONFLICT else 2

    async def slow(_work: ClaimedWork) -> str:
        while len(platform.report_bodies) < wanted:  # noqa: ASYNC110 - polls the stub's record.
            await asyncio.sleep(0.001)
        await asyncio.sleep(0.05)
        return 'slow'

    events = _run_loop(platform, slow, stop_when=_completed, keepalive=0.0)
    statuses = platform.statuses()
    assert statuses[0] == 'working'
    assert statuses[-1] == 'completed'
    if keepalive_answer == HTTPStatus.CONFLICT:
        assert statuses.count('working') == 1
        assert len(statuses) == 2  # noqa: PLR2004 - one extension, one final report.
        assert [event for _fire, event, _detail in events] == ['claimed', 'completed']
    else:
        assert expected_event is not None
        unreachable = [event for event in events if event[1] == 'unreachable']
        assert unreachable[0][:2] == expected_event[:2]
        assert unreachable[0][2] == 'mAIvn API returned 400: Report status 400.'
