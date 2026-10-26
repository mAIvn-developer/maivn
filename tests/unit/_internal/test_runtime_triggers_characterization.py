"""Characterize trigger builder guards, registration failures and lifecycle parsing."""

from __future__ import annotations

from http import HTTPStatus
from typing import Any

import httpx
import pytest
from pydantic import AnyUrl

from maivn import Agent, Client, ClientConfig, MaivnHTTPError, MaivnSDKError
from maivn._internal.triggers import Trigger, stable_trigger_ids

_SCENARIO_ID, _TRIGGER_ID = stable_trigger_ids('agt-1', 'char')


def _canvas(*, status: object = 'enabled', **scenario: object) -> dict[str, Any]:
    trigger: dict[str, object] = {'trigger_id': _TRIGGER_ID, 'name': 'Char'}
    if status is not None:
        trigger['status'] = status
    body: dict[str, object] = {
        'scenario_id': _SCENARIO_ID,
        'project_id': 'prj-1',
        'trigger_id': _TRIGGER_ID,
        'name': 'Char',
    }
    body.update(scenario)
    return {'scenario': {'scenario': body, 'trigger': trigger}}


class _Router:
    """Answer the scenario routes from a per-(method, suffix) table."""

    def __init__(self, routes: dict[tuple[str, str], httpx.Response]) -> None:
        self.routes = routes
        self.calls: list[tuple[str, str, str]] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.calls.append((request.method, request.url.path, request.url.query.decode()))
        for (method, suffix), response in self.routes.items():
            if request.method == method and request.url.path.endswith(suffix):
                return response
        return httpx.Response(HTTPStatus.CREATED, json=_canvas())


def _client(router: _Router) -> Client:
    return Client(
        config=ClientConfig(api_key='test-key', base_url=AnyUrl('https://data.example')),
        transport=httpx.MockTransport(router),
    )


def _register(router: _Router) -> Trigger:
    agent = Agent(name='char', client=_client(router))
    return agent.on_manual().key('char').target('agt-1', version=1).invoke('Handle it.')


def _error(status: int, detail: str = 'Refused.') -> httpx.Response:
    return httpx.Response(status, json={'detail': detail, 'code': 'refused'})


# --- builder guards ----------------------------------------------------------------


@pytest.mark.parametrize('version', [0, -3])
def test_target_version_must_be_positive(version: int) -> None:
    """A non-positive deployed version is refused."""
    agent = Agent(name='char', client=_client(_Router({})))
    with pytest.raises(ValueError, match=r'^trigger target version must be positive$'):
        agent.on_manual().target('agt-1', version=version)


@pytest.mark.parametrize(
    ('call', 'message'),
    [
        ('named', 'name must not be blank'),
        ('agent', 'agent_id must not be blank'),
        ('templates', 'trigger registration requires at least one message template'),
        ('blank-template', 'message template must not be blank'),
    ],
)
def test_builder_refuses_blank_values(call: str, message: str) -> None:
    """Blank names, targets and message templates are refused with fixed messages."""
    agent = Agent(name='char', client=_client(_Router({})))
    builder = agent.on_manual().key('char')

    def attempt() -> object:
        if call == 'named':
            return builder.named('   ')
        if call == 'agent':
            return builder.target('  ', version=1)
        if call == 'templates':
            return builder.target('agt-1', version=1).invoke([])
        return builder.target('agt-1', version=1).invoke(['ok', '  '])

    with pytest.raises(ValueError, match=f'^{message}$'):
        attempt()


# --- registration ------------------------------------------------------------------


def test_registration_creates_when_the_scenario_is_unknown() -> None:
    """A 404 on the existence check falls back to creating the scenario."""
    router = _Router({('GET', _SCENARIO_ID): _error(HTTPStatus.NOT_FOUND)})
    trigger = _register(router)
    assert (trigger.scenario_id, trigger.trigger_id, trigger.status) == (
        _SCENARIO_ID,
        _TRIGGER_ID,
        'enabled',
    )
    assert [(method, path) for method, path, _query in router.calls] == [
        ('GET', f'/v1/scenarios/{_SCENARIO_ID}'),
        ('POST', '/v1/scenarios'),
    ]


def test_registration_propagates_a_non_404_existence_failure() -> None:
    """Any other failure on the existence check is raised, never a blind create."""
    router = _Router({('GET', _SCENARIO_ID): _error(HTTPStatus.FORBIDDEN)})
    with pytest.raises(MaivnHTTPError) as caught:
        _register(router)
    assert caught.value.status_code == HTTPStatus.FORBIDDEN
    assert [method for method, _path, _query in router.calls] == ['GET']


@pytest.mark.parametrize(
    ('body', 'message'),
    [
        ({'not_scenario': {}}, 'server returned a malformed scenario response'),
        (
            {'scenario': {'scenario': {}, 'trigger': 'x'}},
            'server returned a malformed scenario canvas',
        ),
        (_canvas(project_id=''), 'scenario response omitted project_id'),
    ],
)
def test_registration_rejects_malformed_scenario_bodies(body: dict[str, Any], message: str) -> None:
    """A malformed scenario response is an SDK error naming what is missing."""
    router = _Router({('PATCH', _SCENARIO_ID): httpx.Response(HTTPStatus.OK, json=body)})
    with pytest.raises(MaivnSDKError, match=f'^{message}$'):
        _register(router)


def test_status_requires_the_trigger_status_field() -> None:
    """A registered trigger without a status reports the omission on access."""
    router = _Router(
        {('PATCH', _SCENARIO_ID): httpx.Response(HTTPStatus.OK, json=_canvas(status=None))}
    )
    trigger = _register(router)
    with pytest.raises(MaivnSDKError, match=r'^scenario response omitted status$'):
        _ = trigger.status


# --- lifecycle and history ---------------------------------------------------------


def test_sync_lifecycle_calls_use_the_scenario_routes() -> None:
    """pause, resume and delete address the stable scenario path."""
    router = _Router(
        {
            ('POST', '/pause'): httpx.Response(HTTPStatus.OK, json=_canvas(status='paused')),
            ('POST', '/resume'): httpx.Response(HTTPStatus.OK, json=_canvas(status='enabled')),
            ('DELETE', _SCENARIO_ID): httpx.Response(HTTPStatus.OK, json={'success': True}),
        }
    )
    trigger = _register(router)
    assert trigger.pause().status == 'paused'
    assert trigger.resume().status == 'enabled'
    trigger.delete()
    assert [(method, path) for method, path, _query in router.calls[-3:]] == [
        ('POST', f'/v1/scenarios/{_SCENARIO_ID}/pause'),
        ('POST', f'/v1/scenarios/{_SCENARIO_ID}/resume'),
        ('DELETE', f'/v1/scenarios/{_SCENARIO_ID}'),
    ]


@pytest.mark.parametrize('limit', [0, 101])
def test_history_limit_is_bounded(limit: int) -> None:
    """History limits outside 1..100 are refused before any request."""
    router = _Router({})
    trigger = _register(router)
    before = len(router.calls)
    with pytest.raises(ValueError, match=r'^trigger history limit must be between 1 and 100$'):
        trigger.history(limit=limit)
    assert len(router.calls) == before


@pytest.mark.parametrize(
    ('body', 'message'),
    [
        ({'items': None}, 'server returned malformed trigger history'),
        ({'items': ['session']}, 'server returned malformed trigger history item'),
    ],
)
def test_history_rejects_malformed_pages(body: dict[str, object], message: str) -> None:
    """A history page must be a list of objects."""
    router = _Router({('GET', '/v1/sessions'): httpx.Response(HTTPStatus.OK, json=body)})
    trigger = _register(router)
    with pytest.raises(MaivnSDKError, match=f'^{message}$'):
        trigger.history(limit=100)
    assert router.calls[-1][2] == f'project_id=prj-1&scenario_id={_SCENARIO_ID}&limit=100'


def test_history_returns_items_in_order() -> None:
    """A well-formed page returns its items as a tuple."""
    items = [{'session_id': 'a'}, {'session_id': 'b'}]
    router = _Router(
        {('GET', '/v1/sessions'): httpx.Response(HTTPStatus.OK, json={'items': items})}
    )
    trigger = _register(router)
    assert trigger.history(limit=1) == ({'session_id': 'a'}, {'session_id': 'b'})
