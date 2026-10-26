"""An interrupt argument typed with a class pydantic cannot describe still compiles (U05-D14)."""

from __future__ import annotations

from maivn import Agent, depends_on_interrupt
from maivn._internal.wire import _function_tool_spec  # pyright: ignore[reportPrivateUsage]


class Coordinates:
    """A plain class with no pydantic schema."""

    def __init__(self, raw: str) -> None:
        """Keep the raw answer."""
        self.raw = raw


def _ask(prompt: str) -> str:
    return prompt


def place(where: Coordinates) -> str:
    """Use the human's answer."""
    return where.raw


def test_a_custom_class_annotation_falls_back_to_the_declared_input_schema() -> None:
    """The tool spec builds, with the text schema the interrupt declares."""
    agent = Agent(name='asker', api_key='test-key', base_url='http://testserver')
    _ = agent.add_tool(depends_on_interrupt('where', _ask, prompt='Where?')(place), name='place')

    (tool,) = agent.compile_tools()
    spec = _function_tool_spec(tool)

    assert spec['interrupt_dependencies'] == [
        {
            'arg_name': 'where',
            'prompt_source': 'authored',
            'question': 'Where?',
            'response_schema': {'type': 'string'},
        },
    ]
