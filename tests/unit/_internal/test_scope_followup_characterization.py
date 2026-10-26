"""Characterization tests for the async follow-up question builder."""

from __future__ import annotations

import asyncio
import json
from http import HTTPStatus
from typing import Any, cast

import httpx
from pydantic import AnyUrl

from maivn import Agent, Client, ClientConfig, FollowupQuestion

JsonObject = dict[str, Any]


def _frame(position: int, event_type: str, payload: JsonObject) -> str:
    event = {
        'event_id': f'evt-{position}',
        'ordinal': f'ord-{position}',
        'type': event_type,
        'session_id': 'ses-follow',
        'root_event_id': 'evt-root',
        'payload': payload,
        'ts': '2026-08-03T12:00:00Z',
    }
    return f'id: {position}\nevent: {event_type}\ndata: {json.dumps(event)}\n\n'


_INTERRUPT: JsonObject = {
    'kind': 'agent_followup',
    'checkpoint_id': 'int-1',
    'thread_id': 'thr-follow',
    'session_id': 'ses-follow',
    'header': 'Region',
    'question': 'Which region?',
    'explanation': 'Residency.',
    'options': [{'label': 'EU', 'description': 'Frankfurt.'}],
}
_FINAL: JsonObject = {
    'message': {
        'message_id': 'msg-final',
        'role': 'assistant',
        'content': 'continued',
        'ts': '2026-08-03T12:00:00Z',
    },
    'usage': {},
    'tool_calls': {'count': 0, 'names': []},
}


def test_async_followup_answers_stream_part_interrupts_and_keeps_the_thread_id() -> None:
    """An interrupt nested in a stream part is answered by an async handler."""
    answers: list[JsonObject] = []
    handled: list[tuple[FollowupQuestion, JsonObject]] = []
    sse = (
        _frame(1, 'status_message', {'stream_part': {'data': {'note': 'no interrupt'}}})
        + _frame(2, 'status_message', {'stream_part': 'not-a-mapping'})
        + _frame(3, 'session_interrupt', {'interrupt': {'kind': 'approval', 'checkpoint_id': 'x'}})
        + _frame(4, 'session_interrupt', {'stream_part': {'data': {'interrupt': _INTERRUPT}}})
        + _frame(5, 'final', _FINAL)
    )

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == '/v1/invoke':
            return httpx.Response(
                HTTPStatus.ACCEPTED, json={'session_id': 'ses-follow', 'stream_position': 0}
            )
        if request.url.path == '/v1/threads/thr-follow/interrupts/int-1/responses':
            answers.append(cast('JsonObject', json.loads(request.content)))
            return httpx.Response(
                HTTPStatus.ACCEPTED,
                json={'thread_id': 'thr-follow', 'session_id': 'ses-follow', 'stream_position': 5},
            )
        return httpx.Response(
            HTTPStatus.OK, headers={'content-type': 'text/event-stream'}, content=sse
        )

    client = Client(
        config=ClientConfig(api_key='test-key', base_url=AnyUrl('http://testserver')),
        transport=httpx.MockTransport(handler),
        private_data_store=None,
    )
    agent = Agent(name='asker', client=client)

    async def answer(question: FollowupQuestion, schema: JsonObject) -> str:
        handled.append((question, schema))
        return 'EU'

    response = asyncio.run(
        agent.allow_followup_questions(input_handler=answer).ainvoke(
            'hello', thread_id='thr-direct'
        ),
    )

    assert response.response == 'continued'
    assert response.thread_id == 'thr-direct'
    assert [(question.question, question.header, schema) for question, schema in handled] == [
        ('Which region?', 'Region', {}),
    ]
    assert handled[0][0].required is True
    assert handled[0][0].multi_select is False
    assert answers == [
        {
            'answer': {'kind': 'free_text', 'value': 'EU'},
            'responded_by': 'sdk-user',
            'surface': 'sdk',
        },
    ]
