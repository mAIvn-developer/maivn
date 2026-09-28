"""Stable dependency ordering for SDK tool compilation."""

from __future__ import annotations

from maivn import Agent, depends_on_tool


def test_compile_tools_moves_a_registered_producer_before_its_consumer() -> None:
    """A declared producer cannot remain after the consumer the scheduler receives."""
    agent = Agent(name='ordering-agent', api_key='test-key', base_url='http://testserver')

    def producer() -> dict[str, int]:
        return {'value': 1}

    @agent.toolify()
    @depends_on_tool(producer, arg_name='produced')
    def consumer(produced: object) -> object:
        return produced

    _ = consumer
    agent.add_tool(producer)

    assert [tool.name for tool in agent.compile_tools()] == ['producer', 'consumer']


def test_compile_tools_preserves_an_already_legal_registration_order() -> None:
    """A producer-first graph keeps the developer's registration order exactly."""
    agent = Agent(name='stable-agent', api_key='test-key', base_url='http://testserver')

    @agent.toolify()
    def producer() -> dict[str, int]:
        return {'value': 1}

    @agent.toolify()
    def independent() -> str:
        return 'unchanged'

    @agent.toolify()
    @depends_on_tool(producer, arg_name='produced')
    def consumer(produced: object) -> object:
        return produced

    _ = (independent, consumer)

    assert [tool.name for tool in agent.compile_tools()] == [
        'producer',
        'independent',
        'consumer',
    ]
