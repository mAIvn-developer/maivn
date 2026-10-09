"""A lazy immutable decision plan bound to exactly one invocation scope."""

from __future__ import annotations

import asyncio
import inspect
import re
from dataclasses import dataclass, field, replace
from types import MappingProxyType
from typing import TYPE_CHECKING, cast

from maivn._internal.api.async_stream import run_blocking, stream_async_iterator
from maivn._internal.compat.invocation import (
    EventInvocationBuilder,
    StructuredOutputInvocationBuilder,
)
from maivn._internal.decisions.flow_execution import DecisionStage, drain, execute, stream_execute
from maivn._internal.decisions.models import DecisionRequest

if TYPE_CHECKING:
    from collections.abc import AsyncGenerator, Callable, Iterable, Iterator, Mapping

    from pydantic import BaseModel

    from maivn._internal.decisions.flow_models import (
        DecisionContext,
        DecisionInvocationResult,
        DecisionQuestion,
    )
    from maivn._internal.decisions.runtime import DecisionClient
    from maivn._internal.models import StreamEvent


_MAX_QUESTIONS = 32
_NAME = re.compile(r'[A-Za-z][A-Za-z0-9_.-]{0,127}\Z')


def _limit(value: int) -> None:
    if type(value) is not int or value < 1:
        message = 'max_concurrency must be a positive integer'
        raise ValueError(message)


def _name(value: object) -> None:
    if type(value) is not str or _NAME.fullmatch(value) is None:
        message = 'Decision stage and question names must be valid identifiers'
        raise ValueError(message)


@dataclass(frozen=True, slots=True)
class DecisionInvocationBuilder:
    """Define ordered local stages without triggering any platform decision or invocation."""

    _scope: object = field(repr=False)
    _client: DecisionClient = field(repr=False)
    _stages: tuple[DecisionStage, ...] = field(default=(), repr=False, kw_only=True)
    _mapper: Callable[[DecisionContext], object] | None = field(
        default=None, repr=False, kw_only=True
    )
    _output_model: type[BaseModel] | None = field(default=None, repr=False, kw_only=True)

    def decide(  # noqa: C901, PLR0913 - settled stage declaration contract.
        self,
        name: str,
        *,
        client: DecisionClient | None = None,
        questions: Mapping[str, DecisionQuestion],
        at: str = 'before',
        require: Mapping[str, str] | None = None,
        max_concurrency: int = 4,
        native_batch: bool = False,
    ) -> DecisionInvocationBuilder:
        """Add one validated stage; factory requests resolve locally at execution."""
        _name(name)
        _limit(max_concurrency)
        if type(native_batch) is not bool:
            message = 'Decision native_batch must be boolean'
            raise ValueError(message)
        if any(stage.name == name for stage in self._stages):
            message = 'Decision stage names must be unique'
            raise ValueError(message)
        if at not in {'before', 'after'}:
            message = 'Decision placement must be before or after'
            raise ValueError(message)
        if not 1 <= len(questions) <= _MAX_QUESTIONS:
            message = 'Decision stages require between one and 32 questions'
            raise ValueError(message)
        gates = dict(require) if require is not None else {}
        copied: dict[str, DecisionQuestion] = {}
        for key, question in questions.items():
            _name(key)
            if isinstance(question, DecisionRequest):
                copied[key] = DecisionRequest.model_validate(question)
            elif callable(question) and not inspect.iscoroutinefunction(question):
                copied[key] = question
            else:
                message = 'Decision questions must be requests or synchronous factories'
                raise TypeError(message)
        if at == 'after' and gates:
            message = 'After decision stages cannot gate completed output'
            raise ValueError(message)
        for key, value in gates.items():
            if key not in copied or type(value) is not str:
                message = 'Required decisions must name existing questions and string values'
                raise ValueError(message)
            question = copied[key]
            if isinstance(question, DecisionRequest) and value not in {
                choice.key for choice in question.choices
            }:
                message = 'Required decision value is not a declared choice'
                raise ValueError(message)
        bound = client if client is not None else self._client
        stage = DecisionStage(
            name,
            bound,
            MappingProxyType(copied),
            at,
            MappingProxyType(gates),
            max_concurrency,
            native_batch,
        )
        return replace(self, _client=bound, _stages=(*self._stages, stage))

    def map_input(self, mapper: Callable[[DecisionContext], object]) -> DecisionInvocationBuilder:
        """Explicitly map completed before decisions to invocation messages."""
        if not callable(mapper) or inspect.iscoroutinefunction(mapper):
            message = 'Input mapping must be callable'
            raise TypeError(message)
        return replace(self, _mapper=mapper)

    def invoke(self, messages: object, **invoke_kwargs: object) -> DecisionInvocationResult:
        """Execute the plan synchronously outside an async loop."""
        return run_blocking(lambda: self.ainvoke(messages, **invoke_kwargs))

    async def ainvoke(self, messages: object, **invoke_kwargs: object) -> DecisionInvocationResult:
        """Execute before gates, one scope invocation, and after annotations."""
        limit = max((stage.max_concurrency for stage in self._stages), default=1)
        return await execute(
            self._scope,
            self._stages,
            self._mapper,
            self._output_model,
            messages,
            invoke_kwargs,
            asyncio.Semaphore(limit),
        )

    def batch(
        self, inputs: Iterable[object], *, max_concurrency: int = 4, **invoke_kwargs: object
    ) -> list[DecisionInvocationResult]:
        """Preserve input order with a shared bound on decision and invocation work."""
        return run_blocking(
            lambda: self.abatch(inputs, max_concurrency=max_concurrency, **invoke_kwargs)
        )

    async def abatch(
        self, inputs: Iterable[object], *, max_concurrency: int = 4, **invoke_kwargs: object
    ) -> list[DecisionInvocationResult]:
        """Cancel and drain all items when any item fails or the caller cancels."""
        _limit(max_concurrency)
        input_items = list(inputs)
        operations = asyncio.Semaphore(max_concurrency)
        tasks = [
            asyncio.create_task(
                execute(
                    self._scope,
                    self._stages,
                    self._mapper,
                    self._output_model,
                    item,
                    dict(invoke_kwargs),
                    operations,
                )
            )
            for item in input_items
        ]
        try:
            return await asyncio.gather(*tasks)
        except BaseException:
            await drain(cast('list[asyncio.Task[object]]', tasks))
            raise

    def stream(self, messages: object, **invoke_kwargs: object) -> Iterator[StreamEvent]:
        """Bridge live async events without buffering the stream."""
        return stream_async_iterator(lambda: self.astream(messages, **invoke_kwargs))

    def astream(
        self, messages: object, **invoke_kwargs: object
    ) -> AsyncGenerator[StreamEvent, None]:
        """Stream content-free stage events and a final typed plan outcome."""
        limit = max((stage.max_concurrency for stage in self._stages), default=1)
        return stream_execute(
            self._scope,
            self._stages,
            self._mapper,
            self._output_model,
            messages,
            invoke_kwargs,
            asyncio.Semaphore(limit),
        )

    def structured_output(self, model: type[BaseModel]) -> DecisionInvocationBuilder:
        """Retain decision stages while validating final structured output."""
        return replace(
            self, _scope=StructuredOutputInvocationBuilder(self._scope, model), _output_model=model
        )

    def events(
        self,
        *,
        include: Iterable[str] | str | None = None,
        exclude: Iterable[str] | str | None = None,
        on_event: Callable[[dict[str, object]], None] | None = None,
        auto_verbose: bool = True,
    ) -> DecisionInvocationBuilder:
        """Retain decision stages while routing underlying invocation events."""
        scope = EventInvocationBuilder(
            self._scope,
            include=include,
            exclude=exclude,
            on_event=on_event,
            auto_verbose=auto_verbose,
        )
        return replace(self, _scope=scope)

    def allow_followup_questions(self, **kwargs: object) -> DecisionInvocationBuilder:
        """Retain stages while configuring the existing follow-up scope wrapper."""
        method = getattr(self._scope, 'allow_followup_questions', None)
        if not callable(method):
            message = 'The bound scope does not support follow-up questions'
            raise TypeError(message)
        return replace(self, _scope=method(**kwargs))
