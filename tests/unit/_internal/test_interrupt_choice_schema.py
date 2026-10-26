"""Interrupt choices always serialize as a satisfiable string enum (U05-D15).

The brain renders a single choice only as ``{'type': 'string', 'enum': [...]}`` and the
SDK declares ``choices`` as strings, so the schema must say string whatever the argument's
annotation is.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Literal

import pytest

from maivn import Agent, depends_on_interrupt
from maivn._internal.wire import _function_tool_spec  # pyright: ignore[reportPrivateUsage]

if TYPE_CHECKING:
    from collections.abc import Callable


def _ask(prompt: str) -> str:
    return prompt


def pick_count(count: int) -> int:
    """Use a numeric answer."""
    return count


def pick_level(level: Literal[1, 2]) -> int:
    """Use a literal numeric answer."""
    return level


def pick_name(name: str) -> str:
    """Use a text answer."""
    return name


@pytest.mark.parametrize(
    ('target', 'arg_name', 'choices'),
    [
        (pick_count, 'count', ['1', '2']),
        (pick_level, 'level', None),
        (pick_name, 'name', ['a', 'b']),
    ],
)
def test_choices_serialize_as_a_string_enum(
    target: Callable[..., object],
    arg_name: str,
    choices: list[str] | None,
) -> None:
    """Numeric and literal annotations no longer pair a numeric type with string values."""
    agent = Agent(name='asker', api_key='test-key', base_url='http://testserver')
    decorated = depends_on_interrupt(arg_name, _ask, prompt='Pick one', choices=choices)(target)
    _ = agent.add_tool(decorated, name='pick')

    (tool,) = agent.compile_tools()
    (dependency,) = _function_tool_spec(tool)['interrupt_dependencies']

    expected = ['1', '2'] if choices is None else choices
    assert dependency['response_schema'] == {'type': 'string', 'enum': expected}
