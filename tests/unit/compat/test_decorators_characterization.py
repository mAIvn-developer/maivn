"""Characterization tests for the v1-compatible tool authoring decorators.

Decorator metadata is read through the attribute-name constants that existing
adapter tests and consumers already rely on.
"""

from __future__ import annotations

from typing import Literal, cast

import pytest

from maivn import (
    compose_argument_policy,
    depends_on_agent,
    depends_on_await_for,
    depends_on_interrupt,
    depends_on_reevaluate,
    depends_on_tool,
    requires_producer_reference,
    toolify,
    toolset,
)
from maivn._internal.compat.decorators import (
    ARGUMENT_PRODUCER_REQUIREMENTS_ATTR,
    COMPOSE_ARGUMENT_POLICIES_ATTR,
    EXECUTION_CONTROLS_ATTR,
    INTERRUPT_DEPENDENCIES_ATTR,
    TOOL_DEPENDENCIES_ATTR,
    TOOLIFY_ATTR,
    TOOLSET_ATTR,
    AgentDependency,
    ArgumentProducerRequirement,
    ComposeArgumentPolicy,
    ExecutionControl,
    InterruptDependency,
    MethodToolifyOptions,
    ToolDependency,
    ToolsetOptions,
)


def _handler(_prompt: str) -> object:
    return 'answer'


def _metadata(func: object, attr: str) -> list[object]:
    return cast('list[object]', getattr(func, attr))


def test_depends_on_tool_rejects_unknown_result_scopes() -> None:
    """Only invocation and conversation result scopes exist."""
    with pytest.raises(ValueError, match=r'^unsupported dependency result scope: thread$'):
        _ = depends_on_tool('lookup', 'value', result_scope='thread')  # pyright: ignore[reportArgumentType]


def test_tool_references_resolve_from_strings_callables_and_tool_ids() -> None:
    """Strings are both id and name; callables use ``tool_id`` when present."""

    def lookup() -> None:
        return None

    class Fetcher:
        tool_id = 'tool-fetch'

    @depends_on_tool('raw-name', 'a')
    @depends_on_tool(lookup, 'b', revision_policy='on_validation_error')
    @depends_on_tool(Fetcher, 'c', result_scope='conversation')
    def consumer(a: str, b: str, c: str) -> str:
        return a + b + c

    assert _metadata(consumer, TOOL_DEPENDENCIES_ATTR) == [
        ToolDependency('c', 'tool-fetch', 'Fetcher', None, 'conversation'),
        ToolDependency('b', 'lookup', 'lookup', 'on_validation_error', 'invocation'),
        ToolDependency('a', 'raw-name', 'raw-name', None, 'invocation'),
    ]


@pytest.mark.parametrize(
    ('path', 'tool', 'message'),
    [
        ('1bad', 'producer', 'invalid argument producer path: 1bad'),
        ('a..b', 'producer', 'invalid argument producer path: a..b'),
        ('a.b', 'bad tool', 'invalid argument producer tool name: bad tool'),
        ('a', '-x', 'invalid argument producer tool name: -x'),
    ],
)
def test_producer_requirements_validate_path_and_tool_name(
    path: str,
    tool: str,
    message: str,
) -> None:
    """Paths are dotted identifiers; producer names are identifier-like with dashes."""
    with pytest.raises(ValueError, match=r'^') as excinfo:
        _ = requires_producer_reference(path, producer_tool_name=tool)
    assert str(excinfo.value) == message


def test_producer_requirements_refuse_duplicate_paths() -> None:
    """The same argument path cannot require two producers."""

    @requires_producer_reference('block.ref', producer_tool_name='a-tool')
    def put(block: dict[str, object]) -> object:
        return block

    with pytest.raises(
        ValueError, match=r'^duplicate argument producer requirement for block.ref$'
    ):
        _ = requires_producer_reference('block.ref', producer_tool_name='b_tool')(put)
    other = requires_producer_reference('block.other', producer_tool_name='b_tool')(put)
    assert _metadata(other, ARGUMENT_PRODUCER_REQUIREMENTS_ATTR) == [
        ArgumentProducerRequirement('block.ref', 'a-tool'),
        ArgumentProducerRequirement('block.other', 'b_tool'),
    ]


@pytest.mark.parametrize(
    ('arg', 'kwargs', 'message'),
    [
        ('a.b', {}, 'invalid compose_argument target: a.b'),
        ('9x', {}, 'invalid compose_argument target: 9x'),
        ('query', {'mode': 'maybe'}, 'unsupported compose_argument mode: maybe'),
        ('query', {'approval': 'implicit'}, 'unsupported compose_argument approval: implicit'),
    ],
)
def test_compose_argument_policy_validates_its_inputs(
    arg: str,
    kwargs: dict[str, str],
    message: str,
) -> None:
    """Targets are plain identifiers; modes and approvals are closed sets."""
    with pytest.raises(ValueError, match=r'^') as excinfo:
        _ = compose_argument_policy(arg, **kwargs)  # pyright: ignore[reportArgumentType]
    assert str(excinfo.value) == message


def test_compose_argument_policy_requires_a_real_parameter_and_no_duplicates() -> None:
    """The target must be a parameter of the function and declared once."""

    def tool(query: str) -> str:
        return query

    with pytest.raises(
        ValueError,
        match=r"^compose_argument_policy target 'other' is not a function argument$",
    ):
        _ = compose_argument_policy('other')(tool)
    decorated = compose_argument_policy('query', mode='forbid')(tool)
    with pytest.raises(ValueError, match=r'^duplicate compose_argument policy for query$'):
        _ = compose_argument_policy('query')(decorated)


def test_non_required_compose_policies_add_no_producer_requirement() -> None:
    """Only ``mode='require'`` adds the compose_argument producer requirement."""

    @compose_argument_policy('body')
    @compose_argument_policy('subject', mode='forbid', approval='explicit')
    def send(subject: str, body: str) -> str:
        return subject + body

    assert _metadata(send, COMPOSE_ARGUMENT_POLICIES_ATTR) == [
        ComposeArgumentPolicy('subject', 'forbid', 'explicit'),
        ComposeArgumentPolicy('body', 'allow', 'none'),
    ]
    assert getattr(send, ARGUMENT_PRODUCER_REQUIREMENTS_ATTR, None) is None


def test_execution_controls_record_kind_timing_and_instances() -> None:
    """await_for and reevaluate record relative execution controls in order."""

    def upstream() -> None:
        return None

    @depends_on_reevaluate('planner', timing='before', instance_control='last')
    @depends_on_await_for(upstream)
    def downstream() -> None:
        return None

    assert _metadata(downstream, EXECUTION_CONTROLS_ATTR) == [
        ExecutionControl('await_for', 'upstream', 'upstream', 'after', 'each'),
        ExecutionControl('reevaluate', 'planner', 'planner', 'before', 'last'),
    ]


def test_interrupt_input_type_is_inferred_from_annotations() -> None:
    """Literal, bool, int and float annotations pick choice, boolean and number."""

    @depends_on_interrupt('color', _handler)
    @depends_on_interrupt('ok', _handler)
    @depends_on_interrupt('count', _handler)
    @depends_on_interrupt('ratio', _handler)
    @depends_on_interrupt('note', _handler, prompt='Note?')
    @depends_on_interrupt('missing', _handler)
    def tool(  # noqa: PLR0913 - one argument per inferred input type.
        color: Literal['red', 1],
        ok: bool,  # noqa: FBT001
        count: int,
        ratio: float,
        note: str,
        missing: object = None,
    ) -> object:
        return (color, ok, count, ratio, note, missing)

    dependencies = cast('list[InterruptDependency]', _metadata(tool, INTERRUPT_DEPENDENCIES_ATTR))
    assert [(d.arg_name, d.input_type, d.choices, d.prompt_source) for d in dependencies] == [
        ('missing', 'text', (), 'fallback'),
        ('note', 'text', (), 'authored'),
        ('ratio', 'number', (), 'fallback'),
        ('count', 'number', (), 'fallback'),
        ('ok', 'boolean', (), 'fallback'),
        ('color', 'choice', ('red', '1'), 'fallback'),
    ]


def test_interrupt_explicit_type_and_choices_override_inference() -> None:
    """Declared input types and choices win over the annotation."""

    @depends_on_interrupt('count', _handler, input_type='choice', choices=['1', '2'])
    def tool(count: int) -> int:
        return count

    (dependency,) = cast('list[InterruptDependency]', _metadata(tool, INTERRUPT_DEPENDENCIES_ATTR))
    assert (dependency.input_type, dependency.choices) == ('choice', ('1', '2'))
    assert dependency.input_handler is _handler


def test_interrupt_with_unresolvable_annotations_falls_back_to_text() -> None:
    """Annotations that cannot be resolved, or are absent, mean free text."""

    def tool(value: object) -> object:
        return value

    def bare(value: object) -> object:
        return value

    tool.__annotations__['value'] = 'UndefinedName'
    del bare.__annotations__['value']
    for func in (tool, bare):
        decorated = depends_on_interrupt('value', _handler)(func)
        (dependency,) = cast(
            'list[InterruptDependency]',
            _metadata(decorated, INTERRUPT_DEPENDENCIES_ATTR),
        )
        assert (dependency.input_type, dependency.choices) == ('text', ())


def test_agent_dependencies_resolve_agent_id_then_id_then_name_then_str() -> None:
    """Agent references prefer agent_id, then id, then name, then ``str(ref)``."""

    class WithAgentId:
        agent_id = 'agent-1'
        id = 'ignored'

    class WithId:
        agent_id = ''
        id = 'id-2'
        name = 'ignored-name'

    class WithName:
        name = 'named'

    class Plain:
        def __str__(self) -> str:
            return 'plain-ref'

    refs: list[object] = [WithAgentId(), WithId(), WithName(), Plain()]
    expected = ['agent-1', 'id-2', 'named', 'plain-ref']
    for ref, agent_id in zip(refs, expected, strict=True):

        def tool(x: str) -> str:
            return x

        decorated = depends_on_agent(ref, 'x')(tool)
        assert _metadata(decorated, TOOL_DEPENDENCIES_ATTR) == [
            AgentDependency(arg_name='x', agent_id=agent_id, agent_ref=ref),
        ]


def test_toolify_applies_directly_or_with_options() -> None:
    """Bare ``@toolify`` and configured ``@toolify(...)`` both record options."""

    @toolify
    def bare() -> None:
        return None

    @toolify(name='n', tags=['a'], metadata={'k': 1}, destructive=True)
    def configured() -> None:
        return None

    assert getattr(bare, TOOLIFY_ATTR) == MethodToolifyOptions()
    assert getattr(configured, TOOLIFY_ATTR) == MethodToolifyOptions(
        name='n',
        tags=('a',),
        metadata={'k': 1},
        destructive=True,
    )


def test_toolset_applies_directly_or_with_options() -> None:
    """Bare ``@toolset`` and configured ``@toolset(...)`` both record options."""

    @toolset
    class Bare:
        pass

    @toolset(prefix='crm', tags=('sales',), require_marker=False, metadata={'v': 2})
    class Configured:
        pass

    assert getattr(Bare, TOOLSET_ATTR) == ToolsetOptions()
    assert getattr(Configured, TOOLSET_ATTR) == ToolsetOptions(
        prefix='crm',
        tags=('sales',),
        require_marker=False,
        metadata={'v': 2},
    )


def test_subclass_metadata_does_not_mutate_the_parent_list() -> None:
    """Decorating an inheriting target copies the inherited metadata list."""

    class Base:
        pass

    parent = depends_on_tool('a', 'x')(Base)

    class Child(parent):
        pass

    _ = depends_on_tool('b', 'y')(Child)
    assert _metadata(Base, TOOL_DEPENDENCIES_ATTR) == [ToolDependency('x', 'a', 'a')]
    assert _metadata(Child, TOOL_DEPENDENCIES_ATTR) == [
        ToolDependency('x', 'a', 'a'),
        ToolDependency('y', 'b', 'b'),
    ]
