"""Stateful projection from canonical v2 events to the v1 SDK event surface.

``StreamEvent.event_type`` and ``StreamEvent.data`` intentionally stay canonical because
the local tool runtime and invocation response builder depend on their v2 vocabulary.
Only ``StreamEvent.name`` and ``StreamEvent.payload`` expose this compatibility view.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, cast

from maivn._internal.error_diagnostics import diagnostic_facts
from maivn._internal.event_message import message_from_event
from maivn._internal.models import InvokeResponse, JsonObject, StreamEvent
from maivn._internal.private_placeholders import project_private_restorations

if TYPE_CHECKING:
    from collections.abc import Mapping

# MARK: Stream Projection


class StreamEventProjector:
    """Accumulate streamed content and attach v1-visible event metadata per stream."""

    def __init__(self) -> None:
        """Initialize cumulative content state for one stream iterator."""
        self._content_by_message_id: dict[str, str] = {}

    def project(self, event: StreamEvent) -> StreamEvent:
        """Return ``event`` with its v1-visible name and payload attached."""
        if event.event_type == 'final':
            return _project_final_event(event)
        if event.event_type == 'error':
            return _project_error_event(event)
        if event.event_type != 'status_message_chunk':
            return event.model_copy(update={'compat_name': event.event_type})

        payload = _native_payload(event)
        message_id = _message_id(payload, event.position)
        content_delta = payload.get('content_delta')
        delta = content_delta if isinstance(content_delta, str) else ''
        streaming_content = self._content_by_message_id.get(message_id, '') + delta
        self._content_by_message_id[message_id] = streaming_content
        projected_payload = dict(payload)
        projected_payload['streaming_content'] = streaming_content
        projected_payload.setdefault('assistant_id', message_id)
        return event.model_copy(
            update={
                'compat_name': 'update',
                'compat_payload': projected_payload,
            }
        )


def _native_payload(event: StreamEvent) -> JsonObject:
    """Return the canonical payload mapping without copying ordinary events."""
    payload = event.data.get('payload')
    return cast('JsonObject', payload) if isinstance(payload, dict) else event.data


# MARK: Terminal Payloads


def _message_id(payload: JsonObject, position: int) -> str:
    """Choose the v2 message key used to isolate cumulative stream content."""
    value = payload.get('message_id')
    return value if isinstance(value, str) and value else f'event-{position}'


def _project_final_event(event: StreamEvent) -> StreamEvent:
    """Expose v1 final response and token-usage fields while retaining v2 data."""
    payload = _native_payload(event)
    projected_payload = dict(payload)
    response = _response_text(payload)
    projected_payload['response'] = response
    projected_payload['responses'] = [response] if response else []
    spans = payload.get('private_value_restorations')
    if isinstance(spans, list):
        spans = cast('list[object]', spans)
        projected_payload['private_value_restorations'] = cast(
            'Any',
            [
                *spans,
                *project_private_restorations(
                    spans, '/message/content', '/response', length=len(response)
                ),
                *project_private_restorations(
                    spans, '/message/content', '/responses/0', length=len(response)
                ),
            ],
        )
    token_usage = _token_usage(event, payload)
    if token_usage is not None:
        projected_payload['token_usage'] = token_usage
    return event.model_copy(
        update={
            'compat_name': 'final',
            'compat_payload': projected_payload,
        }
    )


def _project_error_event(event: StreamEvent) -> StreamEvent:
    """Expose the v1 error and details fields for a canonical v2 terminal error."""
    payload = _native_payload(event)
    projected_payload = dict(payload)
    # Durable errors intentionally omit provider text. Retain their stable code
    # so legacy consumers still receive an actionable failure without private text.
    projected_payload['error'] = next(
        (
            value
            for key in ('message', 'error_code')
            if isinstance(value := payload.get(key), str) and value.strip()
        ),
        'Unknown error',
    )
    projected_payload['details'] = {
        'error_type': payload.get('error_type') or payload.get('error_class'),
        'termination_reason': payload.get('termination_reason'),
        **diagnostic_facts(payload, event.data),
    }
    return event.model_copy(
        update={
            'compat_name': 'error',
            'compat_payload': projected_payload,
        }
    )


def _response_text(payload: JsonObject) -> str:
    """Extract the v1 final response text from the canonical v2 message payload."""
    message = payload.get('message')
    if isinstance(message, dict):
        mapping = cast('Mapping[str, object]', message)
        content = mapping.get('content')
        if isinstance(content, str):
            return content
    return ''


def _token_usage(event: StreamEvent, payload: JsonObject) -> JsonObject | None:
    """Reuse ``InvokeResponse.token_usage`` for v1 terminal token field mapping."""
    message = payload.get('message')
    usage = payload.get('usage')
    if not isinstance(message, dict) or not isinstance(usage, dict):
        return None
    response = InvokeResponse(
        final_message=message_from_event(cast('Mapping[str, object]', message), event.data),
        session_id='compat-session',
        root_event_id='compat-root-event',
        event_positions=[event.position],
        usage=cast('JsonObject', usage),
        response=_response_text(payload),
    )
    return response.token_usage


__all__ = ['StreamEventProjector']
