"""Tool execution interface.
Defines the protocol for executing a ToolCall and returning its result.
"""

from __future__ import annotations

from typing import Protocol

from maivn_shared import ToolCall, ToolExecutionResult

# MARK: - Tool Execution Interfaces


class ToolExecutor(Protocol):
    """Protocol for executing individual tool calls and returning their results."""

    def execute(self, call: ToolCall) -> ToolExecutionResult:
        """Execute the tool call and return the result.

        Args:
            call: The tool call to execute

        Returns:
            The result of the tool execution
        """
        ...
