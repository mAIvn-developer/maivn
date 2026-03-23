"""Orchestrator service wiring and helpers.
Exposes internal services used by ``AgentOrchestrator`` and the builder.
Not part of the public SDK API surface.
"""

from __future__ import annotations

# MARK: - Infrastructure Imports
from ...utils.configuration.environment_config import DEFAULT_SERVER_BASE_URL

# MARK: - Local Imports
from .config_helpers import get_default_timeout_seconds, get_pending_event_timeout_seconds
from .constants import MAX_PARALLEL_WORKERS
from .events import EventStreamHandlers, EventStreamProcessor
from .events.interrupt_manager import InterruptHandler, InterruptManager
from .execution import BackgroundExecutor
from .helpers import get_optimal_worker_count
from .http import HttpClientService
from .orchestration import ToolExecutionOrchestrator
from .session import SessionService
from .state_compilation import StateCompiler
from .tool_execution import ToolEventDispatcher, ToolExecutionService

# MARK: - Public API

__all__ = [
    "BackgroundExecutor",
    "DEFAULT_SERVER_BASE_URL",
    "EventStreamHandlers",
    "EventStreamProcessor",
    "get_default_timeout_seconds",
    "get_optimal_worker_count",
    "get_pending_event_timeout_seconds",
    "HttpClientService",
    "InterruptHandler",
    "InterruptManager",
    "MAX_PARALLEL_WORKERS",
    "SessionService",
    "StateCompiler",
    "ToolEventDispatcher",
    "ToolExecutionOrchestrator",
    "ToolExecutionService",
]
