"""Model tool execution with validation.

This module handles execution of ModelTool instances (Pydantic models).
"""

from __future__ import annotations

from typing import Any

from maivn_shared.infrastructure.logging import LoggerProtocol

from maivn._internal.core.entities import ModelTool
from maivn._internal.core.exceptions import ToolExecutionError


class ModelExecutor:
    """Executes model tools (Pydantic models)."""

    def __init__(self, *, logger: LoggerProtocol | None = None) -> None:
        """Initialize model executor.

        Args:
            logger: Logger for operation tracking
        """
        self._logger: LoggerProtocol | None = logger

    def execute(self, tool: ModelTool, args: dict[str, Any]) -> Any:
        """Execute a model tool with enhanced error handling.

        Args:
            tool: Model tool to execute
            args: Arguments for execution

        Returns:
            Model execution result (dict representation)

        Raises:
            TypeError: If tool has no model
            ValueError: If model validation fails
        """
        model_cls = getattr(tool, "model", None)
        if model_cls is None:
            tool_name = getattr(tool, "name", "unknown")
            raise ToolExecutionError(
                tool_id=tool_name,
                reason="ModelTool has no 'model' attribute",
            )

        try:
            instance = model_cls(**args)
            return instance.model_dump(mode="json")
        except Exception as e:
            # Provide better error message for validation errors
            raise ToolExecutionError(
                tool_id=model_cls.__name__,
                reason=f"Model validation failed: {e}",
                original_error=e,
            ) from e


__all__ = ["ModelExecutor"]
