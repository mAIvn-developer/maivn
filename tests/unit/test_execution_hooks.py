# The tools under test are registered by decorator and never called by name (reportUnusedFunction),
# and the private event builder they assert against is the seam this file covers
# (reportPrivateUsage).
# pyright: reportPrivateUsage=false, reportUnusedFunction=false
"""Execution hooks fire at tool, agent, and swarm level around local tools."""

from __future__ import annotations

import asyncio
from typing import Any

from maivn_contracts.tools import OkToolOutcome

from maivn import Agent, Swarm
from maivn._internal.tool_runtime import LocalToolRuntime
from tests.unit._internal.test_tool_runtime import (
    _tool_start_event,
)


def _make_hook(source: str, log: list[str]) -> Any:
    def _hook(payload: dict[str, Any]) -> None:
        log.append(f'{source}:{payload["stage"]}:{payload["tool_name"]}')

    return _hook


def test_swarm_agent_and_tool_hooks_fire_in_v1_order() -> None:
    """Before runs outside-in (swarm, agent, tool); after runs inside-out."""
    log: list[str] = []
    agent = Agent(name='Hooked Agent', description='hooked', api_key='mvn_test_key')
    agent.before_execute = _make_hook('agent', log)
    agent.after_execute = _make_hook('agent', log)

    @agent.toolify(
        name='extract_ticket',
        description='Extract fields.',
        before_execute=_make_hook('tool_before', log),
        after_execute=_make_hook('tool_after', log),
    )
    def extract_ticket(ticket: str) -> dict[str, str]:
        return {'summary': ticket}

    swarm = Swarm(name='Hooked Swarm', description='hooked', agents=[agent])
    swarm.before_execute = _make_hook('swarm', log)
    swarm.after_execute = _make_hook('swarm', log)

    tools = agent.compile_tools()
    assert tools, 'agent must compile its tool'
    runtime = LocalToolRuntime(tools, private_data=None, canonical_interrupts=False)

    async def run() -> None:
        outcome = await runtime.outcome_for_event(
            _tool_start_event('extract_ticket', {'ticket': 'printer on fire'})
        )
        assert isinstance(outcome, OkToolOutcome)

    asyncio.run(run())

    assert log == [
        'swarm:before:extract_ticket',
        'agent:before:extract_ticket',
        'tool_before:before:extract_ticket',
        'tool_after:after:extract_ticket',
        'agent:after:extract_ticket',
        'swarm:after:extract_ticket',
    ]


def test_a_raising_hook_never_fails_the_tool() -> None:
    """Hooks observe; a hook bug must not turn into a tool failure."""
    agent = Agent(name='Fragile Hook Agent', description='hooked', api_key='mvn_test_key')

    def _explode(_payload: dict[str, Any]) -> None:
        message = 'observability bug'
        raise RuntimeError(message)

    agent.before_execute = _explode

    @agent.toolify(name='noop_tool', description='No-op.')
    def noop_tool(ticket: str) -> dict[str, str]:
        return {'ok': ticket}

    tools = agent.compile_tools()
    runtime = LocalToolRuntime(tools, private_data=None, canonical_interrupts=False)

    async def run() -> None:
        outcome = await runtime.outcome_for_event(
            _tool_start_event('noop_tool', {'ticket': 'fine'})
        )
        assert isinstance(outcome, OkToolOutcome)

    asyncio.run(run())
