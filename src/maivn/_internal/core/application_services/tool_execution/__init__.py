"""Tool execution services with dependency handling.

Executes function/model tools and resolves declared dependencies before execution.
Includes argument validation, strategy-based dispatch, and Pydantic deserialization.
"""

from __future__ import annotations

from maivn._internal.core.entities.execution_context import ExecutionContext

from .argument_utils import get_allowed_parameters, prune_arguments
from .basic_tool_execution_service import BasicToolExecutionService
from .execution_strategy import (
    FunctionExecutionStrategy,
    McpExecutionStrategy,
    ModelExecutionStrategy,
    StrategyRegistry,
    ToolExecutionStrategy,
    create_default_registry,
)
from .tool_event_dispatcher import ToolEventDispatcher
from .tool_execution_service import ToolExecutionService

__all__ = [
    # Context
    "ExecutionContext",
    # Services
    "BasicToolExecutionService",
    "ToolEventDispatcher",
    "ToolExecutionService",
    # Strategies
    "FunctionExecutionStrategy",
    "McpExecutionStrategy",
    "ModelExecutionStrategy",
    "StrategyRegistry",
    "ToolExecutionStrategy",
    "create_default_registry",
    # Utilities
    "get_allowed_parameters",
    "prune_arguments",
]
