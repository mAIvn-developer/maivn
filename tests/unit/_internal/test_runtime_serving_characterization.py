"""Characterize ServingSession failure handling, response validation and fingerprints."""

from __future__ import annotations

import asyncio
import json
from http import HTTPStatus
from typing import TYPE_CHECKING, cast

import httpx
import pytest
from maivn_contracts.agents.fingerprint import definition_digest
from maivn_contracts.messages import TextContentBlock
from pydantic import AnyUrl

from maivn import Agent, Client, ClientConfig, MaivnHTTPError, MaivnSDKError
from maivn._internal.serving import ServingEvent, ServingSession, build_serving_manifest
from maivn.messages import SystemMessage

if TYPE_CHECKING:
    from collections.abc import Callable

_PROJECT_ID = 'prj/char'
_SERVING_PATH = '/v1/projects/prj%2Fchar/agents/serving'
_ANNOUNCED = {'process_id': 'prc/1', 'served': {'version': 1, 'serving': 'serving'}}


def _agent(handler: Callable[[httpx.Request], httpx.Response], **kwargs: object) -> Agent:
    return Agent(
        name='serving-char',
        client=Client(
            config=ClientConfig(api_key='test-key', base_url=AnyUrl('https://control.example')),
            transport=httpx.MockTransport(handler),
        ),
        **kwargs,  # pyright: ignore[reportArgumentType]
    )


def _session(
    handler: Callable[[httpx.Request], httpx.Response], events: list[ServingEvent]
) -> ServingSession:
    return _agent(handler).serving(project_id=_PROJECT_ID, listener=events.append)


def _scripted(
    *, patch: Callable[[httpx.Request], httpx.Response] | None = None, delete: object = None
) -> tuple[Callable[[httpx.Request], httpx.Response], list[tuple[str, str]]]:
    calls: list[tuple[str, str]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append((request.method, request.url.raw_path.decode()))
        if request.method == 'POST':
            return httpx.Response(HTTPStatus.CREATED, json=_ANNOUNCED)
        if request.method == 'PATCH' and patch is not None:
            return patch(request)
        if request.method == 'DELETE' and delete is not None:
            if isinstance(delete, BaseException):
                raise delete
            return httpx.Response(
                int(cast('int', delete)), json={'detail': 'Stop refused.', 'code': 'refused'}
            )
        return httpx.Response(HTTPStatus.NO_CONTENT)

    return handler, calls


def test_heartbeat_before_announce_is_a_usage_error() -> None:
    """A heartbeat needs an announced process and says how to get one."""
    handler, calls = _scripted()
    session = _session(handler, [])
    with pytest.raises(
        MaivnSDKError,
        match=r'^serving heartbeat requires an announced process: call announce\(\) first$',
    ):
        asyncio.run(session.heartbeat())
    assert calls == []


def test_announce_exposes_process_project_and_served_version() -> None:
    """Announcing records the platform's process id and served version."""
    handler, calls = _scripted()
    events: list[ServingEvent] = []
    session = _session(handler, events)
    assert (session.process_id, session.served) == (None, None)
    served = asyncio.run(session.announce())
    assert session.project_id == _PROJECT_ID
    assert session.process_id == 'prc/1'
    assert session.served == served
    assert served.version == 1
    assert calls == [('POST', _SERVING_PATH)]
    assert [(event.kind, event.process_id, event.detail) for event in events] == [
        ('announced', 'prc/1', None)
    ]


def test_non_transient_transport_error_costs_one_beat() -> None:
    """A non-retryable transport failure emits unreachable and returns False."""

    def broken(request: httpx.Request) -> httpx.Response:
        message = 'garbled response'
        raise httpx.DecodingError(message, request=request)

    handler, calls = _scripted(patch=broken)
    events: list[ServingEvent] = []
    session = _session(handler, events)
    asyncio.run(session.announce())
    assert asyncio.run(session.heartbeat()) is False
    assert [(event.kind, event.detail) for event in events] == [
        ('announced', None),
        ('unreachable', 'garbled response'),
    ]
    assert [method for method, _path in calls] == ['POST', 'PATCH']


def test_non_terminal_http_refusal_costs_one_beat() -> None:
    """A non-retryable, non-terminal status emits unreachable with the error text."""

    def conflict(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(HTTPStatus.CONFLICT, json={'detail': 'Busy.', 'code': 'busy'})

    handler, _calls = _scripted(patch=conflict)
    events: list[ServingEvent] = []
    session = _session(handler, events)
    asyncio.run(session.announce())
    assert asyncio.run(session.heartbeat()) is False
    assert events[-1].kind == 'unreachable'
    assert events[-1].detail == 'mAIvn API returned 409: Busy.'


def test_stop_reports_a_refused_delete_without_raising() -> None:
    """A non-404 stop refusal is reported on the stopped event, once."""
    handler, calls = _scripted(delete=HTTPStatus.FORBIDDEN)
    events: list[ServingEvent] = []
    session = _session(handler, events)
    asyncio.run(session.announce())
    asyncio.run(session.stop())
    asyncio.run(session.stop())
    assert session.process_id is None
    assert [method for method, _path in calls] == ['POST', 'DELETE']
    assert calls[1][1] == f'{_SERVING_PATH}/prc%2F1'
    assert [(event.kind, event.detail) for event in events] == [
        ('announced', None),
        ('stopped', 'mAIvn API returned 403: Stop refused.'),
    ]


def test_stop_treats_not_found_as_already_stopped() -> None:
    """A 404 on stop is the desired outcome and reports a plain stopped event."""
    handler, _calls = _scripted(delete=HTTPStatus.NOT_FOUND)
    events: list[ServingEvent] = []
    session = _session(handler, events)
    asyncio.run(session.announce())
    asyncio.run(session.stop())
    assert [(event.kind, event.detail) for event in events][-1] == ('stopped', None)


def test_stop_reports_a_transport_failure_without_raising() -> None:
    """A transport failure on stop is reported on the stopped event."""
    handler, _calls = _scripted(delete=httpx.DecodingError('stop garbled'))
    events: list[ServingEvent] = []
    session = _session(handler, events)
    asyncio.run(session.announce())
    asyncio.run(session.stop())
    assert [(event.kind, event.detail) for event in events][-1] == ('stopped', 'stop garbled')


@pytest.mark.parametrize(
    ('body', 'message'),
    [
        ({'served': {'version': 1, 'serving': 'serving'}}, 'serving response omitted process_id'),
        ({'process_id': '', 'served': {}}, 'serving response omitted process_id'),
        ({'process_id': 'prc-1', 'served': 'serving'}, 'serving response omitted served'),
    ],
)
def test_announce_rejects_incomplete_responses(body: dict[str, object], message: str) -> None:
    """A response missing the process id or served block is an SDK error."""

    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(HTTPStatus.CREATED, json=body)

    session = _session(handler, [])
    with pytest.raises(MaivnSDKError, match=f'^{message}$') as caught:
        asyncio.run(session.announce())
    assert not isinstance(caught.value, MaivnHTTPError)


@pytest.mark.parametrize(
    ('system_prompt', 'instructions'),
    [
        (None, ''),
        ('Plain instructions.', 'Plain instructions.'),
        (SystemMessage(content='Message text.'), 'Message text.'),
        (
            SystemMessage(content=[TextContentBlock(type='text', text='Block text.')]),
            json.dumps(
                [TextContentBlock(type='text', text='Block text.').model_dump(mode='json')],
                sort_keys=True,
                separators=(',', ':'),
            ),
        ),
    ],
)
def test_fingerprint_covers_only_the_prompt_content(
    system_prompt: object, instructions: str
) -> None:
    """Prompts reduce to their text, or canonical JSON for content blocks."""
    agent = _agent(lambda _request: httpx.Response(HTTPStatus.OK), system_prompt=system_prompt)
    manifest = build_serving_manifest(agent, agent_id='agent-1', version=2)
    assert manifest.definition_digest == definition_digest(
        instructions=instructions, model={'response': agent.model}, tool_names=()
    )
