"""Shared event-name constants used across Maivn services and SDK."""

from __future__ import annotations

from typing import Final

# MARK: - Tool Events

TOOL_EVENT_NAME: Final[str] = 'tool_event'
"""Primary event name emitted by the server bridge and consumed by the SDK."""

INTERRUPT_REQUEST_EVENT_NAME: Final[str] = 'interrupt_request'
"""Interrupt request event emitted when a tool requires user input."""

INTERRUPT_REQUIRED_EVENT_NAME: Final[str] = 'interrupt_required'
"""Interrupt required event indicating user action is needed."""

# MARK: - Assignment Events

ASSIGNMENT_RECEIVED_EVENT_NAME: Final[str] = 'assignment_received'
"""Emitted when an assignment is received by an agent."""

ASSIGNMENT_COMPLETED_EVENT_NAME: Final[str] = 'assignment_completed'
"""Emitted when an assignment is completed by an agent."""

# MARK: - Model Events

MODEL_TOOL_COMPLETE_EVENT_NAME: Final[str] = 'model_tool_complete'
"""Emitted when a model tool call completes."""

# MARK: - Lifecycle Events

HEARTBEAT_EVENT_NAME: Final[str] = 'heartbeat'
"""Periodic heartbeat event for connection health."""

UPDATE_EVENT_NAME: Final[str] = 'update'
"""Progress update event during execution."""

PROGRESS_UPDATE_EVENT_NAME: Final[str] = 'progress_update'
"""Progress update event for streamed response content during execution."""

STATUS_MESSAGE_EVENT_NAME: Final[str] = 'status_message'
"""Standalone status message emitted at swarm lifecycle milestones.

Unlike ``progress_update`` (which carries streaming deltas that accumulate
into a single response), a ``status_message`` is a self-contained,
human-readable notification (e.g. "Dispatching 3 agents: ...").  Clients
should display each one as its own UI element rather than appending to
the assistant response stream.

Payload: ``{"message": str, "assistant_id": str}``
"""

STATUS_MESSAGE_CHUNK_EVENT_NAME: Final[str] = 'status_message_chunk'
"""Incremental chunk for an in-progress status message.

Clients that render live status cards can accumulate ``text`` deltas by
``status_id`` and finalize the card when the matching ``status_message`` arrives.

Payload: ``{"status_id": str, "assistant_id": str, "text": str}``
"""

FINAL_EVENT_NAME: Final[str] = 'final'
"""Final event indicating execution completion."""

ERROR_EVENT_NAME: Final[str] = 'error'
"""Error event indicating execution failure."""

# MARK: - System Tool Events

SYSTEM_TOOL_START_EVENT_NAME: Final[str] = 'system_tool_start'
"""Emitted when a system tool (web_search, repl, think) starts execution."""

SYSTEM_TOOL_CHUNK_EVENT_NAME: Final[str] = 'system_tool_chunk'
"""Emitted when a system tool produces a streaming chunk during execution."""

SYSTEM_TOOL_COMPLETE_EVENT_NAME: Final[str] = 'system_tool_complete'
"""Emitted when a system tool completes execution."""

SYSTEM_TOOL_ERROR_EVENT_NAME: Final[str] = 'system_tool_error'
"""Emitted when a system tool encounters an error."""

# MARK: - Hook Events

HOOK_FIRED_EVENT_NAME: Final[str] = 'hook_fired'
"""Emitted each time a developer-registered scope or tool hook callback fires.

Carries the hook's scope ({tool, agent, swarm}), the firing stage
(``"before"`` / ``"after"``), the callable's display name, and either
``"completed"`` or ``"failed"`` plus an optional error message. The SDK
emits one event per hook execution so multiple hooks on the same target
each surface their own marker in the UI.
"""

# MARK: - Enrichment Events

ENRICHMENT_EVENT_NAME: Final[str] = 'enrichment'
"""Emitted when an agent transitions to a new execution phase (evaluating, planning, etc.)."""

# MARK: - Processing (request-lifecycle) Phases

EVALUATING_ENRICHMENT_PHASE: Final[str] = 'evaluating'
"""Enrichment phase emitted before orchestrator routing / on re-evaluation."""

PLANNING_ENRICHMENT_PHASE: Final[str] = 'planning'
"""Enrichment phase emitted when the orchestrator routes to action generation."""

SEARCHING_TOOLS_ENRICHMENT_PHASE: Final[str] = 'searching_tools'
"""Enrichment phase emitted while the tool-store agent searches for tools."""

LOADING_TOOLS_ENRICHMENT_PHASE: Final[str] = 'loading_tools'
"""Enrichment phase emitted while the assignment agent loads/discovers tools."""

PLANNING_ASSIGNMENTS_ENRICHMENT_PHASE: Final[str] = 'planning_assignments'
"""Enrichment phase emitted while the assignment agent plans assignments."""

EXECUTING_ASSIGNMENTS_ENRICHMENT_PHASE: Final[str] = 'executing_assignments'
"""Enrichment phase emitted when a nested (depth>0) assignment executor starts."""

EXECUTING_ACTIONS_ENRICHMENT_PHASE: Final[str] = 'executing_actions'
"""Enrichment phase emitted when the top-level (depth=0) executor starts."""

SYNTHESIZING_ENRICHMENT_PHASE: Final[str] = 'synthesizing'
"""Enrichment phase emitted while the orchestrator synthesizes the final response."""

FINALIZING_ENRICHMENT_PHASE: Final[str] = 'finalizing'
"""Enrichment phase emitted after execution completes while the final response is
produced. Fills the gap between the last execution phase and ``turn_complete`` on
paths that skip orchestrator synthesis (structured output, force-final single
MODEL tool, predetermined force-final routes) so the UI does not appear stuck on
``executing_actions``."""

REEVALUATE_ACCRUED_ENRICHMENT_PHASE: Final[str] = 'reevaluate_accrued'
"""Enrichment phase emitted each time a ``reevaluate`` system tool runs and triggers
a re-plan. Carries source attribution (dependency vs llm) under ``reevaluate``."""

PROCESSING_ENRICHMENT_MESSAGES: Final[dict[str, str]] = {
    EVALUATING_ENRICHMENT_PHASE: 'Evaluating request...',
    PLANNING_ENRICHMENT_PHASE: 'Planning actions...',
    SEARCHING_TOOLS_ENRICHMENT_PHASE: 'Searching for tools...',
    LOADING_TOOLS_ENRICHMENT_PHASE: 'Loading tools...',
    PLANNING_ASSIGNMENTS_ENRICHMENT_PHASE: 'Planning assignments...',
    EXECUTING_ASSIGNMENTS_ENRICHMENT_PHASE: 'Executing assignments...',
    EXECUTING_ACTIONS_ENRICHMENT_PHASE: 'Executing actions...',
    SYNTHESIZING_ENRICHMENT_PHASE: 'Synthesizing response...',
    FINALIZING_ENRICHMENT_PHASE: 'Finalizing response...',
}
"""Single source of truth for processing-phase display messages. Consumed by the
server (``invoker``/``executor.streaming``) and SDK so the three former copies of
this map cannot drift."""

PROCESSING_ENRICHMENT_PHASES: Final[tuple[str, ...]] = tuple(PROCESSING_ENRICHMENT_MESSAGES.keys())
"""Canonical processing (request-lifecycle) enrichment phase identifiers."""

MEMORY_SUMMARIZING_ENRICHMENT_PHASE: Final[str] = 'memory_summarizing'
"""Enrichment phase emitted before memory summarize-mode execution."""

MEMORY_SUMMARIZED_ENRICHMENT_PHASE: Final[str] = 'memory_summarized'
"""Enrichment phase emitted after memory summarize-mode execution completes."""

MEMORY_RETRIEVING_ENRICHMENT_PHASE: Final[str] = 'memory_retrieving'
"""Enrichment phase emitted before memory retrieve-mode execution."""

MEMORY_RETRIEVED_ENRICHMENT_PHASE: Final[str] = 'memory_retrieved'
"""Enrichment phase emitted after memory retrieval completes."""

MEMORY_INDEXING_ENRICHMENT_PHASE: Final[str] = 'memory_indexing'
"""Enrichment phase emitted before memory index-mode execution."""

MEMORY_INDEXED_ENRICHMENT_PHASE: Final[str] = 'memory_indexed'
"""Enrichment phase emitted after memory index-mode execution completes."""

MEMORY_GRAPH_EXTRACTING_ENRICHMENT_PHASE: Final[str] = 'memory_graph_extracting'
"""Enrichment phase emitted before graph/entity extraction in index-mode."""

MEMORY_SKILL_EXTRACTING_ENRICHMENT_PHASE: Final[str] = 'memory_skill_extracting'
"""Enrichment phase emitted while post-run skill extraction is in progress."""

MEMORY_INSIGHT_EXTRACTING_ENRICHMENT_PHASE: Final[str] = 'memory_insight_extracting'
"""Enrichment phase emitted while post-run insight extraction is in progress."""

MEMORY_SKILL_EXTRACTED_ENRICHMENT_PHASE: Final[str] = 'memory_skill_extracted'
"""Enrichment phase emitted after post-run skill extraction completes."""

MEMORY_INSIGHT_EXTRACTED_ENRICHMENT_PHASE: Final[str] = 'memory_insight_extracted'
"""Enrichment phase emitted after post-run insight extraction completes."""

MEMORY_ENRICHMENT_PHASES: Final[tuple[str, ...]] = (
    MEMORY_SUMMARIZING_ENRICHMENT_PHASE,
    MEMORY_SUMMARIZED_ENRICHMENT_PHASE,
    MEMORY_RETRIEVING_ENRICHMENT_PHASE,
    MEMORY_RETRIEVED_ENRICHMENT_PHASE,
    MEMORY_INDEXING_ENRICHMENT_PHASE,
    MEMORY_INDEXED_ENRICHMENT_PHASE,
    MEMORY_GRAPH_EXTRACTING_ENRICHMENT_PHASE,
    MEMORY_SKILL_EXTRACTING_ENRICHMENT_PHASE,
    MEMORY_INSIGHT_EXTRACTING_ENRICHMENT_PHASE,
    MEMORY_SKILL_EXTRACTED_ENRICHMENT_PHASE,
    MEMORY_INSIGHT_EXTRACTED_ENRICHMENT_PHASE,
)
"""Canonical memory-related enrichment phase identifiers."""

REDACTION_PREVIEWED_ENRICHMENT_PHASE: Final[str] = 'redaction_previewed'
"""Enrichment phase emitted when an explicit redaction preview completes."""

MESSAGE_REDACTION_APPLIED_ENRICHMENT_PHASE: Final[str] = 'message_redaction_applied'
"""Enrichment phase emitted when message redaction is applied during normal execution setup."""

REDACTION_ENRICHMENT_PHASES: Final[tuple[str, ...]] = (
    REDACTION_PREVIEWED_ENRICHMENT_PHASE,
    MESSAGE_REDACTION_APPLIED_ENRICHMENT_PHASE,
)
"""Canonical redaction-related enrichment phase identifiers."""

RESOURCE_REGISTERING_ENRICHMENT_PHASE: Final[str] = 'resource_registering'
"""Enrichment phase emitted when resource registration begins."""

RESOURCE_REGISTERED_ENRICHMENT_PHASE: Final[str] = 'resource_registered'
"""Enrichment phase emitted when resource registration completes."""

RESOURCE_DEDUP_REUSED_ENRICHMENT_PHASE: Final[str] = 'resource_dedup_reused'
"""Enrichment phase emitted when existing resource content is reused (dedup)."""

RESOURCE_VERSION_SUPERSEDED_ENRICHMENT_PHASE: Final[str] = 'resource_version_superseded'
"""Enrichment phase emitted when a previous resource version is superseded."""

RESOURCE_EXTRACTING_ENRICHMENT_PHASE: Final[str] = 'resource_extracting'
"""Enrichment phase emitted when resource extraction starts."""

RESOURCE_EXTRACTED_ENRICHMENT_PHASE: Final[str] = 'resource_extracted'
"""Enrichment phase emitted when resource extraction completes."""

RESOURCE_ENRICHMENT_PHASES: Final[tuple[str, ...]] = (
    RESOURCE_REGISTERING_ENRICHMENT_PHASE,
    RESOURCE_REGISTERED_ENRICHMENT_PHASE,
    RESOURCE_DEDUP_REUSED_ENRICHMENT_PHASE,
    RESOURCE_VERSION_SUPERSEDED_ENRICHMENT_PHASE,
    RESOURCE_EXTRACTING_ENRICHMENT_PHASE,
    RESOURCE_EXTRACTED_ENRICHMENT_PHASE,
)
"""Canonical resource-related enrichment phase identifiers."""

KNOWN_ENRICHMENT_PHASES: Final[frozenset[str]] = frozenset(
    (
        *PROCESSING_ENRICHMENT_PHASES,
        REEVALUATE_ACCRUED_ENRICHMENT_PHASE,
        *MEMORY_ENRICHMENT_PHASES,
        *REDACTION_ENRICHMENT_PHASES,
        *RESOURCE_ENRICHMENT_PHASES,
    )
)
"""Every enrichment phase the platform is expected to emit. Used to warn on
unknown / misspelled phases before they reach consumers."""


def resolve_enrichment_message(phase: str, message: str | None = None) -> str:
    """Resolve the display message for an enrichment ``phase``.

    Prefers an explicit ``message`` (carried by direct emits such as memory /
    redaction / reevaluate phases), then the canonical processing-phase map,
    then falls back to the raw phase string. Centralizing this keeps the
    server's stream-detection and direct-emit paths consistent.
    """
    if message:
        return message
    return PROCESSING_ENRICHMENT_MESSAGES.get(phase, phase)


def is_known_enrichment_phase(phase: str) -> bool:
    """Return True when ``phase`` is a recognized enrichment phase identifier."""
    return phase in KNOWN_ENRICHMENT_PHASES


__all__ = [
    # Tool Events
    'TOOL_EVENT_NAME',
    'INTERRUPT_REQUEST_EVENT_NAME',
    'INTERRUPT_REQUIRED_EVENT_NAME',
    # Assignment Events
    'ASSIGNMENT_RECEIVED_EVENT_NAME',
    'ASSIGNMENT_COMPLETED_EVENT_NAME',
    # Model Events
    'MODEL_TOOL_COMPLETE_EVENT_NAME',
    # Lifecycle Events
    'HEARTBEAT_EVENT_NAME',
    'UPDATE_EVENT_NAME',
    'PROGRESS_UPDATE_EVENT_NAME',
    'STATUS_MESSAGE_EVENT_NAME',
    'STATUS_MESSAGE_CHUNK_EVENT_NAME',
    'FINAL_EVENT_NAME',
    'ERROR_EVENT_NAME',
    # System Tool Events
    'SYSTEM_TOOL_START_EVENT_NAME',
    'SYSTEM_TOOL_CHUNK_EVENT_NAME',
    'SYSTEM_TOOL_COMPLETE_EVENT_NAME',
    'SYSTEM_TOOL_ERROR_EVENT_NAME',
    # Hook Events
    'HOOK_FIRED_EVENT_NAME',
    # Enrichment Events
    'ENRICHMENT_EVENT_NAME',
    # Processing phases
    'EVALUATING_ENRICHMENT_PHASE',
    'PLANNING_ENRICHMENT_PHASE',
    'SEARCHING_TOOLS_ENRICHMENT_PHASE',
    'LOADING_TOOLS_ENRICHMENT_PHASE',
    'PLANNING_ASSIGNMENTS_ENRICHMENT_PHASE',
    'EXECUTING_ASSIGNMENTS_ENRICHMENT_PHASE',
    'EXECUTING_ACTIONS_ENRICHMENT_PHASE',
    'SYNTHESIZING_ENRICHMENT_PHASE',
    'FINALIZING_ENRICHMENT_PHASE',
    'REEVALUATE_ACCRUED_ENRICHMENT_PHASE',
    'PROCESSING_ENRICHMENT_MESSAGES',
    'PROCESSING_ENRICHMENT_PHASES',
    'MEMORY_SUMMARIZING_ENRICHMENT_PHASE',
    'MEMORY_SUMMARIZED_ENRICHMENT_PHASE',
    'MEMORY_RETRIEVING_ENRICHMENT_PHASE',
    'MEMORY_RETRIEVED_ENRICHMENT_PHASE',
    'MEMORY_INDEXING_ENRICHMENT_PHASE',
    'MEMORY_INDEXED_ENRICHMENT_PHASE',
    'MEMORY_GRAPH_EXTRACTING_ENRICHMENT_PHASE',
    'MEMORY_SKILL_EXTRACTING_ENRICHMENT_PHASE',
    'MEMORY_INSIGHT_EXTRACTING_ENRICHMENT_PHASE',
    'MEMORY_SKILL_EXTRACTED_ENRICHMENT_PHASE',
    'MEMORY_INSIGHT_EXTRACTED_ENRICHMENT_PHASE',
    'MEMORY_ENRICHMENT_PHASES',
    'REDACTION_PREVIEWED_ENRICHMENT_PHASE',
    'MESSAGE_REDACTION_APPLIED_ENRICHMENT_PHASE',
    'REDACTION_ENRICHMENT_PHASES',
    'RESOURCE_REGISTERING_ENRICHMENT_PHASE',
    'RESOURCE_REGISTERED_ENRICHMENT_PHASE',
    'RESOURCE_DEDUP_REUSED_ENRICHMENT_PHASE',
    'RESOURCE_VERSION_SUPERSEDED_ENRICHMENT_PHASE',
    'RESOURCE_EXTRACTING_ENRICHMENT_PHASE',
    'RESOURCE_EXTRACTED_ENRICHMENT_PHASE',
    'RESOURCE_ENRICHMENT_PHASES',
    'KNOWN_ENRICHMENT_PHASES',
    'resolve_enrichment_message',
    'is_known_enrichment_phase',
]
