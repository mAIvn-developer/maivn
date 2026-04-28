"""Security policy helpers for frontend-facing event bridges."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any, Literal

from ..._internal.core.orchestrator.helpers import sanitize_user_facing_error_message

BridgeAudience = Literal["internal", "frontend_safe"]

_REDACTED_VALUE = "<redacted>"
_INJECTED_DATA_FIELDS = frozenset({"private_data_injected", "interrupt_data_injected"})
_REDACTION_DICT_FIELDS = frozenset({"added_private_data", "merged_private_data"})
_REDACTION_LIST_FIELDS = frozenset({"matched_known_pii_values", "unmatched_known_pii_values"})
_KNOWN_FRONTEND_SAFE_EVENT_TYPES: frozenset[str] = frozenset(
    {
        "tool_event",
        "system_tool_complete",
        "system_tool_start",
        "system_tool_chunk",
        "agent_assignment",
        "final",
        "enrichment",
        "error",
        "assistant_chunk",
        "status_message",
        "interrupt_required",
        "session_start",
        "session_end",
    }
)
_logger = logging.getLogger("maivn.events._bridge.security")


def _validate_audience(value: str) -> BridgeAudience:
    normalized = value.strip().lower()
    if normalized not in {"internal", "frontend_safe"}:
        raise ValueError("EventBridge audience must be 'internal' or 'frontend_safe'")
    return normalized  # type: ignore[return-value]


@dataclass(frozen=True)
class EventBridgeSecurityPolicy:
    """Controls how much sensitive detail reaches bridge history and SSE."""

    audience: BridgeAudience = "internal"

    def __post_init__(self) -> None:
        object.__setattr__(self, "audience", _validate_audience(self.audience))

    def sanitize_event(self, event_type: str, data: dict[str, Any]) -> dict[str, Any]:
        """Return a bridge-safe payload for the configured audience.

        For ``frontend_safe`` bridges, unknown event types are not
        passed through verbatim. Instead they go through a generic
        injected-fields scrubber that summarizes any fields named in
        :data:`_INJECTED_DATA_FIELDS` and removes top-level error
        details. This is a defense-in-depth fallback so a third-party
        emitter pushing a custom event type cannot accidentally leak
        injected private data to the frontend.
        """
        if self.audience == "internal":
            return data

        normalized_event_type = event_type.strip().lower()
        safe_payload = data

        if normalized_event_type == "tool_event":
            safe_payload = _sanitize_tool_event_payload(safe_payload)
        elif normalized_event_type == "system_tool_complete":
            safe_payload = _sanitize_result_payload(safe_payload, nested_result_keys=("tool",))
        elif normalized_event_type == "agent_assignment":
            safe_payload = _sanitize_result_payload(
                safe_payload,
                nested_result_keys=("assignment",),
            )
            safe_payload = _sanitize_error_fields(
                safe_payload,
                nested_error_keys=("assignment",),
            )
        elif normalized_event_type == "final":
            safe_payload = _sanitize_result_payload(safe_payload, nested_result_keys=("output",))
        elif normalized_event_type == "enrichment":
            safe_payload = _sanitize_enrichment_payload(safe_payload)
        elif normalized_event_type == "error":
            safe_payload = _sanitize_error_payload(safe_payload)
        elif normalized_event_type not in _KNOWN_FRONTEND_SAFE_EVENT_TYPES:
            _logger.warning(
                "Unknown event type %r emitted to frontend_safe bridge; "
                "applying generic injected-fields sanitization. "
                "Add the type to _KNOWN_FRONTEND_SAFE_EVENT_TYPES or extend "
                "sanitize_event() if it needs custom handling.",
                event_type,
            )
            safe_payload = _sanitize_unknown_payload(safe_payload)

        return safe_payload


def _sanitize_unknown_payload(payload: dict[str, Any]) -> dict[str, Any]:
    """Best-effort scrub of an unknown event payload for frontend audiences.

    Walks the payload depth-first and summarizes any field whose name
    matches a known injected-data field (``private_data_injected``,
    ``interrupt_data_injected``). Leaves other fields intact so the
    frontend can still render the event, but prevents accidental
    exfiltration of injected secrets through custom event types.
    """
    return _scrub_injected_fields_recursive(payload)


def _scrub_injected_fields_recursive(value: Any) -> Any:
    if isinstance(value, dict):
        scrubbed: dict[str, Any] = {}
        changed = False
        for key, item in value.items():
            if key in _INJECTED_DATA_FIELDS:
                scrubbed[key] = _summarize_injected_payload(item)
                changed = True
                continue
            new_item = _scrub_injected_fields_recursive(item)
            if new_item is not item:
                changed = True
            scrubbed[key] = new_item
        return scrubbed if changed else value
    if isinstance(value, list):
        new_list = [_scrub_injected_fields_recursive(item) for item in value]
        if any(new is not orig for new, orig in zip(new_list, value, strict=True)):
            return new_list
        return value
    return value


def _sanitize_tool_event_payload(payload: dict[str, Any]) -> dict[str, Any]:
    safe_payload = _sanitize_result_payload(
        payload,
        arg_keys=("args",),
        nested_arg_keys=("tool",),
        nested_result_keys=("tool",),
    )
    return _sanitize_error_fields(safe_payload, nested_error_keys=("tool",))


def _sanitize_result_payload(
    payload: dict[str, Any],
    *,
    arg_keys: tuple[str, ...] = (),
    nested_arg_keys: tuple[str, ...] = (),
    nested_result_keys: tuple[str, ...] = (),
) -> dict[str, Any]:
    safe_payload = payload

    for arg_key in arg_keys:
        arg_value = safe_payload.get(arg_key)
        if isinstance(arg_value, dict):
            sanitized_args = _sanitize_injected_fields(arg_value)
            if sanitized_args is not arg_value:
                safe_payload = _with_value(safe_payload, arg_key, sanitized_args)

    result_value = safe_payload.get("result")
    if isinstance(result_value, dict):
        sanitized_result = _sanitize_injected_fields(result_value)
        if sanitized_result is not result_value:
            safe_payload = _with_value(safe_payload, "result", sanitized_result)

    for nested_key in nested_arg_keys:
        nested_payload = safe_payload.get(nested_key)
        if not isinstance(nested_payload, dict):
            continue
        nested_args = nested_payload.get("args")
        if not isinstance(nested_args, dict):
            continue
        sanitized_args = _sanitize_injected_fields(nested_args)
        if sanitized_args is nested_args:
            continue
        updated_nested = dict(nested_payload)
        updated_nested["args"] = sanitized_args
        safe_payload = _with_value(safe_payload, nested_key, updated_nested)

    for nested_key in nested_result_keys:
        nested_payload = safe_payload.get(nested_key)
        if not isinstance(nested_payload, dict):
            continue
        nested_result = nested_payload.get("result")
        if not isinstance(nested_result, dict):
            continue
        sanitized_result = _sanitize_injected_fields(nested_result)
        if sanitized_result is nested_result:
            continue
        updated_nested = dict(nested_payload)
        updated_nested["result"] = sanitized_result
        safe_payload = _with_value(safe_payload, nested_key, updated_nested)

    return safe_payload


def _sanitize_injected_fields(value: dict[str, Any]) -> dict[str, Any]:
    changed = False
    safe_value: dict[str, Any] = {}

    for key, item in value.items():
        if key in _INJECTED_DATA_FIELDS:
            safe_value[key] = _summarize_injected_payload(item)
            changed = True
            continue
        safe_value[key] = item

    return safe_value if changed else value


def _sanitize_enrichment_payload(payload: dict[str, Any]) -> dict[str, Any]:
    redaction = payload.get("redaction")
    nested_enrichment = payload.get("enrichment")
    safe_payload = payload

    if isinstance(redaction, dict):
        sanitized_redaction = _sanitize_redaction_payload(redaction)
        if sanitized_redaction is not redaction:
            safe_payload = _with_value(safe_payload, "redaction", sanitized_redaction)

    if isinstance(nested_enrichment, dict):
        nested_redaction = nested_enrichment.get("redaction")
        if isinstance(nested_redaction, dict):
            sanitized_nested_redaction = _sanitize_redaction_payload(nested_redaction)
            if sanitized_nested_redaction is not nested_redaction:
                updated_enrichment = dict(nested_enrichment)
                updated_enrichment["redaction"] = sanitized_nested_redaction
                safe_payload = _with_value(safe_payload, "enrichment", updated_enrichment)

    return safe_payload


def _sanitize_redaction_payload(redaction: dict[str, Any]) -> dict[str, Any]:
    changed = False
    safe_redaction: dict[str, Any] = dict(redaction)

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


def _sanitize_error_payload(payload: dict[str, Any]) -> dict[str, Any]:
    safe_payload = _sanitize_error_fields(payload, nested_error_keys=("error_info",))
    details = safe_payload.get("details")
    if isinstance(details, dict) and details:
        safe_payload = _with_value(safe_payload, "details", {})

    nested_error_info = safe_payload.get("error_info")
    if isinstance(nested_error_info, dict) and nested_error_info.get("details"):
        updated_error_info = dict(nested_error_info)
        updated_error_info["details"] = {}
        safe_payload = _with_value(safe_payload, "error_info", updated_error_info)

    return safe_payload


def _sanitize_error_fields(
    payload: dict[str, Any],
    *,
    nested_error_keys: tuple[str, ...],
) -> dict[str, Any]:
    safe_payload = payload
    error_text = payload.get("error")
    if isinstance(error_text, str) and error_text.strip():
        safe_error = sanitize_user_facing_error_message(error_text.strip())
        if safe_error != error_text:
            safe_payload = _with_value(safe_payload, "error", safe_error)

    for nested_key in nested_error_keys:
        nested_payload = payload.get(nested_key)
        if not isinstance(nested_payload, dict):
            continue
        nested_error = nested_payload.get("error") or nested_payload.get("message")
        if not isinstance(nested_error, str) or not nested_error.strip():
            continue
        safe_error = sanitize_user_facing_error_message(nested_error.strip())
        if safe_error == nested_error:
            continue
        updated_nested = dict(nested_payload)
        if "error" in nested_payload:
            updated_nested["error"] = safe_error
        if "message" in nested_payload:
            updated_nested["message"] = safe_error
        safe_payload = _with_value(safe_payload, nested_key, updated_nested)

    return safe_payload


def _summarize_injected_payload(value: Any) -> list[str]:
    if isinstance(value, dict):
        return [str(key) for key in value.keys()]
    if isinstance(value, list):
        return [str(item) for item in value]
    if value is None:
        return []
    return [type(value).__name__]


def _mask_mapping_values(value: Any) -> dict[str, str] | str:
    if isinstance(value, dict):
        return {str(key): _REDACTED_VALUE for key in value.keys()}
    return _REDACTED_VALUE


def _mask_sequence_values(value: Any) -> list[str]:
    if isinstance(value, list):
        return [_REDACTED_VALUE for _ in value]
    return [_REDACTED_VALUE]


def _with_value(payload: dict[str, Any], key: str, value: Any) -> dict[str, Any]:
    safe_payload = dict(payload)
    safe_payload[key] = value
    return safe_payload


__all__ = ["BridgeAudience", "EventBridgeSecurityPolicy"]
