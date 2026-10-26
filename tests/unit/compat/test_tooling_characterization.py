"""Characterization tests for v1-compatible tool registration on ``maivn.Agent``.

Agents are built with a placeholder key and a test base URL; registering and
compiling tools never touches the network.
"""

from __future__ import annotations

from typing import ClassVar

import pytest
from pydantic import BaseModel

from maivn import (
    Agent,
    PermissionFlag,
    PermissionSet,
    depends_on_tool,
    tool_output,
    toolify,
    toolify_options,
    toolset,
    toolset_options,
)
from maivn._internal.compat.decorators import TOOL_DEPENDENCIES_ATTR, ToolDependency


def _agent(name: str = 'tooling') -> Agent:
    return Agent(name=name, api_key='test-key', base_url='http://testserver')


def _ask(_prompt: str) -> object:
    return True


def test_fluent_builder_records_execution_controls_and_hides_interrupt_args() -> None:
    """Builder dependencies become execution controls; interrupt args leave the schema."""
    agent = _agent()

    def fetch(
        query: str,
        approved: bool,  # noqa: FBT001 - interrupt-filled flag.
        loose: object,
        *args: object,
        **kwargs: object,
    ) -> dict[str, object]:
        """Fetch things."""
        return {'query': query, 'approved': approved, 'loose': loose, 'args': args, 'kw': kwargs}

    # An unannotated parameter falls back to a name-only schema.
    del fetch.__annotations__['loose']
    registered = (
        agent.toolify(name='fetch')
        .depends_on_await_for('prep', timing='before', instance_control='first')
        .depends_on_reevaluate('audit')
        .depends_on_interrupt('approved', 'Approve?', _ask, input_type='boolean')
    )(fetch)
    assert registered is fetch

    (compiled,) = agent.compile_tools()
    assert compiled.name == 'fetch'
    assert compiled.description == 'Fetch things.'
    assert compiled.input_schema == {
        'type': 'object',
        'properties': {'query': {'type': 'string'}, 'loose': {'description': 'loose'}},
        'additionalProperties': False,
        'required': ['query', 'loose'],
    }
    assert compiled.metadata == {
        'execution_controls': {
            'await_for': [
                {
                    'tool_id': 'prep',
                    'tool_name': 'prep',
                    'timing': 'before',
                    'instance_control': 'first',
                },
            ],
            'reevaluate': [
                {
                    'tool_id': 'audit',
                    'tool_name': 'audit',
                    'timing': 'after',
                    'instance_control': 'each',
                },
            ],
        },
    }
    assert compiled.output_schema is None
    assert getattr(fetch, 'tool_id', None) == 'fetch'


def test_metadata_decorators_applied_to_the_builder_reach_the_tool_once() -> None:
    """Decorators wrapped around the builder itself are copied onto the target, deduplicated."""
    agent = _agent()
    schema = {'type': 'object', 'properties': {'ok': {'type': 'boolean'}}}
    builder = agent.toolify(name='consumer')
    builder = depends_on_tool('producer', 'upstream')(builder)
    builder = tool_output(schema)(builder)

    @builder
    @depends_on_tool('producer', 'upstream')
    def consumer(upstream: dict[str, object], limit: int = 5) -> dict[str, object]:
        return {'upstream': upstream, 'limit': limit}

    assert getattr(consumer, 'tool_id', None) == 'consumer'
    assert getattr(consumer, TOOL_DEPENDENCIES_ATTR) == [
        ToolDependency('upstream', 'producer', 'producer'),
    ]

    (compiled,) = agent.compile_tools()
    assert compiled.output_schema == schema
    assert compiled.input_schema['properties'] == {
        'limit': {'type': 'integer'},
    }
    assert 'required' not in compiled.input_schema
    assert compiled.input_schema['description'] == (
        'Public dependency guidance:\n\n'
        '- upstream: supplied by producer; this tool can execute only after that producer '
        'succeeds. The runtime supplies its result; do not copy it into arguments.'
    )


def test_permission_names_and_destructive_tags_from_registration() -> None:
    """String permissions are lowercased; destructive flags tag the tool; unknown shapes drop."""
    agent = _agent()

    class Empty(BaseModel):
        pass

    _ = agent.add_tool(Empty, permissions='READ')
    _ = agent.add_tool(len, permissions=5)
    _ = agent.add_tool(
        sorted,
        name='admin_sort',
        permissions=PermissionSet(PermissionFlag.ADMIN | PermissionFlag.READ),
        tags=['ops', 'ops'],
    )
    tools = {tool.name: tool for tool in agent.list_tools()}
    assert (tools['Empty'].metadata, tools['Empty'].tags) == ({'permissions': ['read']}, ('read',))
    assert tools['Empty'].input_schema == {'properties': {}, 'title': 'Empty', 'type': 'object'}
    assert (tools['len'].metadata, tools['len'].tags) == ({}, ())
    assert (tools['len'].description or '').startswith('Return the number of items in a container.')
    assert tools['admin_sort'].metadata == {'permissions': ['read', 'admin']}
    assert tools['admin_sort'].tags == ('ops', 'read', 'admin', 'destructive')


def test_nested_model_used_in_a_union_is_registered_but_not_injected() -> None:
    """A model reachable only through a union becomes a tool without becoming a dependency."""

    class Leaf(BaseModel):
        x: int

    class Root(BaseModel):
        a: Leaf | list[Leaf]

    agent = _agent()
    _ = agent.add_tool(Root)
    compiled = agent.compile_tools()
    assert [tool.name for tool in compiled] == ['Leaf', 'Root']
    assert compiled[1].input_schema['required'] == ['a']


def test_dependencies_on_unregistered_producers_are_ignored_by_the_graph() -> None:
    """A declared producer outside the scope does not add a compiled tool."""
    agent = _agent()

    @agent.toolify()
    @depends_on_tool('elsewhere', 'data')
    def consume(data: object, note: str) -> str:
        return f'{data}{note}'

    assert getattr(consume, 'tool_id', None) == 'consume'

    assert [tool.name for tool in agent.compile_tools()] == ['consume']


def test_add_toolset_refuses_undecorated_classes() -> None:
    """Only ``@toolset`` classes can be added as toolsets."""

    class Plain:
        def go(self) -> None:
            return None

    with pytest.raises(TypeError, match=r'^') as excinfo:
        _ = _agent().add_toolset(Plain())
    assert str(excinfo.value) == (
        'Plain is not a toolset. Decorate the class with @maivn.toolset(...) '
        'before passing instances to add_toolset().'
    )


def test_add_toolset_without_marked_methods_explains_how_to_fix_it() -> None:
    """An empty marked toolset names the missing decorator or the over-tight filter."""

    @toolset(prefix='bare')
    class Bare:
        def unmarked(self) -> None:
            return None

    @toolset(prefix='ops')
    class Ops:
        @toolify
        def read(self) -> None:
            return None

    with pytest.raises(ValueError, match=r'^') as missing:
        _ = _agent().add_toolset(Bare())
    assert str(missing.value) == (
        "Toolset 'Bare' has no @toolify-marked methods to register. "
        'Decorate methods with @maivn.toolify or set @toolset(require_marker=False) '
        'for grandfathered classes.'
    )
    with pytest.raises(ValueError, match=r'^') as filtered:
        _ = _agent().add_toolset(Ops(), include_tags=['write'])
    assert str(filtered.value) == (
        "Toolset 'Ops' has no @toolify-marked methods to register. "
        'All matching methods were filtered out by include/exclude rules; '
        'loosen the filter or set @toolset(require_marker=False).'
    )


def test_unmarked_toolsets_register_plain_public_methods_only() -> None:
    """Without a marker requirement, public instance methods register under a derived prefix."""

    @toolset(require_marker=False, metadata={'team': 'crm'})
    class HTTPClientTools:
        label: ClassVar[str] = 'not callable'

        class Nested:
            pass

        @property
        def prop(self) -> str:
            return 'p'

        def _private(self) -> None:
            return None

        def fetch_page(self, url: str) -> str:
            return url

        @toolify(description='Explicit.', permissions='WRITE', destructive=True)
        def post_page(self, body: str) -> str:
            return body

    agent = _agent()
    handles = agent.add_toolset(HTTPClientTools())
    by_name = {tool.name: tool for tool in handles}
    assert sorted(by_name) == ['HTTP_CLIENT_TOOLS_fetch_page', 'HTTP_CLIENT_TOOLS_post_page']
    fetch = by_name['HTTP_CLIENT_TOOLS_fetch_page']
    post = by_name['HTTP_CLIENT_TOOLS_post_page']
    assert fetch.description == 'Invoke HTTP_CLIENT_TOOLS_fetch_page.'
    assert fetch.metadata == {'team': 'crm'}
    assert fetch.tags == ()
    assert post.description == 'Explicit.'
    assert post.metadata == {'team': 'crm', 'permissions': ['write'], 'destructive': True}
    assert post.tags == ('destructive', 'write')


def test_unmarked_toolsets_skip_classmethods_and_staticmethods() -> None:
    """``require_marker=False`` registers neither a classmethod nor a staticmethod (U05-D6)."""

    @toolset(require_marker=False, prefix='ops')
    class Ops:
        @classmethod
        def build(cls) -> str:
            return cls.__name__

        @staticmethod
        def ping() -> str:
            return 'pong'

        def run(self) -> str:
            return 'ran'

    handles = _agent().add_toolset(Ops())

    assert [tool.name for tool in handles] == ['OPS_run']


def test_toolset_include_tags_drop_methods_without_a_matching_tag() -> None:
    """``include_tags`` keeps only methods whose combined tags intersect it."""

    @toolset(prefix='kb')
    class Knowledge:
        @toolify(tags=['read'])
        def search(self, query: str) -> str:
            """Search the knowledge base."""
            return query

        @toolify(tags=['write'])
        def store(self, text: str) -> str:
            return text

    handles = _agent().add_toolset(Knowledge(), include_tags=['read'], exclude_tags=['write'])
    assert [(tool.name, tool.description, tool.tags) for tool in handles] == [
        ('KB_search', 'Search the knowledge base.', ('read',)),
    ]


def test_options_readers_return_what_the_decorators_recorded() -> None:
    """``toolset_options`` and ``toolify_options`` read decorator options or None."""

    @toolset(prefix='x', tags=('t',))
    class Marked:
        @toolify(name='go')
        def run(self) -> None:
            return None

    class Unmarked:
        def run(self) -> None:
            return None

    options = toolset_options(Marked)
    assert options is not None
    assert (options.prefix, options.tags, options.require_marker) == ('x', ('t',), True)
    assert toolset_options(Unmarked) is None
    method = toolify_options(Marked.run)
    assert method is not None
    assert method.name == 'go'
    assert toolify_options(Unmarked.run) is None
