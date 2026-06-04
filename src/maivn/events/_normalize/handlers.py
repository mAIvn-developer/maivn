# pyright: strict
"""Dispatch table for event normalization handlers."""

from __future__ import annotations

from collections.abc import Callable
from typing import TypeAlias

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

from .._models import JsonObject, NormalizedStreamState
from .assistant_events import (
    handle_progress_update_event,
    handle_status_message_event,
    handle_update_event,
)
from .context import NormalizationOptions
from .lifecycle_events import (
    handle_enrichment_event,
    handle_error_event,
    handle_final_event,
    handle_interrupt_required_event,
    handle_session_start_event,
)
from .system_events import (
    handle_system_tool_chunk_event,
    handle_system_tool_complete_event,
    handle_system_tool_error_event,
    handle_system_tool_start_event,
)
from .tool_events import handle_model_tool_complete_event, handle_tool_event

# MARK: Configuration

_SESSION_START_EVENT_NAME = "session_start"


# MARK: Dispatch


EventHandler: TypeAlias = Callable[
    [JsonObject, NormalizedStreamState, NormalizationOptions],
    list[JsonObject],
]

EVENT_HANDLERS: dict[str, EventHandler] = {
    UPDATE_EVENT_NAME: handle_update_event,
    PROGRESS_UPDATE_EVENT_NAME: handle_progress_update_event,
    TOOL_EVENT_NAME: handle_tool_event,
    SYSTEM_TOOL_START_EVENT_NAME: handle_system_tool_start_event,
    SYSTEM_TOOL_CHUNK_EVENT_NAME: handle_system_tool_chunk_event,
    SYSTEM_TOOL_COMPLETE_EVENT_NAME: handle_system_tool_complete_event,
    SYSTEM_TOOL_ERROR_EVENT_NAME: handle_system_tool_error_event,
    MODEL_TOOL_COMPLETE_EVENT_NAME: handle_model_tool_complete_event,
    STATUS_MESSAGE_EVENT_NAME: handle_status_message_event,
    ENRICHMENT_EVENT_NAME: handle_enrichment_event,
    INTERRUPT_REQUIRED_EVENT_NAME: handle_interrupt_required_event,
    FINAL_EVENT_NAME: handle_final_event,
    ERROR_EVENT_NAME: handle_error_event,
    _SESSION_START_EVENT_NAME: handle_session_start_event,
}
