"""Public event contract for streaming SDK execution state into frontends.

Tiered API:

- **Tier 1 - Stream consumption**: ``normalize_stream()`` for read-only
  consumers (webhooks, logging, analytics).
- **Tier 1.5 - Stream replay**: ``forward_normalized_event()`` and
  ``forward_normalized_stream()`` for replaying normalized AppEvents into a
  reporter or UI bridge.
- **Tier 2 - Frontend bridge**: ``EventBridge`` for building SSE/WebSocket
  endpoints that forward events to a frontend.
- **Tier 3 - Builders**: ``build_*_payload()`` functions for custom reporters
  and advanced integrations.
"""

from __future__ import annotations

# Re-export event name constants from shared core
from maivn_shared.core.events import (
    ENRICHMENT_EVENT_NAME,
    ERROR_EVENT_NAME,
    FINAL_EVENT_NAME,
    INTERRUPT_REQUIRED_EVENT_NAME,
    MODEL_TOOL_COMPLETE_EVENT_NAME,
    PROGRESS_UPDATE_EVENT_NAME,
    STATUS_MESSAGE_EVENT_NAME,
    SYSTEM_TOOL_CHUNK_EVENT_NAME,
    SYSTEM_TOOL_COMPLETE_EVENT_NAME,
    SYSTEM_TOOL_ERROR_EVENT_NAME,
    SYSTEM_TOOL_START_EVENT_NAME,
    TOOL_EVENT_NAME,
    UPDATE_EVENT_NAME,
)

# Re-export builders from internal payloads module
from .._internal.utils.reporting.app_event_payloads import (
    APP_EVENT_CONTRACT_VERSION,
    build_agent_assignment_payload,
    build_assistant_chunk_payload,
    build_enrichment_payload,
    build_error_payload,
    build_final_payload,
    build_interrupt_required_payload,
    build_session_start_payload,
    build_status_message_payload,
    build_system_tool_chunk_payload,
    build_system_tool_complete_payload,
    build_system_tool_start_payload,
    build_tool_event_payload,
)

# Tier 2: Frontend bridge
from ._bridge import BridgeAudience, BridgeRegistry, EventBridge, EventBridgeSecurityPolicy, UIEvent

# Tier 1: Models and stream consumption
from ._forward import (
    NormalizedEventForwardingState,
    forward_normalized_event,
    forward_normalized_stream,
)
from ._models import (
    AppEvent,
    AssignmentDescriptor,
    AssistantDescriptor,
    ChunkDescriptor,
    EnrichmentDescriptor,
    ErrorInfoDescriptor,
    InterruptDescriptor,
    LifecycleDescriptor,
    NormalizedStreamState,
    OutputDescriptor,
    ParticipantDescriptor,
    RawSSEEvent,
    ScopeDescriptor,
    SessionDescriptor,
    ToolDescriptor,
)
from ._normalize import normalize_stream, normalize_stream_event

__all__ = [
    # Tier 1: Stream consumption
    "normalize_stream",
    "normalize_stream_event",
    "forward_normalized_event",
    "forward_normalized_stream",
    "AppEvent",
    "NormalizedEventForwardingState",
    "NormalizedStreamState",
    "RawSSEEvent",
    # Tier 2: Frontend bridge
    "BridgeAudience",
    "EventBridge",
    "EventBridgeSecurityPolicy",
    "UIEvent",
    "BridgeRegistry",
    # Tier 3: Builders (advanced)
    "APP_EVENT_CONTRACT_VERSION",
    "build_agent_assignment_payload",
    "build_assistant_chunk_payload",
    "build_enrichment_payload",
    "build_error_payload",
    "build_final_payload",
    "build_interrupt_required_payload",
    "build_session_start_payload",
    "build_status_message_payload",
    "build_system_tool_chunk_payload",
    "build_system_tool_complete_payload",
    "build_system_tool_start_payload",
    "build_tool_event_payload",
    # Descriptor models (reference types)
    "AssignmentDescriptor",
    "AssistantDescriptor",
    "ChunkDescriptor",
    "EnrichmentDescriptor",
    "ErrorInfoDescriptor",
    "InterruptDescriptor",
    "LifecycleDescriptor",
    "OutputDescriptor",
    "ParticipantDescriptor",
    "ScopeDescriptor",
    "SessionDescriptor",
    "ToolDescriptor",
    # Event name constants
    "ENRICHMENT_EVENT_NAME",
    "ERROR_EVENT_NAME",
    "FINAL_EVENT_NAME",
    "INTERRUPT_REQUIRED_EVENT_NAME",
    "MODEL_TOOL_COMPLETE_EVENT_NAME",
    "PROGRESS_UPDATE_EVENT_NAME",
    "STATUS_MESSAGE_EVENT_NAME",
    "SYSTEM_TOOL_CHUNK_EVENT_NAME",
    "SYSTEM_TOOL_COMPLETE_EVENT_NAME",
    "SYSTEM_TOOL_ERROR_EVENT_NAME",
    "SYSTEM_TOOL_START_EVENT_NAME",
    "TOOL_EVENT_NAME",
    "UPDATE_EVENT_NAME",
]
