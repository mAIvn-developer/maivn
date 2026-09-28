"""New human input rounds supersede old answers without racing sibling calls."""

from __future__ import annotations

import asyncio

import pytest
from maivn_contracts.tools import ErrorToolOutcome, OkToolOutcome

from maivn import default_terminal_interrupt, depends_on_interrupt, depends_on_tool
from maivn._internal.models import StreamEvent, ToolMetadata
from maivn._internal.tool_runtime import LocalToolRuntime


def _event(call_id: str, tool: str, batch: str, *, answer: bool | None = None) -> StreamEvent:
    return StreamEvent(
        position=1,
        event_type='system_tool_start',
        data={
            'payload': {
                'tool_call': {
                    'call_id': call_id,
                    'spec_ref': {'tool_id': tool, 'namespace': 'sdk', 'version': 'v1'},
                    'arguments': {} if answer is None else {'answer': answer},
                    'lineage': {
                        'session_id': 'session',
                        'invocation_id': 'invocation',
                        'message_id': batch,
                    },
                }
            }
        },
    )


@pytest.mark.parametrize('same_batch', [False, True])
def test_latest_human_round_replaces_answer_but_same_batch_first_call_wins(
    *, same_batch: bool
) -> None:
    """Use the new round's answer while preserving first-arrival batch isolation."""

    @depends_on_interrupt(
        arg_name='answer', input_handler=default_terminal_interrupt, prompt='Proceed?'
    )
    def collect(*, answer: bool) -> bool:
        return answer

    @depends_on_tool('collect', 'approved')
    def finish(*, approved: bool) -> bool:
        return approved

    runtime = LocalToolRuntime(
        [
            ToolMetadata(name='collect', target=collect),
            ToolMetadata(name='finish', target=finish),
        ],
        private_data=None,
    )

    async def scenario() -> None:
        first = await runtime.outcome_for_event(_event('first', 'collect', 'round-1', answer=False))
        assert isinstance(first, OkToolOutcome)
        second = await runtime.outcome_for_event(
            _event(
                'second',
                'collect',
                'round-1' if same_batch else 'round-2',
                answer=True,
            )
        )
        assert isinstance(second, OkToolOutcome)
        result = await runtime.outcome_for_event(_event('final', 'finish', 'round-3'))
        assert isinstance(result, OkToolOutcome)
        assert result.result is (not same_batch)

    asyncio.run(scenario())


def test_failed_new_input_round_cannot_reuse_an_older_answer() -> None:
    """A failed collection must block consumers instead of returning stale input."""

    @depends_on_interrupt(arg_name='answer', input_handler=default_terminal_interrupt)
    def collect(*, answer: bool) -> bool:
        if answer:
            message = 'Input rejected'
            raise ValueError(message)
        return answer

    @depends_on_tool('collect', 'approved')
    def finish(*, approved: bool) -> bool:
        return approved

    runtime = LocalToolRuntime(
        [
            ToolMetadata(name='collect', target=collect),
            ToolMetadata(name='finish', target=finish),
        ],
        private_data=None,
    )

    async def scenario() -> None:
        first = await runtime.outcome_for_event(_event('first', 'collect', 'round-1', answer=False))
        assert isinstance(first, OkToolOutcome)
        failed = await runtime.outcome_for_event(
            _event('second', 'collect', 'round-2', answer=True)
        )
        assert isinstance(failed, ErrorToolOutcome)
        result = await runtime.outcome_for_event(_event('final', 'finish', 'round-3'))
        assert isinstance(result, ErrorToolOutcome)

    asyncio.run(scenario())
