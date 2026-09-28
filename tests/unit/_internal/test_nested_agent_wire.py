"""Nested delegation stays scoped to the declared swarm member."""

from __future__ import annotations

from itertools import pairwise
from typing import Any

import httpx
import pytest

from maivn import Agent, Client, ClientConfig
from maivn._internal.wire import (
    delegate_tools,
    invocation_options,
    swarm_member_tools,
    swarm_roster_payload,
)


def _agent(name: str) -> Agent:
    return Agent(name=name, api_key='test-key')


def _depends(owner: Agent, dependency: Agent, name: str) -> None:
    def consume(result: str) -> str:
        return result

    owner.toolify(name=name).depends_on_agent(dependency, 'result')(consume)


def test_nested_delegate_wire_and_local_tools() -> None:
    """Nonmember delegates never become planner members; their tools register."""
    member, researcher, source = (_agent(n) for n in ('member', 'researcher', 'source'))
    _depends(member, researcher, 'report')
    _depends(researcher, source, 'research')

    @source.toolify(name='read_source')
    def read_source() -> str:
        return 'evidence'

    _ = read_source
    payload: Any = swarm_roster_payload([member])
    assert [a['name'] for a in payload['agents']] == ['member']
    spec = payload['agents'][0]
    assert spec['agent_tool_dependencies'] == [
        {'tool_id': 'report', 'agent_name': 'researcher', 'arg_name': 'result', 'optional': False},
    ]
    assert spec['agents'][0]['agents'][0]['name'] == 'source'
    assert {t.name for t in swarm_member_tools([member])} == {'report', 'research', 'read_source'}
    assert {t.name for t in delegate_tools(member.compile_tools())} == {'research', 'read_source'}


def test_shared_delegate_and_optional_edge() -> None:
    """Shared DAG nodes are valid and dependency optionality is preserved."""
    delegate, first, second = (_agent(n) for n in ('delegate', 'first', 'second'))
    _depends(first, delegate, 'first_tool')

    @second.toolify(name='optional_tool').depends_on_agent(delegate, 'result')
    def optional_tool(result: str = 'default') -> str:
        return result

    _ = optional_tool
    payload: Any = swarm_roster_payload([first, second])
    assert [a['name'] for a in payload['agents']] == ['first', 'second']
    assert payload['agents'][1]['agent_tool_dependencies'][0]['optional'] is True
    assert 'agents' not in payload['agents'][0]['agents'][0]


@pytest.mark.parametrize('builder', [swarm_roster_payload, swarm_member_tools])
def test_cycles_rejected(builder: Any) -> None:
    """Reject cyclic declarations before transport or local registration."""
    first, second = _agent('first'), _agent('second')
    _depends(first, second, 'first_tool')
    _depends(second, first, 'second_tool')
    with pytest.raises(ValueError, match='cycle'):
        builder([first])


@pytest.mark.parametrize('builder', [swarm_roster_payload, swarm_member_tools])
def test_depth_limit(builder: Any) -> None:
    """Deep acyclic graphs receive a bounded declaration error."""
    agents = [_agent(f'agent-{i}') for i in range(17)]
    for index, (parent, child) in enumerate(pairwise(agents)):
        _depends(parent, child, f'tool-{index}')
    with pytest.raises(ValueError, match='16'):
        builder(agents[:1])


def test_simple_standalone_wire_unchanged() -> None:
    """One-level delegates do not gain empty nested fields."""
    parent, child = _agent('parent'), _agent('child')
    _depends(parent, child, 'consume')
    payload: Any = invocation_options(
        parent.compile_tools(), private_data=None, force_final_tool=False
    )
    assert payload['agents'] == [{'name': 'child', 'model': 'auto'}]


def test_conflicting_delegate_tool_name_rejected_before_transport() -> None:
    """A nested delegate cannot accidentally execute a member's same-named tool."""
    member, delegate = _agent('member'), _agent('delegate')
    _depends(member, delegate, 'consume')

    @member.toolify(name='shared')
    def member_tool() -> str:
        return 'member'

    @delegate.toolify(name='shared')
    def delegate_tool() -> str:
        return 'delegate'

    _ = member_tool, delegate_tool
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(500)

    client = Client(config=ClientConfig(api_key='test-key'), transport=httpx.MockTransport(handler))
    with pytest.raises(ValueError, match=r'Conflicting local tool registration.*shared'):
        client.invoke('run', swarm_members=[member])
    assert requests == []


def test_same_target_shared_by_member_and_delegate_is_registered_once() -> None:
    """Repeated registration of the identical callable remains valid."""
    member, delegate = _agent('member'), _agent('delegate')
    _depends(member, delegate, 'consume')

    def shared() -> str:
        return 'shared'

    member.toolify(name='shared')(shared)
    delegate.toolify(name='shared')(shared)
    assert [tool.name for tool in swarm_member_tools([member])].count('shared') == 1
