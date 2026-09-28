"""Inline follow-up question policy layered over a scope's invocation surface."""

from __future__ import annotations

import inspect
from collections.abc import Mapping
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, cast

from maivn._internal.api.async_stream import run_blocking
from maivn._internal.compat.interrupts import FollowupQuestion
from maivn._internal.compat.invocation import (
    coerce_structured_response,
    response_from_stream_events,
)
from maivn._internal.models import RunOptions
from maivn._internal.scope.options import response_with_thread_id

if TYPE_CHECKING:
    from collections.abc import AsyncIterator, Awaitable, Callable, Iterator

    from pydantic import BaseModel

    from maivn._internal.client import Client
    from maivn._internal.compat.interrupts import FollowupQuestionsConfig
    from maivn._internal.models import InvokeResponse, JsonObject, StreamEvent
    from maivn._internal.scope.agent import Agent
    from maivn._internal.scope.swarm import Swarm
    from maivn.messages import SdkMessagesInput


@dataclass(frozen=True, slots=True)
class FollowupInvocationBuilder:
    """Chainable proxy that adds follow-up policy without narrowing scope methods."""

    _scope: Agent | Swarm
    _config: FollowupQuestionsConfig

    def invoke(self, messages: SdkMessagesInput, **kwargs: Any) -> InvokeResponse:
        """Invoke, resolving typed follow-up checkpoints through the local handler."""
        events = self.stream(messages, **kwargs)
        response = response_from_stream_events(events)
        structured_output = kwargs.get('structured_output')
        model = structured_output if isinstance(structured_output, type) else None
        return coerce_structured_response(
            response_with_thread_id(response, self._thread_id(kwargs)),
            cast('type[BaseModel] | None', model),
        )

    async def ainvoke(self, messages: SdkMessagesInput, **kwargs: Any) -> InvokeResponse:
        """Forward the complete asynchronous invoke surface with follow-up policy."""
        events = [event async for event in self.astream(messages, **kwargs)]
        response = response_from_stream_events(events)
        structured_output = kwargs.get('structured_output')
        model = structured_output if isinstance(structured_output, type) else None
        return coerce_structured_response(
            response_with_thread_id(response, self._thread_id(kwargs)),
            cast('type[BaseModel] | None', model),
        )

    def stream(self, messages: SdkMessagesInput, **kwargs: Any) -> Iterator[StreamEvent]:
        """Forward the complete synchronous stream surface with follow-up policy."""
        stream = cast('Callable[..., Iterator[StreamEvent]]', self._scope.stream)
        events = stream(messages, **self._kwargs(kwargs))
        return self._resolve_stream(events)

    def astream(self, messages: SdkMessagesInput, **kwargs: Any) -> AsyncIterator[StreamEvent]:
        """Forward the complete asynchronous stream surface with follow-up policy."""
        stream = cast('Callable[..., AsyncIterator[StreamEvent]]', self._scope.astream)
        return self._resolve_astream(stream(messages, **self._kwargs(kwargs)))

    def _kwargs(self, kwargs: Mapping[str, Any]) -> dict[str, Any]:
        forwarded = dict(kwargs)
        raw_options = forwarded.get('options')
        options = raw_options if isinstance(raw_options, RunOptions) else RunOptions()
        forwarded['options'] = options.model_copy(
            update={'followup_questions': self._config.wire_payload()},
        )
        return forwarded

    def _thread_id(self, kwargs: Mapping[str, Any]) -> str | None:
        direct = kwargs.get('thread_id')
        if isinstance(direct, str):
            return direct
        options = kwargs.get('options')
        return options.thread_id if isinstance(options, RunOptions) else None

    def _resolve_stream(self, events: Iterator[StreamEvent]) -> Iterator[StreamEvent]:
        for event in events:
            yield event
            followup = _followup_from_event(event)
            if followup is None:
                continue
            question, schema = followup
            try:
                answer = self._config.input_handler(question, schema)
                if inspect.isawaitable(answer):
                    awaited_answer = cast('Awaitable[object]', answer)
                    answer = run_blocking(lambda awaited=awaited_answer: _await_value(awaited))
            except EOFError:
                # No input is not an answer. Keep consuming the server-owned
                # checkpoint timeout or cancellation instead of abandoning the run.
                continue
            self._client().submit_interrupt_response(
                question.thread_id,
                question.checkpoint_id,
                answer=answer,
                responded_by='sdk-user',
            )

    async def _resolve_astream(
        self,
        events: AsyncIterator[StreamEvent],
    ) -> AsyncIterator[StreamEvent]:
        async for event in events:
            yield event
            followup = _followup_from_event(event)
            if followup is None:
                continue
            question, schema = followup
            try:
                answer = self._config.input_handler(question, schema)
                if inspect.isawaitable(answer):
                    answer = await cast('Awaitable[object]', answer)
            except EOFError:
                # Match the synchronous path: an unavailable handler leaves the
                # checkpoint pending until the server resolves its outcome.
                continue
            await self._client().asubmit_interrupt_response(
                question.thread_id,
                question.checkpoint_id,
                answer=answer,
                responded_by='sdk-user',
            )

    def _client(self) -> Client:
        """Return the client owned by the bound scope without widening public API."""
        return cast(
            'Client',
            getattr(self._scope, '_client'),  # noqa: B009 - intentional private proxy seam.
        )


def _followup_from_event(event: StreamEvent) -> tuple[FollowupQuestion, JsonObject] | None:
    """Extract one canonical agent-followup checkpoint from a stream event."""
    candidates: list[object] = [event.data.get('interrupt'), event.payload.get('interrupt')]
    stream_part = event.payload.get('stream_part')
    if isinstance(stream_part, Mapping):
        typed_stream_part = cast('Mapping[str, object]', stream_part)
        part_data = typed_stream_part.get('data')
        if isinstance(part_data, Mapping):
            typed_part_data = cast('Mapping[str, object]', part_data)
            candidates.append(typed_part_data.get('interrupt'))
    raw: Mapping[str, object] | None = None
    for candidate in candidates:
        if isinstance(candidate, Mapping):
            raw = cast('Mapping[str, object]', candidate)
            break
    if raw is None:
        return None
    if raw.get('kind') != 'agent_followup':
        return None
    response_schema = raw.get('response_schema')
    schema: JsonObject = (
        dict(cast('Mapping[str, Any]', response_schema))
        if isinstance(response_schema, Mapping)
        else {}
    )
    question = FollowupQuestion.model_validate(
        {
            'checkpoint_id': raw.get('checkpoint_id'),
            'thread_id': raw.get('thread_id'),
            'session_id': raw.get('session_id'),
            'header': raw.get('header'),
            'question': raw.get('question'),
            'explanation': raw.get('explanation'),
            'options': raw.get('options', ()),
            'multi_select': raw.get('multi_select', False),
            'required': raw.get('required', True),
        },
    )
    return question, schema


async def _await_value(value: Awaitable[object]) -> object:
    """Normalize an awaitable callback result for the synchronous builder."""
    return await value
