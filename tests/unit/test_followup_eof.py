"""Unavailable input must leave follow-ups pending for the server's terminal outcome."""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING

import httpx
import pytest
from pydantic import AnyUrl

from maivn import (
    Agent,
    Client,
    ClientConfig,
    FollowupQuestion,
    StreamEvent,
    default_terminal_followup,
)

if TYPE_CHECKING:
    from collections.abc import AsyncIterator, Iterator


def _events() -> list[StreamEvent]:
    return [
        StreamEvent(
            position=1,
            event_type='interrupt_required',
            data={
                'interrupt': {
                    'kind': 'agent_followup',
                    'checkpoint_id': 'checkpoint',
                    'thread_id': 'thread',
                    'session_id': 'session',
                    'question': 'Release decision?',
                    'response_schema': {'type': 'string'},
                }
            },
        ),
        StreamEvent(
            position=2,
            event_type='error',
            data={
                'error_code': 'followup_question_timed_out',
            },
        ),
    ]


@pytest.mark.parametrize('async_stream', [False, True])
@pytest.mark.parametrize('async_handler', [False, True])
@pytest.mark.parametrize('input_error', [EOFError, ValueError, KeyboardInterrupt])
def test_terminal_eof_preserves_server_timeout(
    monkeypatch: pytest.MonkeyPatch,
    *,
    async_stream: bool,
    async_handler: bool,
    input_error: type[Exception | KeyboardInterrupt],
) -> None:
    """EOF neither submits a fabricated answer nor closes before the timeout event."""

    def eof(_prompt: str) -> str:
        raise input_error

    monkeypatch.setattr('builtins.input', eof)

    async def handler(question: FollowupQuestion, schema: dict[str, object]) -> object:
        return default_terminal_followup(question, schema)

    requests: list[httpx.Request] = []

    def transport(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(500)

    client = Client(
        config=ClientConfig(api_key='test-key', base_url=AnyUrl('http://testserver')),
        transport=httpx.MockTransport(transport),
    )
    builder = Agent(name='test', client=client).allow_followup_questions(
        input_handler=handler if async_handler else default_terminal_followup,
        timeout=1,
    )
    events = _events()

    def source(_self: Agent, _messages: object, **_kwargs: object) -> Iterator[StreamEvent]:
        yield from events

    async def async_source(
        _self: Agent,
        _messages: object,
        **_kwargs: object,
    ) -> AsyncIterator[StreamEvent]:
        for event in events:
            yield event

    monkeypatch.setattr(Agent, 'stream', source)
    monkeypatch.setattr(Agent, 'astream', async_source)

    async def consume() -> list[StreamEvent]:
        return [event async for event in builder.astream('Ask for a release decision.')]

    def receive() -> list[StreamEvent]:
        return (
            asyncio.run(consume())
            if async_stream
            else list(builder.stream('Ask for a release decision.'))
        )

    if input_error is EOFError:
        assert receive() == events
    else:
        with pytest.raises(input_error):
            receive()
    assert requests == []
