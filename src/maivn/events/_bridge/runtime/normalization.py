from __future__ import annotations

from typing import Any

from ...._internal.utils.reporting.app_event_payloads import (
    build_agent_assignment_payload,
    build_assistant_chunk_payload,
    build_enrichment_payload,
    build_error_payload,
    build_final_payload,
    build_interrupt_required_payload,
    build_status_message_payload,
    build_system_tool_chunk_payload,
    build_system_tool_complete_payload,
    build_system_tool_start_payload,
    build_tool_event_payload,
)
from .helpers import (
    build_fallback_id,
    coerce_mapping,
    coerce_text,
    merge_extra_fields,
    normalize_key_part,
    normalize_text,
)
from .identity import AssignmentAndScopeResolver, BridgeIdentityState, ToolIdentityResolver

# MARK: Payload Normalization


class BridgePayloadNormalizer:
    def __init__(self, identity_state: BridgeIdentityState) -> None:
        self._identity_state = identity_state
        self._tool_resolver = ToolIdentityResolver(identity_state)
        self._assignment_scope_resolver = AssignmentAndScopeResolver(identity_state)

    def normalize_payload(self, event_type: str, data: dict[str, Any]) -> dict[str, Any]:
        normalized_event_type = normalize_key_part(event_type) or event_type
        participant_key = normalize_text(data.get("participant_key"))
        participant_name = normalize_text(data.get("participant_name"))
        participant_role = normalize_text(data.get("participant_role"))

        if normalized_event_type == "tool_event":
            tool_payload = coerce_mapping(data.get("tool")) or {}
            tool_name = normalize_text(data.get("tool_name")) or normalize_text(
                tool_payload.get("name")
            )
            tool_id = normalize_text(data.get("tool_id")) or normalize_text(tool_payload.get("id"))
            status = normalize_text(data.get("status")) or normalize_text(
                tool_payload.get("status")
            )
            tool_type = normalize_text(data.get("tool_type")) or normalize_text(
                tool_payload.get("type")
            )
            args = coerce_mapping(data.get("args")) or coerce_mapping(tool_payload.get("args"))
            result = data.get("result", tool_payload.get("result"))
            error = normalize_text(data.get("error")) or normalize_text(tool_payload.get("error"))
            agent_name = normalize_text(data.get("agent_name"))
            swarm_name = normalize_text(data.get("swarm_name"))
            canonical_tool_id = self._tool_resolver.resolve_tool_id(
                tool_name=tool_name or "tool",
                tool_id=tool_id or build_fallback_id("tool", tool_name),
                status=status or "executing",
                args=args,
                agent_name=agent_name,
                swarm_name=swarm_name,
                tool_type=tool_type,
            )
            return merge_extra_fields(
                build_tool_event_payload(
                    tool_name=tool_name or "tool",
                    tool_id=canonical_tool_id,
                    status=status or "executing",
                    args=args,
                    result=result,
                    error=error,
                    agent_name=agent_name,
                    swarm_name=swarm_name,
                    tool_type=tool_type,
                    participant_key=participant_key,
                    participant_name=participant_name,
                    participant_role=participant_role,
                ),
                data,
            )

        if normalized_event_type == "system_tool_start":
            tool_payload = coerce_mapping(data.get("tool")) or {}
            tool_type = normalize_text(data.get("tool_type")) or normalize_text(
                tool_payload.get("name")
            )
            tool_id = normalize_text(data.get("tool_id")) or normalize_text(tool_payload.get("id"))
            params = coerce_mapping(data.get("params")) or coerce_mapping(tool_payload.get("args"))
            agent_name = normalize_text(data.get("agent_name"))
            swarm_name = normalize_text(data.get("swarm_name"))
            canonical_tool_id = self._tool_resolver.resolve_tool_id(
                tool_name=tool_type or "system_tool",
                tool_id=tool_id or build_fallback_id("system_tool", tool_type),
                status="executing",
                args=params,
                agent_name=agent_name,
                swarm_name=swarm_name,
                tool_type="system",
            )
            return merge_extra_fields(
                build_system_tool_start_payload(
                    tool_type=tool_type or "system_tool",
                    tool_id=canonical_tool_id,
                    params=params,
                    agent_name=agent_name,
                    swarm_name=swarm_name,
                ),
                data,
            )

        if normalized_event_type == "system_tool_chunk":
            tool_payload = coerce_mapping(data.get("tool")) or {}
            tool_id = normalize_text(data.get("tool_id")) or normalize_text(tool_payload.get("id"))
            canonical_tool_id = self._identity_state.tool_id_aliases.get(
                tool_id or "",
                tool_id or "system_tool",
            )
            return merge_extra_fields(
                build_system_tool_chunk_payload(
                    tool_id=canonical_tool_id,
                    text=coerce_text(data.get("text")) or "",
                    progress=data.get("progress")
                    if isinstance(data.get("progress"), (int, float))
                    else None,
                ),
                data,
            )

        if normalized_event_type == "system_tool_complete":
            tool_payload = coerce_mapping(data.get("tool")) or {}
            tool_id = normalize_text(data.get("tool_id")) or normalize_text(tool_payload.get("id"))
            canonical_tool_id = self._identity_state.tool_id_aliases.get(
                tool_id or "",
                tool_id or "system_tool",
            )
            return merge_extra_fields(
                build_system_tool_complete_payload(
                    tool_id=canonical_tool_id,
                    result=data.get("result", tool_payload.get("result")),
                ),
                data,
            )

        if normalized_event_type == "assistant_chunk":
            assistant_payload = coerce_mapping(data.get("assistant")) or {}
            return merge_extra_fields(
                build_assistant_chunk_payload(
                    assistant_id=normalize_text(data.get("assistant_id"))
                    or normalize_text(assistant_payload.get("id"))
                    or "assistant",
                    text=coerce_text(data.get("text"))
                    or coerce_text(assistant_payload.get("delta"))
                    or "",
                    participant_key=participant_key,
                    participant_name=participant_name,
                    participant_role=participant_role,
                ),
                data,
            )

        if normalized_event_type == "status_message":
            status_payload = coerce_mapping(data.get("status")) or {}
            assistant_payload = coerce_mapping(data.get("assistant")) or {}
            return merge_extra_fields(
                build_status_message_payload(
                    assistant_id=normalize_text(data.get("assistant_id"))
                    or normalize_text(assistant_payload.get("id"))
                    or "assistant",
                    message=coerce_text(data.get("message"))
                    or coerce_text(status_payload.get("message"))
                    or "",
                ),
                data,
            )

        if normalized_event_type == "interrupt_required":
            interrupt_payload = coerce_mapping(data.get("interrupt")) or {}
            return merge_extra_fields(
                build_interrupt_required_payload(
                    interrupt_id=normalize_text(data.get("interrupt_id"))
                    or normalize_text(interrupt_payload.get("id"))
                    or build_fallback_id("interrupt"),
                    data_key=normalize_text(data.get("data_key"))
                    or normalize_text(interrupt_payload.get("data_key"))
                    or "input",
                    prompt=coerce_text(data.get("prompt"))
                    or coerce_text(interrupt_payload.get("prompt"))
                    or "Input required.",
                    tool_name=normalize_text(data.get("tool_name"))
                    or normalize_text(interrupt_payload.get("tool_name")),
                    arg_name=normalize_text(data.get("arg_name"))
                    or normalize_text(interrupt_payload.get("arg_name")),
                    checkpoint_id=normalize_text(data.get("checkpoint_id"))
                    or normalize_text(interrupt_payload.get("checkpoint_id")),
                    assignment_id=normalize_text(data.get("assignment_id"))
                    or normalize_text(interrupt_payload.get("assignment_id")),
                    interrupt_number=data.get("interrupt_number")
                    if isinstance(data.get("interrupt_number"), int)
                    else interrupt_payload.get("number")
                    if isinstance(interrupt_payload.get("number"), int)
                    else None,
                    total_interrupts=data.get("total_interrupts")
                    if isinstance(data.get("total_interrupts"), int)
                    else interrupt_payload.get("total")
                    if isinstance(interrupt_payload.get("total"), int)
                    else None,
                    input_type=normalize_text(data.get("input_type"))
                    or normalize_text(interrupt_payload.get("input_type")),
                    choices=data.get("choices")
                    if isinstance(data.get("choices"), list)
                    else interrupt_payload.get("choices")
                    if isinstance(interrupt_payload.get("choices"), list)
                    else None,
                    timestamp=normalize_text(data.get("timestamp")),
                ),
                data,
            )

        if normalized_event_type == "agent_assignment":
            assignment_payload = coerce_mapping(data.get("assignment")) or {}
            agent_name = normalize_text(data.get("agent_name")) or normalize_text(
                assignment_payload.get("agent_name")
            )
            swarm_name = normalize_text(data.get("swarm_name")) or normalize_text(
                assignment_payload.get("swarm_name")
            )
            canonical_assignment_id = self._assignment_scope_resolver.resolve_agent_assignment_id(
                agent_name=agent_name or "unknown-agent",
                assignment_id=normalize_text(data.get("assignment_id"))
                or normalize_text(assignment_payload.get("id")),
                swarm_name=swarm_name,
            )
            return merge_extra_fields(
                build_agent_assignment_payload(
                    agent_name=agent_name or "unknown-agent",
                    status=normalize_text(data.get("status"))
                    or normalize_text(assignment_payload.get("status"))
                    or "in_progress",
                    assignment_id=canonical_assignment_id,
                    swarm_name=swarm_name,
                    task=normalize_text(data.get("task"))
                    or normalize_text(assignment_payload.get("task")),
                    error=normalize_text(data.get("error"))
                    or normalize_text(assignment_payload.get("error")),
                    result=data.get("result", assignment_payload.get("result")),
                    participant_key=participant_key,
                    participant_name=participant_name,
                    participant_role=participant_role,
                ),
                data,
            )

        if normalized_event_type == "enrichment":
            enrichment_payload = coerce_mapping(data.get("enrichment")) or {}
            scope_id = self._assignment_scope_resolver.resolve_scope_id(
                scope_id=normalize_text(data.get("scope_id")),
                scope_name=normalize_text(data.get("scope_name")),
                scope_type=normalize_text(data.get("scope_type")),
            )
            return merge_extra_fields(
                build_enrichment_payload(
                    phase=normalize_text(data.get("phase"))
                    or normalize_text(enrichment_payload.get("phase"))
                    or "enrichment",
                    message=normalize_text(data.get("message"))
                    or normalize_text(enrichment_payload.get("message"))
                    or "Enrichment update",
                    scope_id=scope_id,
                    scope_name=normalize_text(data.get("scope_name")),
                    scope_type=normalize_text(data.get("scope_type")),
                    memory=coerce_mapping(data.get("memory"))
                    or coerce_mapping(enrichment_payload.get("memory")),
                    redaction=coerce_mapping(data.get("redaction"))
                    or coerce_mapping(enrichment_payload.get("redaction")),
                    participant_key=participant_key,
                    participant_name=participant_name,
                    participant_role=participant_role,
                ),
                data,
            )

        if normalized_event_type == "final":
            output_payload = coerce_mapping(data.get("output")) or {}
            responses = data.get("responses")
            response = coerce_text(data.get("response")) or coerce_text(
                output_payload.get("response")
            )
            if response is None and isinstance(responses, list) and responses:
                first_response = responses[0]
                if isinstance(first_response, str):
                    response = first_response
            return merge_extra_fields(
                build_final_payload(
                    response=response or "",
                    result=data.get("result", output_payload.get("result")),
                    token_usage=coerce_mapping(data.get("token_usage"))
                    or coerce_mapping(output_payload.get("token_usage")),
                ),
                data,
            )

        if normalized_event_type == "error":
            error_info_payload = coerce_mapping(data.get("error_info")) or {}
            return merge_extra_fields(
                build_error_payload(
                    error=normalize_text(data.get("error"))
                    or normalize_text(error_info_payload.get("message"))
                    or "Unknown error",
                    details=coerce_mapping(data.get("details"))
                    or coerce_mapping(error_info_payload.get("details")),
                ),
                data,
            )

        return data
