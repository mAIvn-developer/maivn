"""Stateful file toolsets preserve source order despite parallel model tool calls."""

from __future__ import annotations

import asyncio

from maivn import Agent, toolify, toolset
from maivn._internal.models import StreamEvent
from maivn._internal.tool_runtime import LocalToolRuntime


@toolset(prefix='document', metadata={'serialize_calls': True})
class _Document:
    def __init__(self) -> None:
        self.blocks: list[str] = []
        self.started = asyncio.Event()
        self.release = asyncio.Event()

    @toolify()
    async def append(self, text: str) -> str:
        if text == 'title':
            self.started.set()
            await self.release.wait()
        self.blocks.append(text)
        return text


def _event(call_id: str, text: str, *, tool_name: str = 'DOCUMENT_append') -> StreamEvent:
    return StreamEvent(
        position=1,
        event_type='system_tool_start',
        data={
            'payload': {
                'tool_call': {
                    'call_id': call_id,
                    'spec_ref': {'namespace': 'sdk', 'tool_id': tool_name, 'version': 'v1'},
                    'arguments': {'text': text},
                    'lineage': {'session_id': 'session-order', 'invocation_id': 'invocation-order'},
                }
            }
        },
    )


def test_parallel_document_appends_preserve_model_call_order() -> None:
    """A slow title append must not move behind later paragraphs in the generated file."""

    async def scenario() -> None:
        document = _Document()
        agent = Agent(name='ordering', api_key='test', base_url='http://test')
        agent.add_toolset(document)
        runtime = LocalToolRuntime(agent.compile_tools(), private_data=None)
        first = asyncio.create_task(runtime.outcome_for_event(_event('one', 'title')))
        await asyncio.wait_for(document.started.wait(), timeout=2)
        second = asyncio.create_task(runtime.outcome_for_event(_event('two', 'body')))
        await asyncio.sleep(0)
        document.release.set()
        await asyncio.gather(first, second)
        assert document.blocks == ['title', 'body']

    asyncio.run(scenario())


def test_independent_document_instances_can_still_run_concurrently() -> None:
    """Ordering one workspace must not stall an unrelated document toolset."""

    async def scenario() -> None:
        first_document, second_document = _Document(), _Document()
        agent = Agent(name='ordering', api_key='test', base_url='http://test')
        agent.add_tool(
            first_document.append, name='LEFT_append', metadata={'serialize_calls': True}
        )
        agent.add_tool(
            second_document.append, name='RIGHT_append', metadata={'serialize_calls': True}
        )
        runtime = LocalToolRuntime(agent.compile_tools(), private_data=None)
        calls = [
            asyncio.create_task(
                runtime.outcome_for_event(_event('left', 'title', tool_name='LEFT_append'))
            ),
            asyncio.create_task(
                runtime.outcome_for_event(_event('right', 'title', tool_name='RIGHT_append'))
            ),
        ]
        try:
            await asyncio.wait_for(
                asyncio.gather(first_document.started.wait(), second_document.started.wait()),
                timeout=2,
            )
        finally:
            first_document.release.set()
            second_document.release.set()
            await asyncio.gather(*calls)
        assert first_document.blocks == second_document.blocks == ['title']

    asyncio.run(scenario())


def test_cancelled_mutation_finishes_before_next_source_edit() -> None:
    """Cancellation must not release the lane while an in-flight writer can still mutate."""

    async def scenario() -> None:
        document = _Document()
        agent = Agent(name='ordering', api_key='test', base_url='http://test')
        agent.add_toolset(document)
        runtime = LocalToolRuntime(agent.compile_tools(), private_data=None)
        first = asyncio.create_task(runtime.outcome_for_event(_event('one', 'title')))
        await asyncio.wait_for(document.started.wait(), timeout=2)
        first.cancel()
        second = asyncio.create_task(runtime.outcome_for_event(_event('two', 'body')))
        await asyncio.sleep(0)
        document.release.set()
        results = await asyncio.gather(first, second, return_exceptions=True)
        assert isinstance(results[0], asyncio.CancelledError)
        assert document.blocks == ['title', 'body']

    asyncio.run(scenario())
