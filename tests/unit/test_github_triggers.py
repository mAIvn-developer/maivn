"""GitHub preset declarations retain exact provider selectors at registration."""

from __future__ import annotations

import json
from typing import TYPE_CHECKING, cast

import httpx
import pytest
from maivn_contracts.scenarios import GitHubConnectionSelector, matches_connection_selector

from maivn import Agent, Client, ClientConfig, TriggerInvocationBuilder

if TYPE_CHECKING:
    from collections.abc import Callable

CASES: tuple[tuple[Callable[[Agent], TriggerInvocationBuilder], str], ...] = (
    (lambda a: a.on_pull_request('conn_github', repository='owner/repo'), 'pull_request.opened'),
    (
        lambda a: a.on_pull_request('conn_github', action='reopened', repository='owner/repo'),
        'pull_request.reopened',
    ),
    (
        lambda a: a.on_pull_request('conn_github', action='synchronize', repository='owner/repo'),
        'pull_request.synchronize',
    ),
    (
        lambda a: a.on_pull_request('conn_github', action='closed', repository='owner/repo'),
        'pull_request.closed',
    ),
    (lambda a: a.on_issue('conn_github', repository='owner/repo'), 'issues.opened'),
    (
        lambda a: a.on_issue('conn_github', action='reopened', repository='owner/repo'),
        'issues.reopened',
    ),
    (
        lambda a: a.on_issue('conn_github', action='closed', repository='owner/repo'),
        'issues.closed',
    ),
    (
        lambda a: a.on_issue('conn_github', action='labeled', repository='owner/repo'),
        'issues.labeled',
    ),
    (lambda a: a.on_push('conn_github', repository='owner/repo'), 'push'),
    (lambda a: a.on_workflow_run('conn_github', repository='owner/repo'), 'workflow_run.completed'),
)


@pytest.mark.parametrize(('build', 'event_type'), CASES)
def test_each_preset_registers_selector_even_with_where(
    build: Callable[[Agent], TriggerInvocationBuilder], event_type: str
) -> None:
    """Observe real public-client transport; declaration itself performs no network call."""
    calls: list[httpx.Request] = []

    def receive(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        if request.method == 'GET':
            return httpx.Response(404)
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
            api_key='fixture',
            base_url='https://data.example',
        ),
        transport=httpx.MockTransport(receive),
    )
    agent = Agent('github', client=client)
    builder = build(agent).key('github-event').target('github-agent', version=1)
    assert calls == []
    builder.where('$.repository.full_name').invoke('Handle GitHub event.')
    assert [call.method for call in calls] == ['GET', 'POST']
    payload = json.loads(calls[-1].content)
    assert payload['trigger_source'] == {
        'kind': 'external_connection_event',
        'phase': 'phase_2',
        'connection_id': 'conn_github',
        'event_type': event_type,
        'selector': {'kind': 'github', 'repository': 'owner/repo'},
    }
    assert payload['trigger_filter'] == '$.repository.full_name'


@pytest.mark.parametrize('method', ['on_pull_request', 'on_issue'])
@pytest.mark.parametrize('action', [True, False, 'edited', 'completed', '', 0, None])
def test_actions_are_finite_before_declaration(method: str, action: object) -> None:
    """Untyped callers cannot turn preset actions into unchecked generic event names."""
    agent = Agent('github', api_key='fixture')
    invoke = cast('Callable[...,TriggerInvocationBuilder]', getattr(agent, method))
    with pytest.raises(ValueError, match='action'):
        invoke('conn_github', action=action)
    assert agent.declared_triggers == []


@pytest.mark.parametrize('merged', [1, 0, 'true', [], {}])
def test_merged_is_a_strict_boolean(merged: object) -> None:
    """A closed PR means merged only when the signed provider boolean is actually true."""
    agent = Agent('github', api_key='fixture')
    call = cast('Callable[...,TriggerInvocationBuilder]', agent.on_pull_request)
    with pytest.raises(ValueError, match='merged'):
        call('conn_github', action='closed', merged=merged)
    assert agent.declared_triggers == []


@pytest.mark.parametrize(
    'conclusion',
    [
        'success',
        'failure',
        'cancelled',
        'timed_out',
        'neutral',
        'skipped',
        'action_required',
        'stale',
        'startup_failure',
    ],
)
def test_supported_conclusions_remain_exact(conclusion: str) -> None:
    """Every supported completed-workflow conclusion keeps its provider value."""
    call = cast(
        'Callable[...,TriggerInvocationBuilder]', Agent('github', api_key='fixture').on_workflow_run
    )
    source = call('conn_github', conclusion=conclusion).source.model_dump(exclude_none=True)
    assert source['event_type'] == 'workflow_run.completed'
    assert source['selector'] == {'kind': 'github', 'conclusion': conclusion}


@pytest.mark.parametrize('conclusion', [True, False, 0, 'completed', 'FAILED', [], {}])
def test_unknown_conclusion_refuses_before_registration(conclusion: object) -> None:
    """Completed workflow is an event action, never a substitute conclusion."""
    agent = Agent('github', api_key='fixture')
    call = cast('Callable[...,TriggerInvocationBuilder]', agent.on_workflow_run)
    with pytest.raises(ValueError, match='conclusion'):
        call('conn_github', conclusion=conclusion)
    assert agent.declared_triggers == []


def test_event_family_branch_paths_and_merged_predicate() -> None:
    """The helpers preserve typed predicates rather than inventing one generic branch path."""
    agent = Agent('github', api_key='fixture')
    pr = agent.on_pull_request(
        'conn_github', action='closed', repository='owner/repo', branch='main', merged=True
    )
    push = agent.on_push('conn_github', repository='owner/repo', branch='main')
    ci = agent.on_workflow_run(
        'conn_github', repository='owner/repo', branch='main', workflow='CI', conclusion='failure'
    )
    pr_selector = GitHubConnectionSelector.model_validate(pr.source.model_dump()['selector'])
    push_selector = GitHubConnectionSelector.model_validate(push.source.model_dump()['selector'])
    ci_selector = GitHubConnectionSelector.model_validate(ci.source.model_dump()['selector'])
    repository = {'repository': {'full_name': 'owner/repo'}}
    assert matches_connection_selector(
        pr_selector,
        'pull_request.closed',
        {
            **repository,
            'pull_request': {'base': {'ref': 'main'}, 'head': {'ref': 'other'}, 'merged': True},
        },
    )
    assert not matches_connection_selector(
        pr_selector,
        'pull_request.closed',
        {
            **repository,
            'pull_request': {'base': {'ref': 'other'}, 'head': {'ref': 'main'}, 'merged': True},
        },
    )
    assert not matches_connection_selector(
        pr_selector,
        'pull_request.closed',
        {**repository, 'pull_request': {'base': {'ref': 'main'}, 'merged': False}},
    )
    assert matches_connection_selector(
        push_selector, 'push', {**repository, 'ref': 'refs/heads/main'}
    )
    assert not matches_connection_selector(
        push_selector, 'push', {**repository, 'ref': 'refs/tags/main'}
    )
    assert matches_connection_selector(
        ci_selector,
        'workflow_run.completed',
        {
            **repository,
            'workflow_run': {'head_branch': 'main', 'name': 'CI', 'conclusion': 'failure'},
        },
    )
    assert not matches_connection_selector(
        ci_selector,
        'workflow_run.completed',
        {
            **repository,
            'workflow_run': {'head_branch': 'main', 'name': 'CI', 'conclusion': 'success'},
        },
    )
    assert not matches_connection_selector(ci_selector, 'workflow_run.completed', repository)


def test_optional_selectors_and_quoted_workflow_are_literal() -> None:
    """Omitted selectors remain broad; display text is data, including quotes."""
    agent = Agent('github', api_key='fixture')
    assert agent.on_push('conn_github').source.model_dump(mode='json', exclude_none=True)[
        'selector'
    ] == {'kind': 'github'}
    label = 'CI "quoted"; ${untrusted}'
    selector = (
        agent.on_workflow_run('conn_github', workflow=label)
        .where('$.sender')
        .source.model_dump(mode='json', exclude_none=True)['selector']
    )
    assert selector == {'kind': 'github', 'workflow': label}
    assert (
        agent.on_connection('conn_github', event_type='pull_request.edited').source.model_dump()[
            'event_type'
        ]
        == 'pull_request.edited'
    )
