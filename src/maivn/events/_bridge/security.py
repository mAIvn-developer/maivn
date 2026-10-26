"""Security policy helpers for frontend-facing event bridges."""

from __future__ import annotations

import dataclasses
import logging
from collections.abc import Mapping
from dataclasses import dataclass
from types import SimpleNamespace
from typing import Literal, cast

from pydantic import BaseModel, RootModel

from maivn._internal.compat.privacy import PrivateData
from maivn._internal.sanitize import sanitize_user_facing_error_message

from .redaction import dataclass_fields as _dataclass_fields
from .redaction import private_data_descriptors_only, redact_private_data

# MARK: Configuration

BridgeAudience = Literal['internal', 'frontend_safe']
EventPayload = dict[str, object]

_REDACTED_VALUE = '<redacted>'
# Real event payloads nest a handful of levels; anything deeper, or any cycle, is cut
# and replaced with the redaction marker rather than walked or passed through.
_MAX_SCRUB_DEPTH = 64
_INJECTED_DATA_FIELDS = frozenset({'private_data_injected', 'interrupt_data_injected'})
_REDACTION_DICT_FIELDS = frozenset({'added_private_data', 'merged_private_data'})
_REDACTION_LIST_FIELDS = frozenset({'matched_known_pii_values', 'unmatched_known_pii_values'})
_KNOWN_FRONTEND_SAFE_EVENT_TYPES: frozenset[str] = frozenset(
    {
        'tool_event',
        'system_tool_complete',
        'system_tool_start',
        'system_tool_chunk',
        'agent_assignment',
        'final',
        'enrichment',
        'error',
        'assistant_chunk',
        'status_message',
        'status_message_chunk',
        'interrupt_required',
        'hook_fired',
        'session_start',
        'session_end',
    }
)
_logger = logging.getLogger('maivn.events._bridge.security')


# MARK: Validation


def _validate_audience(value: str) -> BridgeAudience:
    normalized = value.strip().lower()
    if normalized == 'internal':
        return 'internal'
    if normalized == 'frontend_safe':
        return 'frontend_safe'
    raise ValueError("EventBridge audience must be 'internal' or 'frontend_safe'")


# MARK: Security Policy


@dataclass(frozen=True)
class EventBridgeSecurityPolicy:
    """Controls how much sensitive detail reaches bridge history and SSE."""

    audience: BridgeAudience = 'internal'

    def __post_init__(self) -> None:
        object.__setattr__(self, 'audience', _validate_audience(self.audience))

    def sanitize_event(self, event_type: str, data: EventPayload) -> EventPayload:
        """Return a bridge-safe payload for the configured audience.

        For ``frontend_safe`` bridges, every payload, whatever its event
        type, goes through an injected-fields scrubber that summarizes any
        field named in :data:`_INJECTED_DATA_FIELDS` at any depth. Event
        types with dedicated handling also have their error and redaction
        details masked. Unknown event types are logged as a warning so a
        third-party emitter can add dedicated handling if it needs it.
        """
        if self.audience == 'internal':
            return data

        normalized_event_type = event_type.strip().lower()
        safe_payload = data

        if normalized_event_type == 'tool_event':
            safe_payload = _sanitize_error_fields(safe_payload, nested_error_keys=('tool',))
        elif normalized_event_type == 'agent_assignment':
            safe_payload = _sanitize_error_fields(
                safe_payload,
                nested_error_keys=('assignment',),
            )
        elif normalized_event_type == 'enrichment':
            safe_payload = _sanitize_enrichment_payload(safe_payload)
        elif normalized_event_type == 'error':
            safe_payload = _sanitize_error_payload(safe_payload)
        elif normalized_event_type not in _KNOWN_FRONTEND_SAFE_EVENT_TYPES:
            _logger.warning(
                'Unknown event type %r emitted to frontend_safe bridge; '
                'applying generic injected-fields sanitization. '
                'Add the type to _KNOWN_FRONTEND_SAFE_EVENT_TYPES or extend '
                'sanitize_event() if it needs custom handling.',
                event_type,
            )

        return cast('EventPayload', _InjectedFieldScrubber().scrub(safe_payload))


# MARK: Payload Sanitizers


class _InjectedFieldScrubber:
    """One walk over a payload: injected fields summarized at any depth.

    Each container is walked once; cycles and over-deep nesting are cut. ``scrub`` returns
    the input object itself when nothing below it changed.
    """

    def __init__(self) -> None:
        self._active: set[int] = set()
        # Results by container identity, so a container reused many times is walked once.
        # Each entry keeps its container alive: a dumped model's children are temporary, and
        # a freed object's id could otherwise be reused by a different one later in the walk.
        self._done: dict[int, tuple[object, object]] = {}

    def scrub(self, value: object) -> object:
        if isinstance(value, RootModel):
            # The serializer emits a root model as its root value, so walk that.
            root = cast('object', value.root)
            scrubbed_root = self.scrub(root)
            return cast('object', value) if scrubbed_root is root else scrubbed_root
        container = _as_container(value)
        if container is None:
            return value
        value_id = id(value)
        if value_id in self._done:
            return self._done[value_id][1]
        if value_id in self._active or len(self._active) >= _MAX_SCRUB_DEPTH:
            return _REDACTED_VALUE
        self._active.add(value_id)
        try:
            scrubbed = self._scrub_container(container)
        finally:
            self._active.remove(value_id)
        result = value if scrubbed is container else scrubbed
        self._done[value_id] = (value, result)
        return result

    def _scrub_container(self, value: EventPayload | list[object] | tuple[object, ...]) -> object:
        if isinstance(value, dict):
            scrubbed: EventPayload = {}
            changed = False
            for key, item in value.items():
                if _is_injected_field(key):
                    scrubbed[key] = _summarize_injected_payload(item)
                    changed = True
                    continue
                new_item = self.scrub(item)
                changed = changed or new_item is not item
                scrubbed[key] = new_item
            return scrubbed if changed else value
        new_items = [self.scrub(item) for item in value]
        if all(new is orig for new, orig in zip(new_items, value, strict=True)):
            return value
        return tuple(new_items) if isinstance(value, tuple) else new_items


def _as_container(value: object) -> EventPayload | list[object] | tuple[object, ...] | None:
    """Return the walkable view of a container, or None for a leaf.

    ``PrivateData`` is a leaf: serialization replaces it with a value-free descriptor, so
    it must reach the serializer as itself. Models and dataclasses are read field by
    field (never dumped), so a ``PrivateData`` inside them survives a rebuild intact.
    """
    if isinstance(value, PrivateData):
        return None
    if isinstance(value, dict):
        return cast('EventPayload', value)
    if isinstance(value, list):
        return cast('list[object]', value)
    if isinstance(value, tuple):
        return cast('tuple[object, ...]', value)
    if isinstance(value, set | frozenset):
        return list(cast('frozenset[object]', value))
    if isinstance(value, Mapping):
        return dict(cast('Mapping[str, object]', value))
    if isinstance(value, BaseModel):
        return _model_view(value)
    if isinstance(value, SimpleNamespace):
        return cast('EventPayload', vars(value))
    if dataclasses.is_dataclass(value) and not isinstance(value, type):
        return _dataclass_fields(value)
    return None


def _model_view(model: BaseModel) -> EventPayload | list[object] | tuple[object, ...] | None:
    """Walk what the serializer will emit for a model, keeping ``PrivateData`` intact.

    Without ``PrivateData`` inside, the model is walked as ``model_dump()`` shows it, so field
    serializers and serialization aliases apply exactly as on the wire. With ``PrivateData``
    inside, dumping would flatten it into a plain dict the serializer no longer redacts, so
    the redaction step replaces it with its value-free descriptor first.
    """
    redacted = redact_private_data(model)
    if redacted is not model:
        return _as_container(redacted)
    with private_data_descriptors_only():
        dumped = cast('object', model.model_dump(mode='python'))
    return None if isinstance(dumped, BaseModel) else _as_container(dumped)


def _is_injected_field(key: object) -> bool:
    return isinstance(key, str) and key.strip().lower() in _INJECTED_DATA_FIELDS


def _sanitize_enrichment_payload(payload: EventPayload) -> EventPayload:
    redaction = payload.get('redaction')
    nested_enrichment = payload.get('enrichment')
    safe_payload = payload

    if isinstance(redaction, dict):
        sanitized_redaction = _sanitize_redaction_payload(cast('EventPayload', redaction))
        if sanitized_redaction is not redaction:
            safe_payload = _with_value(safe_payload, 'redaction', sanitized_redaction)

    if isinstance(nested_enrichment, dict):
        enrichment = cast('EventPayload', nested_enrichment)
        nested_redaction = enrichment.get('redaction')
        if isinstance(nested_redaction, dict):
            sanitized_nested_redaction = _sanitize_redaction_payload(
                cast('EventPayload', nested_redaction)
            )
            if sanitized_nested_redaction is not nested_redaction:
                updated_enrichment = dict(enrichment)
                updated_enrichment['redaction'] = sanitized_nested_redaction
                safe_payload = _with_value(safe_payload, 'enrichment', updated_enrichment)

    return safe_payload


def _sanitize_redaction_payload(redaction: EventPayload) -> EventPayload:
    changed = False
    safe_redaction: EventPayload = dict(redaction)

    for field_name in _REDACTION_DICT_FIELDS:
        field_value = redaction.get(field_name)
        if field_value is None:
            continue
        safe_redaction[field_name] = _mask_mapping_values(field_value)
        changed = True

    for field_name in _REDACTION_LIST_FIELDS:
        field_value = redaction.get(field_name)
        if field_value is None:
            continue
        safe_redaction[field_name] = _mask_sequence_values(field_value)
        changed = True

    return safe_redaction if changed else redaction


def _sanitize_error_payload(payload: EventPayload) -> EventPayload:
    safe_payload = _sanitize_error_fields(payload, nested_error_keys=('error_info',))
    details = safe_payload.get('details')
    if isinstance(details, dict) and details:
        safe_payload = _with_value(safe_payload, 'details', {})

    nested_error_info = safe_payload.get('error_info')
    if isinstance(nested_error_info, dict):
        error_info = cast('EventPayload', nested_error_info)
        if not error_info.get('details'):
            return safe_payload
        updated_error_info = dict(error_info)
        updated_error_info['details'] = {}
        safe_payload = _with_value(safe_payload, 'error_info', updated_error_info)

    return safe_payload


def _sanitize_error_fields(
    payload: EventPayload,
    *,
    nested_error_keys: tuple[str, ...],
) -> EventPayload:
    safe_payload = payload
    error_text = payload.get('error')
    if isinstance(error_text, str) and error_text.strip():
        safe_error = sanitize_user_facing_error_message(error_text.strip())
        if safe_error != error_text:
            safe_payload = _with_value(safe_payload, 'error', safe_error)

    for nested_key in nested_error_keys:
        nested_payload = payload.get(nested_key)
        if not isinstance(nested_payload, dict):
            continue
        nested = cast('EventPayload', nested_payload)
        nested_error = nested.get('error') or nested.get('message')
        if not isinstance(nested_error, str) or not nested_error.strip():
            continue
        safe_error = sanitize_user_facing_error_message(nested_error.strip())
        updated_nested = dict(nested)
        if safe_error != nested_error:
            if 'error' in nested:
                updated_nested['error'] = safe_error
            if 'message' in nested:
                updated_nested['message'] = safe_error
        message = updated_nested.get('message')
        if isinstance(message, str) and message.strip():
            # A safe error must not let a leaking message beside it through.
            safe_message = sanitize_user_facing_error_message(message.strip())
            if safe_message != message.strip():
                updated_nested['message'] = safe_message
        if updated_nested != nested:
            safe_payload = _with_value(safe_payload, nested_key, updated_nested)

    return safe_payload


# MARK: Summary and Masking Helpers


def _summarize_injected_payload(value: object) -> list[str]:
    if isinstance(value, dict):
        mapping = cast('dict[object, object]', value)
        return [str(key) for key in mapping.keys()]
    if isinstance(value, list):
        items = cast('list[object]', value)
        # A list cannot tell entry names from entry values, so only item types are shown.
        return [type(item).__name__ for item in items]
    if value is None:
        return []
    return [type(value).__name__]


def _mask_mapping_values(value: object) -> dict[str, str] | str:
    if isinstance(value, dict):
        mapping = cast('dict[object, object]', value)
        return {str(key): _REDACTED_VALUE for key in mapping.keys()}
    return _REDACTED_VALUE


def _mask_sequence_values(value: object) -> list[str]:
    if isinstance(value, list):
        items = cast('list[object]', value)
        return [_REDACTED_VALUE for _ in items]
    return [_REDACTED_VALUE]


def _with_value(payload: EventPayload, key: str, value: object) -> EventPayload:
    safe_payload = dict(payload)
    safe_payload[key] = value
    return safe_payload


# MARK: Public Exports

__all__ = ['BridgeAudience', 'EventBridgeSecurityPolicy']
