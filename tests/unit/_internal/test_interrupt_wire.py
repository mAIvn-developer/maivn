"""Interrupt wire regression contracts."""

from __future__ import annotations

from maivn import Agent, depends_on_interrupt
from maivn._internal.wire import (
    _function_tool_spec,  # pyright: ignore[reportPrivateUsage] - private wire helper under test.
)


def test_explicit_interrupt_input_type_overrides_broad_python_annotation() -> None:
    """A UI-specific declaration must survive even when the runtime value is a string."""
    agent = Agent(name='confirmation-agent', api_key='test-only-key')

    @agent.toolify(name='confirm_action')
    @depends_on_interrupt(
        arg_name='answer',
        input_handler=lambda _prompt: 'yes',
        prompt='Proceed? (yes/no)',
        input_type='boolean',
    )
    def confirm_action(answer: str) -> bool:
        return answer == 'yes'

    _ = confirm_action
    spec = _function_tool_spec(agent.compile_tools()[0])

    assert spec['interrupt_dependencies'] == [
        {
            'arg_name': 'answer',
            'prompt_source': 'authored',
            'question': 'Proceed? (yes/no)',
            'response_schema': {'type': 'boolean'},
        }
    ]
