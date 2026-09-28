"""Fail-closed projection from SDK stream events to public telemetry."""

from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING, Any, cast

from ._models import (
    TELEMETRY_SCHEMA_VERSION,
    RunTelemetryEvent,
    RunTelemetryScope,
    TokenUsageTelemetry,
    ToolCallTelemetry,
)

if TYPE_CHECKING:
    from collections.abc import Mapping

    from maivn._internal.models import JsonObject, StreamEvent

    from ._models import TelemetryEventKind

_TOOL_EVENTS = frozenset(
    {
        'agent.tool_selected',
        'model_tool_complete',
        'system_tool_start',
        'system_tool_chunk',
        'system_tool_complete',
        'system_tool_error',
        'tool.dispatch_completed',
        'tool.dispatch_started',
        'tool_event',
    }
)
_RUN_EVENTS = frozenset(
    {
        'agent.responded',
        'error',
        'final',
        'session.completed',
        # Compatibility names still emitted by current client streams.
        'session_complete',
        'session_start',
    }
)
_MESSAGE_EVENTS = frozenset(
    {
        'agent.thinking',
        'progress_update',
        'status_message',
        'status_message_chunk',
        'update',
        # Compatibility names used by normalized reporting consumers.
        'assistant_chunk',
        'response_chunk',
    }
)
_STATUS_EVENTS = frozenset(
    {
        'agent_assignment',
        'artifact.available',
        'artifact.deleted',
        'artifact.delete_incomplete',
        'artifact.deletion_pending_backup_expiry',
        'artifact.deletion_requested',
        'artifact.deletion_started',
        'artifact.delivery_failed',
        'artifact.delivery_queued',
        'artifact.delivery_started',
        'artifact.delivery_succeeded',
        'artifact.expired',
        'artifact.failed',
        'artifact.rejected',
        'artifact.reserved',
        'artifact.selected',
        'artifact.uploaded',
        'artifact.validation_started',
        'artifact.validated',
        'artifact.vault_authorization_denied',
        'artifact.vault_authorization_pending',
        'artifact.vault_render_failed',
        'artifact.vault_render_started',
        'assignment_completed',
        'assignment_received',
        'connection.event_received',
        'conversation.reply_received',
        'document_analysis_selected',
        'document_evidence_coverage',
        'email.received',
        'enrichment',
        'evaluating',
        'executing_actions',
        'executing_assignments',
        'finalizing',
        'heartbeat',
        'hook_fired',
        'interrupt_request',
        'interrupt_required',
        'loading_tools',
        'memory.record_changed',
        'memory_graph_extracting',
        'memory_indexed',
        'memory_indexing',
        'memory_insight_extracted',
        'memory_insight_extracting',
        'memory_retrieved',
        'memory_retrieving',
        'memory_skill_extracted',
        'memory_skill_extracting',
        'memory_summarized',
        'memory_summarizing',
        'message_redaction_applied',
        'model_routing',
        'planning',
        'planning_assignments',
        'redaction_previewed',
        'reevaluate_accrued',
        'resource_dedup_reused',
        'resource_extracted',
        'resource_extracting',
        'resource_registered',
        'resource_registering',
        'resource_version_superseded',
        'searching_tools',
        'status',
        'storage.object_uploaded',
        'synthesizing',
        'trigger.disabled',
        'trigger.enabled',
        'trigger.failed',
        'trigger.fire_requested',
        'trigger.fired',
        'trigger.skipped',
    }
)


def project_stream_event(event: StreamEvent) -> RunTelemetryEvent:
    """Project one customer-visible event through explicit metadata allowlists."""
    data = event.data
    event_kind = _event_kind(event.event_type)
    known = event_kind != 'unknown'
    payload: Mapping[str, Any] = _mapping(data.get('payload')) if known else {}
    duration_ms = _duration_ms(data, payload, event.event_type) if known else None
    return RunTelemetryEvent(
        schema_version=TELEMETRY_SCHEMA_VERSION,
        event_name=event.event_type,
        event_kind=event_kind,
        position=event.position,
        timestamp=_timestamp(data.get('ts')),
        event_id=_string(data.get('event_id')),
        session_id=_string(data.get('session_id')),
        thread_id=_string(data.get('thread_id')),
        parent_session_id=_string(data.get('parent_session_id')),
        root_event_id=_string(data.get('root_event_id')),
        correlation_id=_string(data.get('correlation_id')),
        status=_status(event.event_type, payload) if known else None,
        duration_ms=duration_ms,
        scope=_scope(payload) if known else None,
        tool_call=_tool_call(event.event_type, data, payload),
        token_usage=_token_usage(event.event_type, payload),
    )


def _event_kind(event_type: str) -> TelemetryEventKind:
    if event_type in _TOOL_EVENTS:
        return 'tool'
    if event_type in _RUN_EVENTS:
        return 'run'
    if event_type in _MESSAGE_EVENTS:
        return 'message'
    if event_type in _STATUS_EVENTS:
        return 'status'
    return 'unknown'


def _status(event_type: str, payload: Mapping[str, Any]) -> str | None:
    fixed = {
        'system_tool_start': 'started',
        'system_tool_complete': 'completed',
        'system_tool_error': 'error',
        'model_tool_complete': 'completed',
        'tool.dispatch_started': 'started',
        'tool.dispatch_completed': 'completed',
        'session_start': 'started',
        'session_complete': 'completed',
        'session.completed': 'completed',
        'final': 'completed',
        'error': 'error',
    }
    if event_type in fixed:
        return fixed[event_type]
    return _string(payload.get('status'))


def _scope(payload: Mapping[str, Any]) -> RunTelemetryScope | None:
    agent_name = _string(payload.get('agent_name'))
    swarm_name = _string(payload.get('swarm_name'))
    if agent_name is None and swarm_name is None:
        return None
    return RunTelemetryScope(agent_name=agent_name, swarm_name=swarm_name)


def _tool_call(
    event_type: str,
    data: Mapping[str, Any],
    payload: Mapping[str, Any],
) -> ToolCallTelemetry | None:
    if event_type not in _TOOL_EVENTS:
        return None
    tool_call = _mapping(payload.get('tool_call'))
    outcome = _mapping(payload.get('outcome'))
    spec_ref = _mapping(tool_call.get('spec_ref'))
    name = _string(payload.get('tool_name'))
    if name is None:
        name = _string(payload.get('name'))
    if name is None:
        return None
    return ToolCallTelemetry(
        call_id=(
            _string(tool_call.get('call_id'))
            or _string(outcome.get('call_id'))
            or _string(data.get('correlation_id'))
            or _string(data.get('event_id'))
        ),
        name=name,
        tool_type=_string(payload.get('tool_type')),
        namespace=_string(spec_ref.get('namespace')),
        version=_string(spec_ref.get('version')),
    )


def _duration_ms(
    data: Mapping[str, Any],
    payload: Mapping[str, Any],
    event_type: str,
) -> int | float | None:
    latency = _nonnegative_number(data.get('latency_ms'))
    if latency is not None:
        return latency
    direct = _nonnegative_number(payload.get('duration_ms'))
    if direct is not None:
        return direct
    if event_type in _TOOL_EVENTS:
        return _nonnegative_number(_mapping(payload.get('outcome')).get('duration_ms'))
    return None


def _token_usage(
    event_type: str,
    payload: Mapping[str, Any],
) -> TokenUsageTelemetry | None:
    if event_type not in {'final', 'session_complete', 'session.completed'}:
        return None
    usage = _mapping(payload.get('usage'))
    if not usage:
        return None
    input_tokens = _token_count(usage.get('input_tokens'))
    output_tokens = _token_count(usage.get('output_tokens'))
    return TokenUsageTelemetry(
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        cache_read_tokens=_token_count(
            usage.get('cache_read_tokens', usage.get('cache_read_input_tokens'))
        ),
        cache_creation_tokens=_token_count(
            usage.get('cache_creation_tokens', usage.get('cache_creation_input_tokens'))
        ),
        reasoning_tokens=_token_count(usage.get('reasoning_tokens')),
        total_tokens=input_tokens + output_tokens,
    )


def _mapping(value: object) -> Mapping[str, Any]:
    if isinstance(value, dict):
        return cast('JsonObject', value)
    return {}


def _string(value: object) -> str | None:
    return value if isinstance(value, str) and value else None


def _timestamp(value: object) -> datetime | None:
    if not isinstance(value, str) or not value:
        return None
    try:
        return datetime.fromisoformat(value.replace('Z', '+00:00'))
    except ValueError:
        return None


def _nonnegative_number(value: object) -> int | float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or value < 0:
        return None
    return value


def _token_count(value: object) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        return 0
    return value


__all__ = ['project_stream_event']
