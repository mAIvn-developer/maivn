# pyright: reportPrivateUsage=false, reportUnusedFunction=false
"""Transport replay is distinct from a model requesting the same procedure again."""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Any, cast

import pytest

from maivn import Agent
from maivn._internal.client import _stream_with_local_tools
from maivn._internal.models import StreamEvent
from maivn._internal.tool_runtime import LocalToolRuntime

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

    from typing_extensions import Self


class _StubToolHttp:
    async def __aenter__(self) -> Self:
        return self

    async def __aexit__(self, *_exc: object) -> None:
        return None

    async def post(self, path: str, body: dict[str, object]) -> dict[str, object]:
        _ = path, body
        return {}


def _start(call_id: str, block: str) -> StreamEvent:
    return StreamEvent(
        position=1,
        event_type='system_tool_start',
        data={
            'payload': {
                'tool_call': {
                    'call_id': call_id,
                    'spec_ref': {'tool_id': 'put_block', 'namespace': 'sdk', 'version': 'v1'},
                    'arguments': {'block': block},
                    'lineage': {'session_id': 'ses-replay', 'invocation_id': 'inv-replay'},
                }
            },
        },
    )


def _run(
    starts: list[StreamEvent], calls: list[str], *, completed_replay: bool = False
) -> list[StreamEvent]:
    agent = Agent(name='Writer', api_key='mvn_test_key')
    first_completed = asyncio.Event()

    @agent.toolify(name='put_block', description='Place one block.')
    async def put_block(block: str) -> str:
        calls.append(block)
        await asyncio.sleep(0)
        first_completed.set()
        return 'placed'

    async def source() -> AsyncIterator[StreamEvent]:
        for event in starts:
            yield event
            if completed_replay:
                await first_completed.wait()

    async def collect() -> list[StreamEvent]:
        return [
            event
            async for event in _stream_with_local_tools(
                events=source(),
                tool_runtime=LocalToolRuntime(agent.compile_tools(), private_data=None),
                tool_http=cast('Any', _StubToolHttp()),
                session_id='ses-replay',
            )
        ]

    return asyncio.run(collect())


@pytest.mark.parametrize('completed_replay', [False, True])
def test_replayed_start_executes_once_but_distinct_call_executes_again(
    *,
    completed_replay: bool,
) -> None:
    """An in-flight or completed duplicate runs once; a new image call still runs."""
    calls: list[str] = []
    title = _start('title-call', 'title')
    events = _run(
        [title, title, _start('image-call', 'image'), title],
        calls,
        completed_replay=completed_replay,
    )
    assert calls == ['title', 'image']
    assert [
        event.payload['outcome']['call_id']
        for event in events
        if event.event_type == 'system_tool_complete'
    ] == ['title-call', 'image-call']


def test_reused_call_identity_with_changed_arguments_fails_closed() -> None:
    """Conflicting transport identities cannot execute a second set of arguments."""
    calls: list[str] = []
    with pytest.raises(ValueError, match='Conflicting SDK tool call identity'):
        _run([_start('same-call', 'title'), _start('same-call', 'conflicting')], calls)
    assert 'conflicting' not in calls


def test_local_completion_records_current_time_instead_of_dispatch_timestamp() -> None:
    """A delayed dispatch must not produce a completion dated before execution."""
    start = _start('timed-call', 'title')
    start.data['ts'] = '2000-01-01T00:00:00+00:00'
    before = datetime.now(timezone.utc)
    events = _run([start], [])
    after = datetime.now(timezone.utc)
    complete = next(event for event in events if event.event_type == 'system_tool_complete')
    recorded = datetime.fromisoformat(str(complete.data['ts']))
    assert before <= recorded <= after
    assert start.data['ts'] == '2000-01-01T00:00:00+00:00'
