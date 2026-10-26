"""Stream helpers behind Client: local tool execution, hydration and interrupt resolution.

Split out of client.py, which re-exports the names other modules import from it.
"""

from __future__ import annotations

import asyncio
import itertools
import logging
import time
from collections.abc import Mapping
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Any, cast

from maivn_contracts.runtime import (
    BooleanInterruptResponse,
    FreeTextInterruptResponse,
    InterruptResponse,
    MultipleChoiceInterruptResponse,
    SingleChoiceInterruptResponse,
    StructuredInterruptResponse,
)
from maivn_contracts.tools import ToolCall

from maivn._internal.api.async_stream import (
    close_async_iterator,
)
from maivn._internal.artifact_image_context import InvocationArtifactImages
from maivn._internal.artifacts import ArtifactsClient
from maivn._internal.compat.decorators import (
    INTERRUPT_DEPENDENCIES_ATTR,
    InterruptDependency,
)
from maivn._internal.decisions.session import current_tool_session
from maivn._internal.error_diagnostics import attach_error_diagnostics, diagnostic_facts
from maivn._internal.models import (
    JsonObject,
    RunOptions,
    StreamEvent,
    tool_card_id,
)
from maivn._internal.private_placeholders import (
    rebase_private_restorations,
    resolve_private_placeholders_with_restorations,
)
from maivn._internal.reporting.context import get_current_reporter
from maivn._internal.tool_files import intake_generated_file_outcome
from maivn._internal.tool_runtime import HookFiring, LocalToolRuntime, collect_interrupt_value
from maivn._internal.tracing import sdk_operation
from maivn._internal.wire import (
    TOOL_RESULT_BATCH_PATH,
)
from maivn.messages import SdkMessagesInput, to_contract_messages
from maivn.telemetry._registry import publish_stream_event

if TYPE_CHECKING:
    from collections.abc import AsyncIterator, Callable, Sequence

    from maivn_contracts.artifacts import ArtifactRef
    from maivn_contracts.tools import ToolOutcomeVariant

    from maivn._internal.models import ToolMetadata
    from maivn._internal.private_artifacts import PrivateArtifactsClient
    from maivn._internal.transport.http import HttpJsonClient

if TYPE_CHECKING:
    from maivn._internal.client import Client


# Logged under client.py's name so log records and filters keep working unchanged.
logger = logging.getLogger('maivn._internal.client')
_ORCHESTRATOR_ASSISTANT_ID = 'orchestrator_agent'


def interrupt_response(answer: object) -> InterruptResponse:
    """Wrap ordinary Python answers while preserving explicitly typed choice envelopes."""
    if isinstance(
        answer,
        (
            FreeTextInterruptResponse,
            BooleanInterruptResponse,
            SingleChoiceInterruptResponse,
            MultipleChoiceInterruptResponse,
            StructuredInterruptResponse,
        ),
    ):
        return answer
    if isinstance(answer, bool):
        return BooleanInterruptResponse(kind='boolean', value=answer)
    if isinstance(answer, str):
        return FreeTextInterruptResponse(kind='free_text', value=answer)
    if isinstance(answer, list):
        items = cast('list[object]', answer)
        if all(isinstance(item, str) for item in items):
            return MultipleChoiceInterruptResponse(
                kind='multiple_choice',
                value=cast('list[str]', items),
            )
    if isinstance(answer, dict):
        return StructuredInterruptResponse(
            kind='structured',
            value=cast('dict[str, Any]', answer),
        )
    message = 'interrupt answer must be text, boolean, string list, object, or typed response'
    raise TypeError(message)


def single_thread_message(message: SdkMessagesInput) -> JsonObject:
    """Encode a one-message endpoint without silently discarding conversation input."""
    messages = to_contract_messages(message)
    if len(messages) != 1:
        error = (
            'Thread continuation requires exactly one message; '
            'pass a string or a single message object. '
            'Use start_thread(...) to supply an initial conversation.'
        )
        raise ValueError(error)
    return messages[0].model_dump(mode='json', exclude_none=True)


def report_invoke_accepted(session_id: str) -> None:
    """Publish the canonical session id at the earliest authoritative boundary."""
    reporter = get_current_reporter()
    if reporter is None:
        return
    report_start = getattr(reporter, 'report_session_start', None)
    if callable(report_start):
        cast('Callable[[str, str], None]', report_start)(
            session_id,
            _ORCHESTRATOR_ASSISTANT_ID,
        )


async def stream_with_local_tools(  # noqa: C901, PLR0913, PLR0915 - coordinated lifecycle pump.
    *,
    events: AsyncIterator[StreamEvent],
    tool_runtime: LocalToolRuntime,
    tool_http: HttpJsonClient,
    session_id: str,
    private_publisher: PrivateArtifactsClient | None = None,
    image_refs: Sequence[ArtifactRef] = (),
) -> AsyncIterator[StreamEvent]:
    """Pump transport/tool work independently from consumer-side event rendering."""
    images = InvocationArtifactImages(
        ordinary=ArtifactsClient(lambda: tool_http),
        private=private_publisher,
        session_id=session_id,
    )
    images.register(image_refs)
    pending_outcomes: list[asyncio.Task[ToolOutcomeVariant | None]] = []
    pending_batches: list[asyncio.Task[None]] = []
    seen_local_calls: dict[str, ToolCall] = {}
    event_queue: asyncio.Queue[StreamEvent | Exception | None] = asyncio.Queue()

    def local_completion_event(
        start_event: StreamEvent,
        outcome: ToolOutcomeVariant,
    ) -> StreamEvent:
        """Project actual local completion without exposing an unredacted result."""
        start_payload = dict(start_event.payload)
        tool_name = start_payload.get('tool_name')
        outcome_payload = outcome.model_dump(mode='json', exclude_none=True)
        outcome_payload.pop('result', None)
        completion_payload: dict[str, object] = {
            **start_payload,
            'stage': 'tool_complete' if outcome.status == 'ok' else 'tool_error',
            'outcome': outcome_payload,
        }
        completion_payload.pop('tool_call', None)
        completion_data = dict(start_event.data)
        completion_type = 'system_tool_complete' if outcome.status == 'ok' else 'system_tool_error'
        completion_data.update(
            {
                'event_id': f'{completion_data.get("event_id", outcome.call_id)}:local',
                'type': completion_type,
                'ts': datetime.now(timezone.utc).isoformat(),
                'payload': completion_payload,
            }
        )
        if isinstance(tool_name, str):
            completion_payload['tool_name'] = tool_name
        return StreamEvent(
            position=start_event.position,
            event_type=completion_type,
            data=completion_data,
        )

    def local_hook_event(
        start_event: StreamEvent,
        firing: HookFiring,
        *,
        card_id: str | None,
        ordinal: int,
    ) -> StreamEvent:
        """Project one developer hook firing onto the run's own event stream.

        Hooks run in this process, so nothing on the wire ever describes them.
        Without this projection a run that fired six callbacks is indis-
        tinguishable from one that registered none - which is exactly what the
        tool-execution-hooks demo reported. Names and outcome only; the payload
        the callback received stays in the developer's process.
        """
        hook_payload: dict[str, object] = {
            'name': firing.name,
            'stage': firing.stage,
            'status': firing.status,
            'source': firing.source,
            'target_type': firing.target_type,
            # A tool firing has to name the same card the tool event creates,
            # or a UI looks up a card that does not exist. Scope firings key on
            # their own display name.
            'target_id': card_id if firing.target_type == 'tool' else firing.target_name,
            'target_name': firing.target_name,
            'error': firing.error,
            'elapsed_ms': firing.elapsed_ms,
        }
        hook_data = dict(start_event.data)
        hook_data.update(
            {
                'event_id': f'{hook_data.get("event_id", card_id)}:hook:{ordinal}',
                'type': 'hook_fired',
                'payload': hook_payload,
            }
        )
        return StreamEvent(
            position=start_event.position,
            event_type='hook_fired',
            data=hook_data,
        )

    async def execute_local_tool(event: StreamEvent) -> ToolOutcomeVariant | None:
        started = False
        completed_call: ToolCall | None = None
        card_id = tool_card_id(event.payload)
        hook_ordinal = itertools.count()

        def report_hook(firing: HookFiring) -> None:
            event_queue.put_nowait(
                local_hook_event(
                    event,
                    firing,
                    card_id=card_id,
                    ordinal=next(hook_ordinal),
                )
            )

        def report_started(tool_call: object) -> None:
            nonlocal started
            started = True
            private_data_keys = tool_runtime.private_data_keys_for_call(cast('ToolCall', tool_call))
            if not private_data_keys:
                event_queue.put_nowait(event)
                return
            enriched_payload = {
                **event.payload,
                'private_data_keys': list(private_data_keys),
            }
            enriched_data = {
                **event.data,
                'payload': enriched_payload,
            }
            event_queue.put_nowait(
                event.model_copy(
                    update={
                        'data': enriched_data,
                        'compat_payload': enriched_payload,
                    }
                )
            )

        def report_completed(
            tool_call: object,
            _outcome: ToolOutcomeVariant,
        ) -> None:
            nonlocal completed_call
            completed_call = cast('ToolCall', tool_call)
            if not started:
                event_queue.put_nowait(event)

        raw_tool_call = event.payload.get('tool_call')
        tool_call_id = (
            cast('Mapping[str, object]', raw_tool_call).get('call_id')
            if isinstance(raw_tool_call, Mapping)
            else None
        )
        with (
            sdk_operation(
                'tool.call',
                attributes={
                    'maivn.tool.name': (
                        event.payload.get('tool_name')
                        if isinstance(event.payload.get('tool_name'), str)
                        else None
                    ),
                    'maivn.tool.kind': 'sdk',
                    'maivn.tool.call.id': (tool_call_id if isinstance(tool_call_id, str) else None),
                },
            ),
            images.activate(),
        ):
            tool_session = current_tool_session.set(session_id)
            try:
                outcome = await tool_runtime.outcome_for_event(
                    event,
                    on_started=report_started,
                    on_completed=report_completed,
                    on_hook=report_hook,
                )
            finally:
                current_tool_session.reset(tool_session)
        if outcome is None:
            event_queue.put_nowait(event)
            return None
        if completed_call is not None:
            outcome = await intake_generated_file_outcome(
                session_id=session_id,
                tool_call=completed_call,
                tool=tool_runtime.metadata_for_call(completed_call),
                private_data_keys=tool_runtime.private_data_keys_for_call(completed_call),
                local_sources=tool_runtime.take_generated_file_sources(completed_call),
                outcome=outcome,
                http=tool_http,
                private_publisher=private_publisher,
            )
        images.register(outcome.artifact_refs or ())
        event_queue.put_nowait(local_completion_event(event, outcome))
        return outcome

    async def submit_batch(tasks: Sequence[asyncio.Task[ToolOutcomeVariant | None]]) -> None:
        started = time.perf_counter()
        resolved = await asyncio.gather(*tasks)
        outcomes = [outcome for outcome in resolved if outcome is not None]
        if not outcomes:
            logger.debug(
                'Skipped SDK tool result batch: task_count=%d outcome_count=0 duration_ms=%d',
                len(tasks),
                round((time.perf_counter() - started) * 1000),
            )
            return
        await tool_http.post(
            TOOL_RESULT_BATCH_PATH.format(session_id=session_id),
            {
                'outcomes': [
                    outcome.model_dump(mode='json', exclude_none=True) for outcome in outcomes
                ]
            },
        )
        logger.debug(
            'Submitted SDK tool result batch: session_id=%s task_count=%d '
            'outcome_count=%d duration_ms=%d',
            session_id,
            len(tasks),
            len(outcomes),
            round((time.perf_counter() - started) * 1000),
        )

    def flush_layer() -> None:
        if not pending_outcomes:
            return
        layer = tuple(pending_outcomes)
        pending_outcomes.clear()
        pending_batches.append(asyncio.create_task(submit_batch(layer)))

    def is_replayed_local_call(event: StreamEvent) -> bool:
        raw_call = event.payload.get('tool_call')
        if not isinstance(raw_call, Mapping):
            return False
        call = ToolCall.model_validate(raw_call)
        if call.spec_ref.namespace != 'sdk' or tool_runtime.is_deferred_call(call):
            return False
        previous = seen_local_calls.get(call.call_id)
        if previous is not None:
            if previous != call:
                message = 'Conflicting SDK tool call identity'
                raise ValueError(message)
            return True
        # Reserve before scheduling, including while the first delivery is still
        # executing or uploading. New call IDs remain independent operations.
        seen_local_calls[call.call_id] = call
        return False

    async def produce_events() -> None:
        try:
            async with tool_http:
                try:
                    async for event in events:
                        images.observe(event)
                        if event.event_type == 'system_tool_start':
                            if is_replayed_local_call(event):
                                continue
                            pending_outcomes.append(asyncio.create_task(execute_local_tool(event)))
                        elif (
                            event.event_type == 'system_tool_chunk'
                            and event.payload.get('phase') == 'started'
                        ):
                            flush_layer()
                        if event.event_type != 'system_tool_start':
                            event_queue.put_nowait(event)
                finally:
                    flush_layer()
                    if pending_batches:
                        await asyncio.gather(*pending_batches)
        except Exception as exc:  # noqa: BLE001 - preserve stream transport failures.
            event_queue.put_nowait(exc)
        finally:
            try:
                await close_async_iterator(events)
            finally:
                event_queue.put_nowait(None)

    producer = asyncio.create_task(produce_events())
    try:
        while True:
            queued = await event_queue.get()
            if queued is None:
                break
            if isinstance(queued, Exception):
                raise queued
            yield queued
    finally:
        if not producer.done():
            producer.cancel()
        await asyncio.gather(producer, return_exceptions=True)


def _canonical_interrupt(event: StreamEvent, *, kind: str) -> Mapping[str, object] | None:
    candidates: list[object] = [event.data.get('interrupt'), event.payload.get('interrupt')]
    stream_part = event.payload.get('stream_part')
    if isinstance(stream_part, Mapping):
        typed_stream_part = cast('Mapping[str, object]', stream_part)
        part_data = typed_stream_part.get('data')
        if isinstance(part_data, Mapping):
            typed_part_data = cast('Mapping[str, object]', part_data)
            candidates.append(typed_part_data.get('interrupt'))
    for candidate in candidates:
        if not isinstance(candidate, Mapping):
            continue
        typed_candidate = cast('Mapping[str, object]', candidate)
        if typed_candidate.get('kind') == kind:
            return typed_candidate
    return None


def _interrupt_dependency_for(
    tools: Sequence[ToolMetadata],
    *,
    tool_name: str,
    arg_name: str,
) -> InterruptDependency | None:
    for tool in tools:
        if tool.name != tool_name:
            continue
        raw = getattr(tool.target, INTERRUPT_DEPENDENCIES_ATTR, None)
        if not isinstance(raw, list):
            return None
        for candidate in cast('list[object]', raw):
            if isinstance(candidate, InterruptDependency) and candidate.arg_name == arg_name:
                return candidate
    return None


def _caller_named_private_values(messages: SdkMessagesInput) -> dict[str, str]:
    """Return the values the caller named in `known_pii_values`, keyed by that name.

    A named known-PII value is the caller's own, supplied by the caller, and the
    server hands it back inside tool calls under `user_<name>`. Only this process can
    turn that key into the value again, so the names travel into the same resolution
    map the declared `private_data` uses. They go UNDER it: an explicit `private_data`
    entry is the caller speaking for this turn and always wins.

    Unnamed entries are skipped. The server keys those positionally, by discovery
    order within one run, so nothing here could match them without guessing.
    """
    named: dict[str, str] = {}
    for message in to_contract_messages(messages):
        redaction = message.redaction
        if redaction is None:
            continue
        for entry in redaction.known_pii_values or ():
            if isinstance(entry, str):
                continue
            if entry.name:
                named.setdefault(entry.name, entry.value)
    return named


def local_resolution_data(
    messages: SdkMessagesInput,
    private_data: Mapping[object, object] | None,
) -> dict[object, object]:
    """Return everything this process may substitute back into its own results.

    The caller's explicit `private_data` sits on top of the values it named in
    `known_pii_values`: an explicit entry is the caller speaking for this turn and
    must win over a name attached to a message.
    """
    resolution: dict[object, object] = {}
    resolution.update(_caller_named_private_values(messages))
    if private_data:
        resolution.update(private_data)
    return resolution


async def hydrated_client_events(
    events: AsyncIterator[StreamEvent],
    *,
    private_data: Mapping[object, object] | None,
    session_id: str | None = None,
) -> AsyncIterator[StreamEvent]:
    """Restore private values in what the caller reads, never in what the server keeps.

    The API redacts every durable event payload, because the event log IS the
    persistence layer - and the same log is what the SSE stream replays. So a caller
    who supplied `serial_number` got `{_{serial_number}_}` back where v1 returned the
    serial itself: in Studio output, in terminal output and in `InvokeResponse.result`
    alike. This process holds the private map and is the vault boundary, so resolution
    belongs here, on the way out. Nothing hydrated is written down or sent back: local
    tool execution and tool-result upload both happen upstream of this generator.

    A key the caller never declared is left as a placeholder rather than blanked, so a
    value this process genuinely cannot resolve stays a visible failure.
    """
    values = {str(key): value for key, value in (private_data or {}).items()}
    identity: dict[str, object] = {'session_id': session_id}
    try:
        async for event in events:
            if not event.data.get('parent_session_id'):
                facts = diagnostic_facts(event.payload, event.data)
                facts.pop('error_code', None)
                identity.update(facts)
                identity['session_id'] = session_id
            yield hydrated_client_event(event, values) if values else event
    except (Exception, asyncio.CancelledError) as exc:
        if session_id is not None:
            attach_error_diagnostics(
                exc,
                identity,
                {'error_code': 'cancelled' if isinstance(exc, asyncio.CancelledError) else None},
            )
        raise
    finally:
        await close_async_iterator(events)


async def telemetry_client_events(
    events: AsyncIterator[StreamEvent],
) -> AsyncIterator[StreamEvent]:
    """Publish the metadata-only public projection before caller hydration."""
    try:
        async for event in events:
            # PRIVACY INVARIANT: listeners receive only the closed metadata allowlist.
            # Never pass the StreamEvent or payload through the public listener surface;
            # arrival-form content may already contain server-resolved values.
            publish_stream_event(event)
            yield event
    finally:
        await close_async_iterator(events)


def hydrated_client_event(
    event: StreamEvent,
    private_data: Mapping[str, object],
) -> StreamEvent:
    """Return one stream event with the caller's own private values restored."""
    data, restorations = resolve_private_placeholders_with_restorations(
        dict(event.data),
        private_data,
    )
    hydrated_data = cast('JsonObject', data)
    payload = hydrated_data.get('payload')
    if isinstance(payload, dict):
        payload['private_value_restorations'] = cast(
            'Any',
            rebase_private_restorations(
                cast('Mapping[str, object]', event.data['payload']), private_data
            )
            + [
                {**span, 'path': span['path'].removeprefix('/payload')}
                for span in restorations
                if span['path'].startswith('/payload/')
            ],
        )
    elif restorations:
        hydrated_data['private_value_restorations'] = cast(
            'Any',
            rebase_private_restorations(event.data, private_data) + restorations,
        )
    update: dict[str, Any] = {'data': hydrated_data}
    if event.compat_payload is not None:
        compat, compat_restorations = resolve_private_placeholders_with_restorations(
            dict(event.compat_payload),
            private_data,
        )
        compat_payload = cast('JsonObject', compat)
        compat_payload['private_value_restorations'] = cast(
            'Any',
            rebase_private_restorations(event.compat_payload, private_data) + compat_restorations,
        )
        update['compat_payload'] = compat_payload
    return event.model_copy(update=update)


async def resolve_canonical_tool_interrupts(
    events: AsyncIterator[StreamEvent],
    *,
    client: Client,
    tools: Sequence[ToolMetadata],
    options: RunOptions,
) -> AsyncIterator[StreamEvent]:
    """Resolve canonical tool-argument checkpoints while retaining the invoke stream."""
    try:
        async for event in events:
            yield event
            interrupt = _canonical_interrupt(event, kind='tool_argument')
            if interrupt is None or (options.origin or '').casefold() == 'studio':
                continue
            tool_name = interrupt.get('tool_name')
            arg_name = interrupt.get('argument_name', interrupt.get('arg_name'))
            thread_id = interrupt.get('thread_id')
            checkpoint_id = interrupt.get('checkpoint_id')
            if not all(
                isinstance(value, str) and value
                for value in (tool_name, arg_name, thread_id, checkpoint_id)
            ):
                continue
            dependency = _interrupt_dependency_for(
                tools,
                tool_name=cast('str', tool_name),
                arg_name=cast('str', arg_name),
            )
            if dependency is None:
                continue
            raw_question = interrupt.get('question')
            question = raw_question if isinstance(raw_question, str) and raw_question else None
            answer = await collect_interrupt_value(
                dependency,
                tool_name=cast('str', tool_name),
                prompt_override=question,
            )
            await client.asubmit_interrupt_response(
                cast('str', thread_id),
                cast('str', checkpoint_id),
                answer=answer,
                responded_by=options.user_id,
                surface='sdk',
            )
    finally:
        await close_async_iterator(events)


__all__ = [
    '_caller_named_private_values',
    '_canonical_interrupt',
    '_interrupt_dependency_for',
    'hydrated_client_event',
    'hydrated_client_events',
    'interrupt_response',
    'local_resolution_data',
    'report_invoke_accepted',
    'resolve_canonical_tool_interrupts',
    'single_thread_message',
    'stream_with_local_tools',
    'telemetry_client_events',
]
