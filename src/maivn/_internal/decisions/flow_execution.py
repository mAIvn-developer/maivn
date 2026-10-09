"""Execute ordered stages without exporting invocation context automatically."""

from __future__ import annotations

import asyncio
from collections.abc import Mapping
from copy import deepcopy
from dataclasses import dataclass
from types import MappingProxyType
from typing import TYPE_CHECKING, cast

from pydantic import BaseModel

from maivn._internal.api.async_stream import close_async_iterator
from maivn._internal.compat.invocation import (
    coerce_structured_response,
    response_from_stream_events,
)
from maivn._internal.decisions.flow_models import (
    DecisionContext,
    DecisionInvocationResult,
    DecisionQuestion,
    DecisionStreamEvent,
)
from maivn._internal.decisions.models import DecisionRequest, DecisionResult
from maivn._internal.models import RunOptions
from maivn._internal.scope.options import response_with_thread_id

if TYPE_CHECKING:
    from collections.abc import AsyncGenerator, AsyncIterator, Awaitable, Callable, Iterable

    from maivn._internal.decisions.runtime import DecisionClient
    from maivn._internal.models import InvokeResponse, StreamEvent


@dataclass(frozen=True, slots=True)
class DecisionStage:
    """One validated immutable stage declaration."""

    name: str
    client: DecisionClient
    questions: Mapping[str, DecisionQuestion]
    at: str
    require: Mapping[str, str]
    max_concurrency: int
    native_batch: bool = False


def immutable_results(
    results: Mapping[str, Mapping[str, DecisionResult]],
) -> Mapping[str, Mapping[str, DecisionResult]]:
    """Copy stage records into immutable nested mappings."""
    return MappingProxyType(
        {stage: MappingProxyType(dict(values)) for stage, values in results.items()}
    )


def _freeze(value: object) -> object:
    if isinstance(value, Mapping):
        return MappingProxyType(
            {key: _freeze(item) for key, item in cast('Mapping[object, object]', value).items()}
        )
    if isinstance(value, (list, tuple)):
        return tuple(_freeze(item) for item in cast('Iterable[object]', value))
    if isinstance(value, set):
        return frozenset(_freeze(item) for item in cast('Iterable[object]', value))
    return deepcopy(value)


def context(
    original: object,
    response: InvokeResponse | None,
    results: Mapping[str, Mapping[str, DecisionResult]],
) -> DecisionContext:
    """Build a stable local snapshot for all factories in the current stage."""
    return DecisionContext(_freeze(original), deepcopy(response), immutable_results(results))


async def drain(tasks: list[asyncio.Task[object]]) -> None:
    """Cancel owned tasks and await cleanup even under repeated caller cancellation."""
    for task in tasks:
        task.cancel()
    completion = asyncio.gather(*tasks, return_exceptions=True)
    while not completion.done():
        try:
            await asyncio.shield(completion)
        except asyncio.CancelledError:  # noqa: PERF203 - drain survives repeated cancellation.
            continue
    completion.result()


async def run_stage(
    stage: DecisionStage,
    snapshot: DecisionContext,
    operations: asyncio.Semaphore,
    *,
    group_operations: asyncio.Semaphore | None = None,
) -> Mapping[str, DecisionResult]:
    """Resolve every factory and gate before dispatching any platform decision call."""
    requests: list[tuple[str, DecisionRequest]] = []
    for name, declaration in stage.questions.items():
        isolated = context(snapshot.input, snapshot.output, snapshot.decisions)
        produced = cast('object', declaration(isolated) if callable(declaration) else declaration)
        if not isinstance(produced, DecisionRequest):
            message = 'Decision factories must return a DecisionRequest'
            raise TypeError(message)
        request = DecisionRequest.model_validate(produced)
        required = stage.require.get(name)
        if required is not None and required not in {choice.key for choice in request.choices}:
            message = 'Required decision value is not a declared choice'
            raise ValueError(message)
        requests.append((name, request))
    semaphore = asyncio.Semaphore(stage.max_concurrency)

    units = _stage_units(requests, native_batch=stage.native_batch)

    async def dispatch(indices: list[int]) -> list[DecisionResult]:
        if stage.native_batch:
            return await stage.client.abatch(
                [requests[index][1] for index in indices], max_concurrency=1, native_batch=True
            )
        return [await stage.client.adecide(requests[indices[0]][1])]

    async def one(indices: list[int]) -> list[DecisionResult]:
        async with semaphore:
            if group_operations is None:
                async with operations:
                    return await dispatch(indices)
            async with group_operations, operations:
                return await dispatch(indices)

    tasks = [asyncio.create_task(one(indices)) for indices in units]
    try:
        chunks = await asyncio.gather(*tasks)
    except BaseException:
        await drain(cast('list[asyncio.Task[object]]', tasks))
        raise
    indexed = {
        index: answer
        for indices, chunk in zip(units, chunks, strict=True)
        for index, answer in zip(indices, chunk, strict=True)
    }
    return MappingProxyType({name: indexed[index] for index, (name, _) in enumerate(requests)})


def _stage_units(
    requests: list[tuple[str, DecisionRequest]], *, native_batch: bool
) -> list[list[int]]:
    if not native_batch:
        return [[index] for index in range(len(requests))]
    groups: dict[str, list[int]] = {}
    units: list[list[int]] = []
    for index, (_, request) in enumerate(requests):
        if request.privacy != 'public':
            units.append([index])
        else:
            if request.state not in groups:
                groups[request.state] = []
                units.append(groups[request.state])
            groups[request.state].append(index)
    return units


def rejected(stage: DecisionStage, answers: Mapping[str, DecisionResult]) -> bool:
    """Only accepted primary answers can authorize skipping the invocation."""
    return any(
        answers[name].source == 'primary'
        and answers[name].primary.status == 'accepted'
        and answers[name].value != value
        for name, value in stage.require.items()
    )


def _method(scope: object, name: str) -> Callable[..., object]:
    method = getattr(scope, name, None)
    if not callable(method):
        message = 'The bound scope does not support this invocation method'
        raise TypeError(message)
    return method


async def execute(  # noqa: PLR0913 - frozen plan execution parameters.
    scope: object,
    stages: tuple[DecisionStage, ...],
    mapper: Callable[[DecisionContext], object] | None,
    output_model: type[BaseModel] | None,
    messages: object,
    kwargs: Mapping[str, object],
    operations: asyncio.Semaphore,
) -> DecisionInvocationResult:
    """Execute before gates, one invocation and then after annotations."""
    original = deepcopy(messages)
    records: dict[str, Mapping[str, DecisionResult]] = {}
    for stage in stages:
        if stage.at != 'before':
            continue
        records[stage.name] = await run_stage(stage, context(original, None, records), operations)
        if rejected(stage, records[stage.name]):
            return DecisionInvocationResult('skipped', None, immutable_results(records), stage.name)
    invocation_input = mapper(context(original, None, records)) if mapper else deepcopy(original)
    invoke = cast('Callable[..., Awaitable[InvokeResponse]]', _method(scope, 'ainvoke'))
    async with operations:
        response = await invoke(invocation_input, **dict(kwargs))
    response = coerce_structured_response(response, _effective_model(output_model, kwargs))
    for stage in stages:
        if stage.at == 'after':
            records[stage.name] = await run_stage(
                stage, context(original, response, records), operations
            )
    return DecisionInvocationResult('completed', response, immutable_results(records))


def _lifecycle(
    kind: str, stage: DecisionStage | None = None, outcome: DecisionInvocationResult | None = None
) -> DecisionStreamEvent:
    data: dict[str, object] = {}
    if stage is not None:
        data.update(stage=stage.name, at=stage.at, question_count=len(stage.questions))
    if outcome is not None:
        data.update(status=outcome.status, skipped_by=outcome.skipped_by)
    return DecisionStreamEvent(position=0, event_type=kind, data=data, outcome=outcome)


async def stream_execute(  # noqa: PLR0913 - same plan parameters as invoke.
    scope: object,
    stages: tuple[DecisionStage, ...],
    mapper: Callable[[DecisionContext], object] | None,
    output_model: type[BaseModel] | None,
    messages: object,
    kwargs: Mapping[str, object],
    operations: asyncio.Semaphore,
) -> AsyncGenerator[StreamEvent, None]:
    """Forward invocation events as produced and finish only after annotations."""
    original = deepcopy(messages)
    records: dict[str, Mapping[str, DecisionResult]] = {}
    for stage in stages:
        if stage.at != 'before':
            continue
        yield _lifecycle('decision.stage.started', stage)
        records[stage.name] = await run_stage(stage, context(original, None, records), operations)
        yield _lifecycle('decision.stage.completed', stage)
        if rejected(stage, records[stage.name]):
            outcome = DecisionInvocationResult(
                'skipped', None, immutable_results(records), stage.name
            )
            yield _lifecycle('decision.plan.completed', outcome=outcome)
            return
    invocation_input = mapper(context(original, None, records)) if mapper else deepcopy(original)
    method = cast('Callable[..., AsyncIterator[StreamEvent]]', _method(scope, 'astream'))
    events: list[StreamEvent] = []
    has_terminal = False
    async with operations:
        source = method(invocation_input, **dict(kwargs))
        try:
            async for event in source:
                events.append(event)
                if event.event_type in {
                    'final',
                    'session.completed',
                    'error',
                } and not event.data.get('parent_session_id'):
                    has_terminal = True
                yield event
        finally:
            await close_async_iterator(source)
    if not has_terminal:
        message = 'Invocation stream ended without a terminal response'
        raise ValueError(message)
    response = response_with_thread_id(response_from_stream_events(events), _thread_id(kwargs))
    response = coerce_structured_response(response, _effective_model(output_model, kwargs))
    for stage in stages:
        if stage.at == 'after':
            yield _lifecycle('decision.stage.started', stage)
            records[stage.name] = await run_stage(
                stage, context(original, response, records), operations
            )
            yield _lifecycle('decision.stage.completed', stage)
    yield _lifecycle(
        'decision.plan.completed',
        outcome=DecisionInvocationResult('completed', response, immutable_results(records)),
    )


def _effective_model(
    bound: type[BaseModel] | None, kwargs: Mapping[str, object]
) -> type[BaseModel] | None:
    value = kwargs.get('structured_output')
    if bound is not None:
        return bound
    return value if isinstance(value, type) and issubclass(value, BaseModel) else None


def _thread_id(kwargs: Mapping[str, object]) -> str | None:
    explicit = kwargs.get('thread_id')
    if isinstance(explicit, str) and explicit:
        return explicit
    options = kwargs.get('options')
    return options.thread_id if isinstance(options, RunOptions) else None
