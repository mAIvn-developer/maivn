# ruff: noqa: PLR0913 - the wide signatures here are public SDK entry points; collapsing them into a config object would break callers.
"""V1-compatible invocation helpers shared by Agent and Swarm."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Awaitable, Callable, Iterable, Iterator, Mapping
from concurrent.futures import ThreadPoolExecutor
from typing import TYPE_CHECKING, TypeAlias, cast

from maivn_contracts.messages import Message
from pydantic import BaseModel, ValidationError

from maivn._internal.error_diagnostics import attach_error_diagnostics
from maivn._internal.errors import DurableBoundaryRefusedError, MaivnSDKError
from maivn._internal.event_message import message_from_event
from maivn._internal.models import InvokeResponse, JsonObject, StreamEvent
from maivn._internal.private_placeholders import project_private_restorations

if TYPE_CHECKING:
    from maivn._internal.compat.options import (
        ModelConfig,
        SessionOrchestrationConfig,
        SystemToolsConfig,
    )
    from maivn._internal.models import RunOptions

EventPayloadSink: TypeAlias = Callable[[dict[str, object]], None]

# The API's name for a run ended by the durable privacy boundary. Matched on
# so the SDK can raise a TYPE for it; message text is not a classification surface.
_DURABLE_BOUNDARY_REFUSED_CODE = 'durable_event_refused'


class StructuredOutputInvocationBuilder:
    """Bound invocable that validates the final response against a Pydantic model."""

    def __init__(self, scope: object, model: type[BaseModel]) -> None:
        """Create a structured-output invocable bound to one scope."""
        self._scope = scope
        self._model = model

    def invoke(
        self,
        messages: object,
        *,
        force_final_tool: bool = False,
        targeted_tools: list[str] | None = None,
        thread_id: str | None = None,
        verbose: bool = False,
        reasoning: str | None = None,
        metadata: Mapping[str, object] | None = None,
        memory_config: object | None = None,
        allow_private_in_system_tools: bool | None = None,
        model: str | ModelConfig | None = None,
        system_tools_config: SystemToolsConfig | Mapping[str, object] | None = None,
        orchestration_config: SessionOrchestrationConfig | Mapping[str, object] | None = None,
        stream_response: bool = False,
        options: RunOptions | None = None,
        origin: str | None = None,
    ) -> InvokeResponse:
        """Invoke the bound scope and coerce the response result to the requested model."""
        invoke = cast('Callable[..., InvokeResponse]', _scope_method(self._scope, 'invoke'))
        return invoke(
            messages,
            force_final_tool=force_final_tool,
            targeted_tools=targeted_tools,
            structured_output=self._model,
            thread_id=thread_id,
            verbose=verbose,
            reasoning=reasoning,
            metadata=metadata,
            memory_config=memory_config,
            allow_private_in_system_tools=allow_private_in_system_tools,
            model=model,
            system_tools_config=system_tools_config,
            orchestration_config=orchestration_config,
            stream_response=stream_response,
            options=options,
            origin=origin,
        )

    async def ainvoke(
        self,
        messages: object,
        *,
        force_final_tool: bool = False,
        targeted_tools: list[str] | None = None,
        thread_id: str | None = None,
        verbose: bool = False,
        reasoning: str | None = None,
        metadata: Mapping[str, object] | None = None,
        memory_config: object | None = None,
        allow_private_in_system_tools: bool | None = None,
        model: str | ModelConfig | None = None,
        system_tools_config: SystemToolsConfig | Mapping[str, object] | None = None,
        orchestration_config: SessionOrchestrationConfig | Mapping[str, object] | None = None,
        stream_response: bool = False,
        options: RunOptions | None = None,
        origin: str | None = None,
    ) -> InvokeResponse:
        """Asynchronously invoke the bound scope and coerce the structured result."""
        ainvoke = cast(
            'Callable[..., Awaitable[InvokeResponse]]',
            _scope_method(self._scope, 'ainvoke'),
        )
        return await ainvoke(
            messages,
            force_final_tool=force_final_tool,
            targeted_tools=targeted_tools,
            structured_output=self._model,
            thread_id=thread_id,
            verbose=verbose,
            reasoning=reasoning,
            metadata=metadata,
            memory_config=memory_config,
            allow_private_in_system_tools=allow_private_in_system_tools,
            model=model,
            system_tools_config=system_tools_config,
            orchestration_config=orchestration_config,
            stream_response=stream_response,
            options=options,
            origin=origin,
        )

    def stream(self, messages: object, **kwargs: object) -> Iterator[StreamEvent]:
        """Stream the bound scope while retaining its structured-output contract."""
        stream = cast('Callable[..., Iterator[StreamEvent]]', _scope_method(self._scope, 'stream'))
        return stream(messages, structured_output=self._model, **kwargs)

    async def astream(
        self,
        messages: object,
        **kwargs: object,
    ) -> AsyncIterator[StreamEvent]:
        """Asynchronously stream the bound structured-output scope."""
        astream = cast(
            'Callable[..., AsyncIterator[StreamEvent]]',
            _scope_method(self._scope, 'astream'),
        )
        async for event in astream(messages, structured_output=self._model, **kwargs):
            yield event


class EventInvocationBuilder:
    """Bound invocable that routes v2 stream events through v1-style filters."""

    def __init__(
        self,
        scope: object,
        *,
        include: Iterable[str] | str | None = None,
        exclude: Iterable[str] | str | None = None,
        on_event: EventPayloadSink | None = None,
        auto_verbose: bool = True,
    ) -> None:
        """Create an event-routing invocation builder bound to one scope."""
        self._scope = scope
        self._include = _string_filter(include)
        self._exclude = _string_filter(exclude)
        self._on_event = on_event
        self._auto_verbose = auto_verbose

    def invoke(self, *args: object, **kwargs: object) -> InvokeResponse:
        """Invoke by consuming the scope's stream and projecting the final event."""
        events = list(self.stream(*args, **kwargs))
        if not events:
            message = 'events invocation produced no events'
            raise ValueError(message)
        return response_from_stream_events(events)

    async def ainvoke(self, *args: object, **kwargs: object) -> InvokeResponse:
        """Asynchronously invoke by consuming the scope's async stream."""
        events = [event async for event in self.astream(*args, **kwargs)]
        if not events:
            message = 'events invocation produced no events'
            raise ValueError(message)
        return response_from_stream_events(events)

    def stream(self, *args: object, **kwargs: object) -> Iterator[StreamEvent]:
        """Stream events while routing matching event payloads to ``on_event``."""
        stream = cast('Callable[..., Iterable[StreamEvent]]', _scope_method(self._scope, 'stream'))
        for event in stream(*args, **self._prepare_call_kwargs(kwargs)):
            self._route_event(event)
            yield event

    async def astream(self, *args: object, **kwargs: object) -> AsyncIterator[StreamEvent]:
        """Asynchronously stream events while routing matching payloads."""
        method = getattr(self._scope, 'astream', None)
        if callable(method):
            astream = cast('Callable[..., AsyncIterator[StreamEvent]]', method)
            async for event in astream(*args, **self._prepare_call_kwargs(kwargs)):
                self._route_event(event)
                yield event
            return

        events = await asyncio.to_thread(lambda: list(self.stream(*args, **kwargs)))
        for event in events:
            yield event

    def _prepare_call_kwargs(self, kwargs: Mapping[str, object]) -> dict[str, object]:
        call_kwargs = dict(kwargs)
        if self._auto_verbose and 'verbose' not in call_kwargs:
            call_kwargs['verbose'] = True
        return call_kwargs

    def _route_event(self, event: StreamEvent) -> None:
        if self._on_event is None or not self._matches(event):
            return
        self._on_event(_event_payload(event))

    def _matches(self, event: StreamEvent) -> bool:
        categories = _event_categories(event)
        if self._include is not None and not categories.intersection(self._include):
            return False
        return self._exclude is None or not categories.intersection(self._exclude)


def coerce_structured_response(
    response: InvokeResponse,
    model: type[BaseModel] | None,
) -> InvokeResponse:
    """Return ``response`` with ``result`` validated as ``model`` when requested."""
    if model is None:
        return response
    try:
        if response.result is not None:
            structured = model.model_validate(response.result)
        else:
            structured = model.model_validate_json(response.response)
    except (ValidationError, ValueError) as exc:
        message = f'structured output did not validate as {model.__name__}'
        raise ValueError(message) from exc
    return response.model_copy(update={'result': structured})


def response_from_stream_events(events: Iterable[StreamEvent]) -> InvokeResponse:
    """Project a consumed event stream into the closest v2 ``InvokeResponse`` shape."""
    event_items = list(events)
    if not event_items:
        message = 'events invocation produced no events'
        raise ValueError(message)

    terminal = _terminal_event(event_items)
    data = terminal.data
    payload = _mapping_value(data.get('payload')) or {}
    _raise_for_terminal_error(terminal, data, payload, events=event_items)
    final_message, restorations = _final_message(data, payload, terminal)
    content = final_message.content if isinstance(final_message.content, str) else ''
    usage = _mapping_value(payload.get('usage')) or _mapping_value(data.get('usage')) or {}
    result = payload.get('result', data.get('result'))
    tool_calls = _mapping_value(payload.get('tool_calls')) or {}
    run_config = (
        _mapping_value(payload.get('run_config')) or _mapping_value(data.get('run_config')) or {}
    )

    return InvokeResponse(
        final_message=final_message,
        organization_id=_first_string(payload.get('organization_id')),
        project_id=_first_string(payload.get('project_id')),
        session_id=_first_string(payload.get('session_id'), data.get('session_id')) or 'ses-events',
        thread_id=_first_string(
            payload.get('thread_id'),
            data.get('thread_id'),
            run_config.get('thread_id'),
        ),
        root_event_id=(
            _first_string(
                payload.get('root_event_id'),
                data.get('root_event_id'),
                data.get('event_id'),
            )
            or 'evt-events-root'
        ),
        event_positions=[event.position for event in event_items],
        usage=cast('JsonObject', usage),
        stop_reason=_first_string(payload.get('stop_reason'), data.get('stop_reason')),
        tool_call_count=_int_value(
            tool_calls.get('count'),
            payload.get('tool_call_count'),
            data.get('tool_call_count'),
        ),
        tool_names=_string_list(
            tool_calls.get('names') or payload.get('tool_names') or data.get('tool_names'),
        ),
        response=content,
        private_value_restorations=restorations,
        result=result,
        assistant_id=_first_string(payload.get('assistant_id'), data.get('assistant_id')),
        artifacts=tuple(final_message.artifact_refs or ()),
    )


def resolve_max_concurrency(max_concurrency: int | None, input_count: int) -> int | None:
    """Validate and cap a v1 ``max_concurrency`` value for batch invocation."""
    if max_concurrency is not None and max_concurrency < 1:
        message = 'max_concurrency must be greater than 0'
        raise ValueError(message)
    if input_count < 1:
        return 0
    if max_concurrency is None:
        return None
    return min(max_concurrency, input_count)


def run_batch(
    scope: object,
    inputs: Iterable[object],
    *,
    max_concurrency: int | None,
    invoke_kwargs: Mapping[str, object],
) -> list[InvokeResponse]:
    """Invoke ``scope`` for each input concurrently, preserving input order."""
    input_items = list(inputs)
    max_workers = resolve_max_concurrency(max_concurrency, len(input_items))
    if max_workers == 0:
        return []

    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        futures = [
            executor.submit(_invoke_batch_item, scope, item, dict(invoke_kwargs))
            for item in input_items
        ]
        return [future.result() for future in futures]


async def run_abatch(
    scope: object,
    inputs: Iterable[object],
    *,
    max_concurrency: int | None,
    invoke_kwargs: Mapping[str, object],
) -> list[InvokeResponse]:
    """Asynchronously invoke ``scope`` for each input, preserving input order."""
    input_items = list(inputs)
    max_workers = resolve_max_concurrency(max_concurrency, len(input_items))
    if max_workers == 0:
        return []

    semaphore = asyncio.Semaphore(max_workers or len(input_items))

    async def _invoke_one(input_item: object) -> InvokeResponse:
        async with semaphore:
            ainvoke = cast(
                'Callable[..., Awaitable[InvokeResponse]]',
                _scope_method(scope, 'ainvoke'),
            )
            return await ainvoke(input_item, **dict(invoke_kwargs))

    return list(await asyncio.gather(*(_invoke_one(item) for item in input_items)))


def _invoke_batch_item(
    scope: object,
    input_item: object,
    invoke_kwargs: dict[str, object],
) -> InvokeResponse:
    invoke = cast('Callable[..., InvokeResponse]', _scope_method(scope, 'invoke'))
    return invoke(input_item, **invoke_kwargs)


def _scope_method(scope: object, method_name: str) -> Callable[..., object]:
    method = getattr(scope, method_name, None)
    if method is None:
        message = f'Scope does not support {method_name}().'
        raise AttributeError(message)
    if not callable(method):
        message = f'Scope attribute {method_name} is not callable.'
        raise TypeError(message)
    return method


def _string_filter(value: Iterable[str] | str | None) -> frozenset[str] | None:
    if value is None:
        return None
    if isinstance(value, str):
        return frozenset({value})
    return frozenset(str(item) for item in value)


def _event_categories(event: StreamEvent) -> frozenset[str]:
    categories = {event.event_type}
    event_type = event.data.get('type')
    if isinstance(event_type, str):
        categories.add(event_type)
    payload = _mapping_value(event.data.get('payload'))
    category = payload.get('category') if payload is not None else None
    if isinstance(category, str):
        categories.add(category)
    return frozenset(categories)


def _event_payload(event: StreamEvent) -> dict[str, object]:
    if event.event_type == 'assignment_received':
        payload = _mapping_value(event.data.get('payload')) or {}
        session_id = event.data.get('session_id')
        if isinstance(session_id, str) and session_id:
            payload = {**payload, 'session_id': session_id}
        return {'event': 'session_start', 'payload': payload}
    if event.event_type in {'enrichment', 'system_tool_chunk'}:
        payload = _mapping_value(event.data.get('payload'))
        if payload is not None:
            phase = payload.get('phase')
            if isinstance(phase, str) and phase:
                return {'event': 'enrichment', 'payload': payload}
    payload = dict(event.data)
    payload.setdefault('category', event.event_type)
    return payload


def _terminal_event(events: list[StreamEvent]) -> StreamEvent:
    """Return the run's own terminal event, never a delegated child's.

    A delegating run's stream carries its sub-agents' events too, each stamped with a
    parent_session_id. A child finishing - or failing - is a tool-argument outcome inside
    the parent's run, not the run's own ending, so child terminals never speak for the run.
    """
    terminal_types = {'final', 'session.completed', 'error'}
    for event in reversed(events):
        if event.event_type in terminal_types and not _is_child_event(event):
            return event
    return events[-1]


def _is_child_event(event: StreamEvent) -> bool:
    parent_session_id = event.data.get('parent_session_id')
    return isinstance(parent_session_id, str) and bool(parent_session_id)


def _raise_for_terminal_error(
    terminal: StreamEvent,
    data: Mapping[str, object],
    payload: Mapping[str, object],
    *,
    events: Iterable[StreamEvent] = (),
) -> None:
    """Raise the run's terminal error, naming it as precisely as the wire allows.

    BENCH-2026-08-16 (claude): durable error events deliberately carry no message -
    `durable_tool_outcome_source` and the loop error projection both drop it rather than
    persist free text - but they DO carry `error_code`, a stable enumerated token. Reading
    only `message` meant every such failure surfaced as the bare string "invoke failed":
    the complex-types demo died on `structured_output_invalid_json` and said nothing at
    all, in the trace panel or in the raised exception. Preferring the code over the
    fallback names the failure without putting provider or customer text on the wire.
    """
    if terminal.event_type != 'error':
        return
    error_type = _first_string(payload.get('error_type'), data.get('error_type'))
    error_code = _first_string(payload.get('error_code'), data.get('error_code'))
    refusal_reason = (
        _repeated_tool_failure_reason(events) if error_code == 'tool_repeated_failure' else None
    )
    if error_code == 'redacted_message_attachment_unshieldable':
        refusal_reason = (
            'This attachment cannot be redacted in the message path and will not be sent '
            'to a model. For video or audio, provide a transcript produced in your private '
            'environment and send it as a text attachment in a redacted message.'
        )
    message = (
        _first_string(payload.get('message'), data.get('message'))
        or refusal_reason
        or error_code
        or _first_string(payload.get('termination_reason'), data.get('termination_reason'))
        or 'invoke failed'
    )
    # Thread P2. A run refused by the durable privacy boundary carried a field path on
    # the terminal receipt and nothing surfaced it, so callers read a bare
    # `durable_event_refused` and had no way to tell WHICH field ended the run. The path
    # is structure - a projection name and field names, value-free by construction at
    # the boundary that produced it - so it is safe to put in the raised detail.
    refused_path = _first_string(payload.get('refused_path'), data.get('refused_path'))
    if refused_path is not None:
        message = f'{message} at {refused_path}'
    detail = f'{error_type}: {message}' if error_type is not None else message
    error = (
        DurableBoundaryRefusedError(detail, refused_path=refused_path)
        if error_code == _DURABLE_BOUNDARY_REFUSED_CODE
        else MaivnSDKError(detail)
    )
    error.session_id = _first_string(payload.get('session_id'), data.get('session_id'))
    error.root_event_id = _first_string(payload.get('root_event_id'), data.get('root_event_id'))
    attach_error_diagnostics(error, payload, data)
    raise error


def _repeated_tool_failure_reason(events: Iterable[StreamEvent]) -> str | None:
    """Recover the latest stable tool-error code hidden by a retry terminal."""
    for event in reversed(list(events)):
        if event.event_type != 'system_tool_error' or _is_child_event(event):
            continue
        event_payload = _mapping_value(event.data.get('payload')) or event.data
        outcome = _mapping_value(event_payload.get('outcome')) or {}
        error = _mapping_value(outcome.get('error')) or {}
        error_code = _first_string(error.get('error_code'), error.get('code'))
        if error_code is not None:
            return error_code
    return None


def _final_message(
    data: Mapping[str, object],
    payload: Mapping[str, object],
    event: StreamEvent,
) -> tuple[Message, list[JsonObject]]:
    for source, path in (
        (payload, '/message'),
        (payload, '/final_message'),
        (data, '/final_message'),
    ):
        message_payload = source.get(path[1:])
        if not message_payload:
            continue
        if isinstance(message_payload, Mapping):
            message = message_from_event(cast('Mapping[str, object]', message_payload), data)
            spans = project_private_restorations(
                source.get('private_value_restorations'),
                f'{path}/content',
                '/response',
                length=len(message.content) if isinstance(message.content, str) else 0,
            )
            return message, cast('list[JsonObject]', spans)
        break
    content = ''
    restorations: list[JsonObject] = []
    for source, path in ((payload, '/content'), (payload, '/response'), (data, '/content')):
        candidate = source.get(path[1:])
        if isinstance(candidate, str) and candidate:
            content = candidate
            restorations = cast(
                'list[JsonObject]',
                project_private_restorations(
                    source.get('private_value_restorations'),
                    path,
                    '/response',
                    length=len(content),
                ),
            )
            break
    message = Message(
        message_id=_first_string(payload.get('message_id'), data.get('message_id'))
        or f'msg-event-{event.position}',
        role='assistant',
        content=content,
        ts=_first_string(payload.get('ts'), data.get('ts')) or '1970-01-01T00:00:00Z',
    )
    return message, restorations


def _mapping_value(value: object) -> dict[str, object] | None:
    if isinstance(value, Mapping):
        return dict(cast('Mapping[str, object]', value))
    return None


def _first_string(*values: object) -> str | None:
    for value in values:
        if isinstance(value, str) and value:
            return value
    return None


def _int_value(*values: object) -> int:
    for value in values:
        if isinstance(value, bool):
            continue
        if isinstance(value, int):
            return value
    return 0


def _string_list(value: object) -> list[str]:
    if isinstance(value, list):
        return [str(item) for item in cast('list[object]', value)]
    return []


__all__ = [
    'EventInvocationBuilder',
    'StructuredOutputInvocationBuilder',
    'coerce_structured_response',
    'resolve_max_concurrency',
    'response_from_stream_events',
    'run_abatch',
    'run_batch',
]
