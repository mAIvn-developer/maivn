"""Characterization tests for Agent and Swarm constructor input normalization."""

from __future__ import annotations

from typing import Any, cast

import pytest
from pydantic import BaseModel

from maivn import (
    Agent,
    MCPServer,
    MemoryConfig,
    PrivateData,
    SessionOrchestrationConfig,
    Skill,
    SkillStep,
    Swarm,
    SystemToolsConfig,
)


class _Ticket(BaseModel):
    """Open a support ticket."""

    title: str


def _lookup(query: str) -> str:
    return query


def _donated(query: str) -> str:
    return query


def _agent(**kwargs: Any) -> Agent:
    return Agent(name='norm-agent', api_key='test-key', base_url='http://testserver', **kwargs)


def _swarm(**kwargs: Any) -> Swarm:
    return Swarm(name='norm-swarm', **kwargs)


def _skill() -> Skill:
    return Skill(
        name='Triage', description='Triage tickets.', steps=(SkillStep(index=1, action='Read'),)
    )


def test_initial_tools_accept_metadata_models_and_callables() -> None:
    """Constructor tools may be compiled metadata, pydantic models, or plain callables."""
    donor = _agent()
    donor.add_tool(_donated, name='donated')
    compiled = donor.compile_tools()[0]

    agent = _agent(tools=[compiled, _Ticket, _lookup])

    assert [tool.name for tool in agent.compile_tools()] == ['donated', '_Ticket', '_lookup']


@pytest.mark.parametrize('factory', [_agent, _swarm])
def test_initial_tools_reject_non_callable_entries(factory: Any) -> None:
    """A non-callable tool entry raises a TypeError naming the accepted shapes."""
    with pytest.raises(TypeError) as raised:
        factory(tools=[42])

    assert str(raised.value) == 'tools must contain ToolMetadata or callable entries'


def test_skill_mappings_promote_legacy_ids_to_memory_id() -> None:
    """Legacy skill_id and id keys become memory_id without overriding an explicit one."""
    base = {
        'name': 'Triage',
        'description': 'Triage tickets.',
        'steps': [{'index': 1, 'action': 'Read'}],
    }
    skill = _skill()

    agent = _agent(
        skills=[
            skill,
            {**base, 'skill_id': 'skill-legacy'},
            {**base, 'id': 7},
            {**base, 'memory_id': 'explicit', 'skill_id': 'ignored', 'id': 'ignored-too'},
            dict(base),
        ],
    )

    skills = cast('list[Skill]', agent.skills)
    assert skills[0] is skill
    assert [item.memory_id for item in skills] == [None, 'skill-legacy', '7', 'explicit', None]


@pytest.mark.parametrize(
    ('field', 'value', 'expected'),
    [
        ('skills', 'not-a-list', 'skills must be a list of dictionaries, Skill objects, or None'),
        ('skills', b'bytes', 'skills must be a list of dictionaries, Skill objects, or None'),
        ('skills', 5, 'skills must be a list of dictionaries, Skill objects, or None'),
        ('skills', [_skill(), 'text'], 'skills[1] must be a dictionary or Skill'),
        ('resources', 'not-a-list', 'resources must be a list of dictionaries or None'),
        ('resources', [{'name': 'r'}, 3], 'resources[1] must be a dictionary'),
        ('tags', 'tag', 'tags must be a list of strings or None'),
        ('memory_config', 5, 'memory_config must be a MemoryConfig, dictionary, or None'),
        (
            'system_tools_config',
            'web',
            'system_tools_config must be a SystemToolsConfig, dictionary, or None',
        ),
        (
            'orchestration_config',
            7,
            'orchestration_config must be a SessionOrchestrationConfig, dictionary, or None',
        ),
        (
            'private_data',
            'secret',
            'private_data must be a dictionary, list of PrivateData, or None',
        ),
        (
            'private_data',
            [{'name': 'missing-value'}],
            'private_data list entries must be PrivateData objects or value dicts',
        ),
        (
            'private_data',
            ['raw'],
            'private_data list entries must be PrivateData objects or value dicts',
        ),
    ],
)
def test_agent_constructor_rejects_malformed_inputs(
    field: str, value: object, expected: str
) -> None:
    """Each malformed constructor field raises a TypeError with its exact message."""
    with pytest.raises(TypeError) as raised:
        _ = _agent(**{field: value})

    assert str(raised.value) == expected


def test_agent_constructor_normalizes_lists_and_configs() -> None:
    """None becomes empty, tags become strings, and config instances are kept as-is."""
    memory = MemoryConfig(enabled=False)
    tools_config = SystemToolsConfig(allowed_tools=['web_search'])
    orchestration = SessionOrchestrationConfig()

    agent = _agent(
        skills=None,
        resources=None,
        tags=[1, 'two'],
        memory_config=memory,
        system_tools_config=tools_config,
        orchestration_config=orchestration,
        private_data=None,
    )

    assert agent.skills == []
    assert agent.resources == []
    assert agent.tags == ['1', 'two']
    assert agent.memory_config is memory
    assert agent.system_tools_config is tools_config
    assert agent.orchestration_config is orchestration
    assert agent.private_data == {}


def test_agent_constructor_validates_config_mappings() -> None:
    """Mapping configs are validated into their compat models."""
    agent = _agent(
        memory_config={'enabled': False},
        system_tools_config={'allowed_tools': []},
        orchestration_config={'allow_reevaluate_loop': True},
        tags=None,
    )

    assert agent.memory_config == MemoryConfig(enabled=False)
    assert agent.system_tools_config == SystemToolsConfig(allowed_tools=[])
    assert agent.orchestration_config == SessionOrchestrationConfig(allow_reevaluate_loop=True)
    assert agent.tags == []


def test_private_data_lists_become_named_mappings() -> None:
    """Named entries keep their names; unnamed entries get sequential placeholders."""
    agent = _agent(
        private_data=[
            PrivateData(name='email', value='dana@example.test'),
            {'value': 'first-unnamed'},
            PrivateData(value='second-unnamed'),
            {'name': 'account', 'value': 'acct-1'},
        ],
    )

    assert agent.private_data == {
        'email': 'dana@example.test',
        '_private_1': 'first-unnamed',
        '_private_2': 'second-unnamed',
        'account': 'acct-1',
    }


def test_private_data_mapping_is_copied() -> None:
    """A private_data mapping is copied into a new dict."""
    source: dict[object, object] = {'email': 'dana@example.test'}

    agent = _agent(private_data=source)

    assert agent.private_data == source
    assert agent.private_data is not source


def test_private_data_rejects_duplicate_names() -> None:
    """Two list entries with the same name raise a ValueError naming it."""
    with pytest.raises(ValueError, match=r'^duplicate private_data name: email$'):
        _ = _agent(
            private_data=[
                PrivateData(name='email', value='a@example.test'),
                {'name': 'email', 'value': 'b@example.test'},
            ],
        )


def test_mcp_server_registration_validates_and_deduplicates() -> None:
    """The same server object is idempotent; a different one with the same name is refused."""
    agent = _agent()
    server = MCPServer(name='files')

    agent.register_mcp_servers((server,))
    agent.register_mcp_servers(server)

    assert agent.list_mcp_servers() == [server]
    with pytest.raises(ValueError, match=r'^MCP server name already registered: files$'):
        agent.register_mcp_servers(MCPServer(name='files'))
    with pytest.raises(TypeError, match=r'^register_mcp_servers expects MCPServer instances$'):
        agent.register_mcp_servers([cast('MCPServer', 'files')])
    with pytest.raises(
        TypeError,
        match=r'^register_mcp_servers expects an MCPServer or sequence of MCPServer$',
    ):
        agent.register_mcp_servers(cast('MCPServer', 'files'))
    assert agent.list_mcp_servers() == [server]


def test_swarm_constructor_normalizes_like_an_agent() -> None:
    """Swarm applies the same list, tag, and config normalization."""
    swarm = Swarm(
        name='norm-swarm',
        tools=[_lookup],
        skills=[
            {
                'name': 'Triage',
                'description': 'D.',
                'steps': [{'index': 1, 'action': 'Go'}],
                'id': 'sk',
            }
        ],
        resources=[{'name': 'r'}],
        tags=cast('Any', ('a', 2)),
        system_tools_config={'allowed_tools': []},
        orchestration_config=None,
    )

    assert [tool.name for tool in swarm.compile_tools()] == ['_lookup']
    assert [cast('Skill', item).memory_id for item in swarm.skills] == ['sk']
    assert swarm.resources == [{'name': 'r'}]
    assert swarm.tags == ['a', '2']
    assert swarm.system_tools_config == SystemToolsConfig(allowed_tools=[])
    assert swarm.orchestration_config == SessionOrchestrationConfig()
