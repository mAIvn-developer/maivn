"""Private local presentation inspection custody; no inference provider is enrolled.

This seam deliberately has no public options or wire representation. Only the
nominal presentation owner may receive original intake. Offline composition is a
trusted local callback, not provider clearance or durable expense authorization.
"""

from __future__ import annotations

import asyncio
import hashlib
import inspect
import json
import sys
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass, field, replace
from types import MappingProxyType
from typing import TYPE_CHECKING, Protocol, cast

from pydantic import BaseModel

from maivn.messages import SdkMessagesInput, to_contract_messages

if TYPE_CHECKING:
    from maivn_contracts.messages import Message
    from maivn_contracts.tools import ToolCall

    from maivn._internal.models import ToolMetadata

_RUBRIC_VERSION = 'presentation-semantic-advisory-v1'
_CRITERIA = frozenset({'source_fidelity', 'intent_completeness', 'prose_presentation'})
_SUPPORTED_LEVEL = 3
__all__ = [
    'InspectionCall',
    'InspectionSource',
    'LocalInspectionInvoke',
    'OfflineReviewComposition',
    '_enroll_offline_reviewer',
    'admitted_hints',
    'capture_invoke_inspection',
    'digest',
    'dispatch_owner_inspection',
    'freeze',
    'inspection_call_seal',
    'inspection_worker',
    'presentation_owner',
    'thaw',
]


def freeze(value: object) -> object:
    """Detach JSON-shaped custody recursively, refusing unsupported mutable values."""
    if isinstance(value, BaseModel):
        return freeze(value.model_dump(mode='json'))
    if isinstance(value, Mapping):
        mapping = cast('Mapping[object, object]', value)
        return MappingProxyType({str(key): freeze(item) for key, item in mapping.items()})
    if isinstance(value, (list, tuple)):
        return tuple(freeze(item) for item in cast('list[object] | tuple[object, ...]', value))
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    message = 'unsupported local inspection custody value'
    raise TypeError(message)


def thaw(value: object) -> object:
    if isinstance(value, Mapping):
        return {key: thaw(item) for key, item in cast('Mapping[str, object]', value).items()}
    if isinstance(value, tuple):
        return [thaw(item) for item in cast('tuple[object, ...]', value)]
    return value


def digest(value: object) -> str:
    return hashlib.sha256(
        json.dumps(
            thaw(value), ensure_ascii=False, sort_keys=True, separators=(',', ':'), allow_nan=False
        ).encode()
    ).hexdigest()


@dataclass(frozen=True, slots=True)
class LocalInspectionInvoke:
    messages: tuple[Mapping[str, object], ...] = field(repr=False)
    private_data: Mapping[str, object] = field(repr=False)
    thread_id: str | None = field(repr=False)
    project_id: str | None = field(repr=False)
    complete_intent: bool
    intent_digest: str = field(repr=False)
    session_id: str | None = field(default=None, repr=False)

    def accepted(self, session_id: str) -> LocalInspectionInvoke:
        accepted = replace(self, session_id=session_id)
        return replace(accepted, intent_digest=digest(_invoke_binding(accepted)))

    def current(self) -> bool:
        return self.intent_digest == digest(_invoke_binding(self))


def _invoke_binding(invoke: LocalInspectionInvoke) -> tuple[object, ...]:
    return (
        invoke.messages,
        invoke.private_data,
        invoke.thread_id,
        invoke.project_id,
        invoke.complete_intent,
        invoke.session_id,
    )


def _callback_implementation(callback: object) -> tuple[object, ...]:
    function = getattr(callback, '__func__', callback)
    call_method = getattr(type(callback), '__call__', None)  # noqa: B004 - bind actual dispatch implementation.
    return (
        callback,
        type(callback),
        getattr(callback, '__self__', None),
        function,
        getattr(function, '__code__', None),
        call_method,
        getattr(call_method, '__code__', None),
    )


def _callback_seal(callback: object) -> tuple[int, ...]:
    """Bind the actual callable implementation, including in-place code replacement."""
    return tuple(id(value) for value in _callback_implementation(callback))


@dataclass(frozen=True, slots=True)
class OfflineReviewComposition:
    callback: Callable[[InspectionSource, InspectionCall], Awaitable[object]] = field(repr=False)


@dataclass(frozen=True, slots=True)
class InspectionCall:
    tool_call: Mapping[str, object] = field(repr=False)
    target: object = field(repr=False)
    arguments: Mapping[str, object] = field(repr=False)
    invoke: LocalInspectionInvoke = field(repr=False)
    composition: OfflineReviewComposition = field(repr=False)
    binding_digest: str = field(repr=False)
    resolution_current: Callable[[], bool] = field(repr=False)
    callback_identity: int = field(repr=False)
    callback_implementation: tuple[int, ...] = field(repr=False)

    def current(self) -> bool:
        return (
            self.invoke.current()
            and self.resolution_current()
            and id(self.composition.callback) == self.callback_identity
            and _callback_seal(self.composition.callback) == self.callback_implementation
            and self.binding_digest
            == digest((self.tool_call, self.arguments, self.invoke.intent_digest))
        )


def inspection_call_seal(call: InspectionCall) -> tuple[str, tuple[object, ...]]:
    """Seal complete exposed records against independent owner-held originals."""
    signature = digest(
        (
            call.tool_call,
            id(call.target),
            _callback_seal(call.target),
            call.arguments,
            id(call.invoke),
            _invoke_binding(call.invoke),
            call.invoke.intent_digest,
            id(call.composition),
            _callback_seal(call.composition.callback),
            call.binding_digest,
            _callback_seal(call.resolution_current),
            call.callback_identity,
            call.callback_implementation,
        )
    )
    # Hold original implementations as well as their identities so replacement
    # cannot recycle an old Python object ID after a callback changes a field.
    references = (
        call.invoke,
        call.composition,
        *_callback_implementation(call.target),
        *_callback_implementation(call.composition.callback),
        *_callback_implementation(call.resolution_current),
    )
    return signature, references


@dataclass(frozen=True, slots=True)
class InspectionSource:
    manifest: Mapping[str, object] = field(repr=False)
    handle: Mapping[str, object] = field(repr=False)
    stamp: object = field(repr=False)
    deterministic_issues: tuple[Mapping[str, object], ...] = field(repr=False)
    custody: str
    owner: object = field(repr=False)
    protected_projection: None = field(default=None, repr=False)
    raw_to_protected: None = field(default=None, repr=False)


@dataclass(frozen=True, slots=True)
class _SemanticObservation:
    criterion: str = field(repr=False)
    level: int = field(repr=False)
    slide_id: str = field(repr=False)
    element_id: str = field(repr=False)
    composition_id: str = field(repr=False)


@dataclass(frozen=True, slots=True)
class _SemanticReview:
    rubric_version: str = field(repr=False)
    observations: tuple[_SemanticObservation, ...] = field(repr=False)


def capture_invoke_inspection(
    messages: SdkMessagesInput,
    *,
    private_data: Mapping[object, object] | None,
    thread_id: str | None,
    project_id: str | None,
    complete_history: bool | None = None,
) -> tuple[list[Message], LocalInspectionInvoke | None]:
    normalized = [message.model_copy(deep=True) for message in to_contract_messages(messages)]
    try:
        intake = cast('tuple[Mapping[str, object], ...]', freeze(normalized))
        resolution: dict[object, object] = {}
        for message in normalized:
            if message.redaction is not None:
                for entry in message.redaction.known_pii_values or ():
                    if not isinstance(entry, str) and entry.name:
                        resolution.setdefault(entry.name, entry.value)
        resolution.update(private_data or {})
        private = cast('Mapping[str, object]', freeze(resolution))
        fresh = thread_id is None if complete_history is None else complete_history
        complete = (
            bool(intake)
            and fresh
            and all(not message.attachments and not message.artifact_refs for message in normalized)
            and all(
                isinstance(message.content, str)
                or all(block.type == 'text' for block in message.content)
                for message in normalized
            )
        )
        context = LocalInspectionInvoke(
            intake,
            private,
            thread_id,
            project_id,
            complete,
            digest((intake, private, thread_id, project_id, complete, None)),
        )
    except (TypeError, ValueError):
        context = None
    return normalized, context


class _PresentationInspectionOwner(Protocol):
    """Typing only; enrollment uses exact nominal class and function identity."""

    _semantic_review_composition: OfflineReviewComposition | None

    async def _sdk_inspection_dispatch(
        self,
        *,
        call: InspectionCall,
        arguments: Mapping[str, object],
        dispatch: Callable[[], Awaitable[object]],
    ) -> object: ...


def presentation_owner(target: object) -> _PresentationInspectionOwner | None:
    # No structural getattr protocol: user tool hooks never receive private intake.
    module = sys.modules.get('maivn_tools.connectors.presentations.toolset')
    owner_type = getattr(module, 'PresentationsToolSet', None)
    owner = getattr(target, '__self__', None)
    if owner_type is None or type(owner) is not owner_type:
        return None
    function = getattr(target, '__func__', target)
    if not callable(function):
        return None
    if inspect.unwrap(function) is not inspect.unwrap(owner_type.inspect_layout):
        return None
    return cast('_PresentationInspectionOwner', owner)


def _enroll_offline_reviewer(
    owner: object,
    callback: Callable[[InspectionSource, InspectionCall], Awaitable[object]],
) -> None:
    registered = presentation_owner(getattr(owner, 'inspect_layout', None))
    if registered is not owner or registered is None:
        message = 'local inspection requires the nominal presentation owner'
        raise TypeError(message)
    if not inspect.iscoroutinefunction(callback):
        message = 'local inspection requires an async offline callback'
        raise TypeError(message)
    registered._semantic_review_composition = OfflineReviewComposition(callback)  # pyright: ignore[reportPrivateUsage] # noqa: SLF001 - private nominal owner composition.


def _bind_inspection_call(
    tool: ToolMetadata,
    arguments: Mapping[str, object],
    context: LocalInspectionInvoke | None,
    tool_call: ToolCall,
    private_resolution: Mapping[str, object],
) -> tuple[_PresentationInspectionOwner, InspectionCall] | None:
    owner = presentation_owner(tool.target)
    if owner is None or context is None or not context.complete_intent or not context.current():
        return None
    composition = getattr(owner, '_semantic_review_composition', None)
    if (
        type(composition) is not OfflineReviewComposition
        or context.session_id is None
        or tool_call.lineage.session_id != context.session_id
    ):
        return None

    def resolution_current() -> bool:
        try:
            return digest(freeze(private_resolution)) == digest(context.private_data)
        except (TypeError, ValueError):
            return False

    if not resolution_current():
        return None
    # No model argument may substitute an original-invocation capability. Nested
    # sessions need an actual original-history binding before they can enroll.
    frozen_call = cast('Mapping[str, object]', freeze(tool_call))
    frozen_arguments = cast('Mapping[str, object]', freeze(arguments))
    binding = InspectionCall(
        frozen_call,
        tool.target,
        frozen_arguments,
        context,
        composition,
        digest((frozen_call, frozen_arguments, context.intent_digest)),
        resolution_current,
        id(composition.callback),
        _callback_seal(composition.callback),
    )
    return owner, binding


async def dispatch_owner_inspection(  # noqa: PLR0913 - exact call and cancellation custody.
    tool: ToolMetadata,
    arguments: Mapping[str, object],
    context: LocalInspectionInvoke | None,
    tool_call: ToolCall,
    dispatch: Callable[[], Awaitable[object]],
    private_resolution: Mapping[str, object],
    on_owner_dispatch: Callable[[], None] | None = None,
) -> object:
    bound = _bind_inspection_call(tool, arguments, context, tool_call, private_resolution)
    if bound is None:
        return await dispatch()
    owner, call = bound
    if on_owner_dispatch is not None:
        on_owner_dispatch()
    # Invoke the exact nominal implementation rather than a replaceable instance hook.
    return await type(owner)._sdk_inspection_dispatch(  # pyright: ignore[reportPrivateUsage] # noqa: SLF001 - exact nominal private dispatch.
        owner,
        call=call,
        arguments=arguments,
        dispatch=dispatch,
    )


async def inspection_worker(callback: Callable[[], object]) -> object:
    """Keep every worker owned through terminal completion, including cancellation."""
    task = asyncio.create_task(asyncio.to_thread(callback))
    try:
        return await asyncio.shield(task)
    except asyncio.CancelledError:
        while not task.done():
            try:
                await asyncio.shield(task)
            except asyncio.CancelledError:  # noqa: PERF203 - repeated cancellation must still drain the owned worker.
                continue
            except Exception:  # noqa: BLE001 - drain worker failure while preserving caller cancellation.
                break
        if not task.cancelled():
            task.exception()
        raise


def admitted_hints(  # noqa: C901, PLR0911 - strict all-or-nothing observation admission.
    review: object,
    source: InspectionSource,
) -> list[dict[str, str]]:
    """Strict exact-label/occurrence validation; arbitrary prose never crosses out."""
    if type(review) is not _SemanticReview:
        return []
    if type(review.rubric_version) is not str or review.rubric_version != _RUBRIC_VERSION:
        return []
    if type(review.observations) is not tuple:
        return []
    occurrences: set[tuple[object, object, object]] = set()
    slides = cast('tuple[Mapping[str, object], ...]', source.manifest['slides'])
    for slide in slides:
        for entry in cast('tuple[Mapping[str, object], ...]', slide['elements']):
            element = cast('Mapping[str, object]', entry['element'])
            composition = cast('Mapping[str, object]', element['composition'])
            occurrences.add((slide['slide_id'], entry['element_id'], composition['composition_id']))
    seen: set[tuple[str, tuple[str, str, str]]] = set()
    hints: list[dict[str, str]] = []
    for observation in review.observations:
        if type(observation) is not _SemanticObservation:
            return []
        if any(
            type(value) is not str
            for value in (
                observation.criterion,
                observation.slide_id,
                observation.element_id,
                observation.composition_id,
            )
        ):
            return []
        identity = (observation.slide_id, observation.element_id, observation.composition_id)
        key = (observation.criterion, identity)
        if (
            observation.criterion not in _CRITERIA
            or type(observation.level) is not int
            or observation.level not in range(4)
            or identity not in occurrences
            or key in seen
        ):
            return []
        seen.add(key)
        if observation.level == _SUPPORTED_LEVEL:
            continue
        messages = (
            'Local semantic review advisory: an explicit intent obligation may be violated; '
            'review this occurrence against the original request.',
            'Local semantic review advisory: this occurrence is ambiguous; review it '
            'against the original request.',
            'Local semantic review advisory: an explicit style obligation may be weak; '
            'review this occurrence against the original request.',
        )
        hints.append(
            {
                'slide_id': observation.slide_id,
                'element_id': observation.element_id,
                'code': f'semantic_review_{observation.criterion}',
                'message': messages[observation.level],
            }
        )
    return hints
