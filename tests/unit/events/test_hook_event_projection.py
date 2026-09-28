# The demo tools under test are registered by decorator and never called by name
# (reportUnusedFunction), and the client stream pump this file drives is the seam
# it covers (reportPrivateUsage).
# pyright: reportPrivateUsage=false, reportUnusedFunction=false
"""Developer hook firings reach trace consumers as ``hook_fired`` AppEvents.

The tool-execution-hooks demo registers four named callbacks - ``swarm_hook``,
``agent_hook``, ``tool_before_hook`` and ``tool_after_hook`` - and its whole
point is that each firing is visible in the run. These tests pin the three
seams that carry a firing from the local tool runtime to a reporter: the
runtime reports it, the client projects it onto the event stream, and
normalization turns the raw event into an ``AppEvent`` the forwarding
dispatcher already knows how to route.
"""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING, Any, cast

from maivn_contracts.tools import OkToolOutcome

from maivn import Agent, Swarm
from maivn._internal.client import _stream_with_local_tools
from maivn._internal.models import StreamEvent
from maivn._internal.tool_runtime import LocalToolRuntime
from maivn.events import forward_normalized_event, normalize_stream_event
from maivn.events._models import RawSSEEvent

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

    from typing_extensions import Self

    from maivn._internal.tool_runtime import HookFiring


# MARK: Demo Fixtures


def _demo_hook(source: str) -> Any:
    """Rebuild the demo's hook factory: one named callable per hook source."""

    def _hook(_payload: dict[str, Any]) -> None:
        return None

    _hook.__name__ = f'{source}_hook'
    return _hook


def _hooked_agent() -> Agent:
    """Build an agent wired exactly like ``tool_execution_hooks_demo``."""
    agent = Agent(name='Ticket Agent', description='hooked', api_key='mvn_test_key')
    agent.before_execute = _demo_hook('agent')
    agent.after_execute = _demo_hook('agent')

    @agent.toolify(
        name='extract_ticket',
        description='Extract key fields from a raw support ticket.',
        before_execute=_demo_hook('tool_before'),
        after_execute=_demo_hook('tool_after'),
    )
    def extract_ticket(ticket: str) -> dict[str, str]:
        return {'summary': ticket}

    swarm = Swarm(name='Hook Swarm', description='hooked', agents=[agent])
    swarm.before_execute = _demo_hook('swarm')
    swarm.after_execute = _demo_hook('swarm')
    return agent


def _tool_start_event(tool_name: str, arguments: dict[str, object]) -> StreamEvent:
    return StreamEvent(
        position=1,
        event_type='system_tool_start',
        data={
            'event_id': f'evt-{tool_name}',
            'session_id': 'ses-hooks',
            'payload': {
                'tool_name': tool_name,
                'tool_call': {
                    'call_id': f'call-{tool_name}',
                    'spec_ref': {
                        'tool_id': tool_name,
                        'namespace': 'sdk',
                        'version': 'v1',
                    },
                    'arguments': dict(arguments),
                    'lineage': {
                        'session_id': 'ses-hooks',
                        'invocation_id': 'inv-hooks',
                    },
                },
            },
        },
    )


# MARK: Runtime Reporting


def test_local_runtime_reports_every_hook_firing_by_name() -> None:
    """Each registered callback reports its own firing, in v1's nesting order."""
    agent = _hooked_agent()
    runtime = LocalToolRuntime(
        agent.compile_tools(),
        private_data=None,
        canonical_interrupts=False,
    )
    firings: list[HookFiring] = []

    async def run() -> None:
        outcome = await runtime.outcome_for_event(
            _tool_start_event('extract_ticket', {'ticket': 'printer on fire'}),
            on_hook=firings.append,
        )
        assert isinstance(outcome, OkToolOutcome)

    asyncio.run(run())

    assert [(firing.name, firing.stage, firing.source) for firing in firings] == [
        ('swarm_hook', 'before', 'swarm'),
        ('agent_hook', 'before', 'scope'),
        ('tool_before_hook', 'before', 'tool'),
        ('tool_after_hook', 'after', 'tool'),
        ('agent_hook', 'after', 'scope'),
        ('swarm_hook', 'after', 'swarm'),
    ]
    assert {firing.status for firing in firings} == {'completed'}
    assert {firing.target_name for firing in firings} == {
        'Hook Swarm',
        'Ticket Agent',
        'extract_ticket',
    }
    assert all(firing.elapsed_ms >= 0 for firing in firings)


def test_a_raising_hook_reports_failed_without_failing_the_tool() -> None:
    """A hook bug is reported as a failed firing, never as a failed tool."""
    agent = Agent(name='Fragile Agent', description='hooked', api_key='mvn_test_key')

    def explode(_payload: dict[str, Any]) -> None:
        message = 'observability bug'
        raise RuntimeError(message)

    agent.before_execute = explode

    @agent.toolify(name='noop_tool', description='No-op.')
    def noop_tool(ticket: str) -> dict[str, str]:
        return {'ok': ticket}

    runtime = LocalToolRuntime(
        agent.compile_tools(),
        private_data=None,
        canonical_interrupts=False,
    )
    firings: list[HookFiring] = []

    async def run() -> None:
        outcome = await runtime.outcome_for_event(
            _tool_start_event('noop_tool', {'ticket': 'fine'}),
            on_hook=firings.append,
        )
        assert isinstance(outcome, OkToolOutcome)

    asyncio.run(run())

    failed = [firing for firing in firings if firing.status == 'failed']
    assert [(firing.name, firing.stage) for firing in failed] == [('explode', 'before')]
    assert failed[0].error == 'RuntimeError'


# MARK: Client Stream Projection


class _StubToolHttp:
    """Minimal stand-in for the batch-result HTTP client the stream pump owns."""

    def __init__(self) -> None:
        self.posts: list[tuple[str, dict[str, object]]] = []

    async def __aenter__(self) -> Self:
        return self

    async def __aexit__(self, *_exc: object) -> None:
        return None

    async def post(self, path: str, body: dict[str, object]) -> dict[str, object]:
        self.posts.append((path, body))
        return {}


def test_client_projects_hook_firings_onto_the_event_stream() -> None:
    """A local tool's hook firings appear as ``hook_fired`` events in the stream."""
    agent = _hooked_agent()
    runtime = LocalToolRuntime(
        agent.compile_tools(),
        private_data=None,
        canonical_interrupts=False,
    )
    start = _tool_start_event('extract_ticket', {'ticket': 'printer on fire'})

    async def source() -> AsyncIterator[StreamEvent]:
        yield start

    async def collect() -> list[StreamEvent]:
        return [
            event
            async for event in _stream_with_local_tools(
                events=source(),
                tool_runtime=runtime,
                tool_http=cast('Any', _StubToolHttp()),
                session_id='ses-hooks',
            )
        ]

    events = asyncio.run(collect())

    hook_events = [event for event in events if event.event_type == 'hook_fired']
    assert [str(event.payload.get('name')) for event in hook_events] == [
        'swarm_hook',
        'agent_hook',
        'tool_before_hook',
        'tool_after_hook',
        'agent_hook',
        'swarm_hook',
    ]

    # A tool firing has to name the same card the tool event creates, or the
    # UI attaches it to a card that does not exist. Read the card id back out
    # of the normalized tool event rather than restating the precedence here.
    tool_event = normalize_stream_event(cast('Any', start))[0]
    assert tool_event.tool is not None
    card_id = tool_event.tool.id
    assert [
        event.payload.get('target_id')
        for event in hook_events
        if event.payload.get('target_type') == 'tool'
    ] == [card_id, card_id]
    assert {
        event.payload.get('target_id')
        for event in hook_events
        if event.payload.get('target_type') != 'tool'
    } == {'Hook Swarm', 'Ticket Agent'}


# MARK: Stream Normalization


def _raw_hook_event() -> RawSSEEvent:
    return RawSSEEvent(
        name='hook_fired',
        payload={
            'name': 'tool_before_hook',
            'stage': 'before',
            'status': 'completed',
            'target_type': 'tool',
            'target_id': 'evt-extract_ticket',
            'target_name': 'extract_ticket',
            'source': 'tool',
            'error': None,
            'elapsed_ms': 0,
        },
    )


def test_normalization_keeps_hook_events_instead_of_dropping_them() -> None:
    """``hook_fired`` survives normalization with its hook descriptor populated."""
    normalized = normalize_stream_event(_raw_hook_event())

    assert len(normalized) == 1
    event = normalized[0]
    assert event.event_name == 'hook_fired'
    assert event.event_kind == 'hook'
    assert event.hook is not None
    assert event.hook.name == 'tool_before_hook'
    assert event.hook.stage == 'before'
    assert event.hook.status == 'completed'
    assert event.hook.target_type == 'tool'
    assert event.hook.target_id == 'evt-extract_ticket'
    assert event.hook.target_name == 'extract_ticket'
    assert event.hook.source == 'tool'


class _RecordingReporter:
    """Reporter that records the hook firings forwarded to it."""

    def __init__(self) -> None:
        self.hooks: list[dict[str, object]] = []

    def report_hook_fired(self, **fields: object) -> None:
        self.hooks.append(dict(fields))


def test_normalized_hook_event_reaches_the_reporter() -> None:
    """The forwarding dispatcher hands a normalized firing to the reporter."""
    reporter = _RecordingReporter()
    normalized = normalize_stream_event(_raw_hook_event())
    assert normalized, 'hook event must survive normalization before it can forward'

    asyncio.run(forward_normalized_event(normalized[0], reporter=reporter))

    assert [hook['name'] for hook in reporter.hooks] == ['tool_before_hook']
    assert reporter.hooks[0]['stage'] == 'before'
    assert reporter.hooks[0]['status'] == 'completed'
