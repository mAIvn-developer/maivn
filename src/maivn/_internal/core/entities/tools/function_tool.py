"""Function-based tool entity.

This module provides a tool implementation for Python functions,
using mixins for common functionality.
"""

from __future__ import annotations

import inspect
from collections.abc import Callable
from typing import Any

from maivn_shared import ToolType
from pydantic import Field

from ..mixins import FunctionToolIdentifiableMixin
from .base_tool import BaseTool

# MARK: - FunctionTool


class FunctionTool(FunctionToolIdentifiableMixin, BaseTool):
    """Model for function-based tools.

    Extends BaseTool with function-specific functionality and
    uses FunctionToolIdentifiableMixin for UUID generation based
    on the function object.
    """

    tool_type: ToolType = Field(
        default="func",
        description="Type of tool (always func for this class)",
    )
    tool_id: str = Field(
        default="",
        description="Unique tool identifier",
    )
    func: Callable[..., Any] = Field(
        ...,
        description="The function that implements this tool's behavior",
    )

    # MARK: Execution

    def is_executable(self) -> bool:
        """Check if tool can be executed.

        Returns:
            True if func is callable
        """
        return callable(self.func)

    # MARK: Function Metadata

    def get_function_name(self) -> str:
        """Get the name of the wrapped function.

        Returns:
            Function name or '<lambda>' for lambda functions
        """
        return getattr(self.func, "__name__", "<lambda>")

    def get_function_module(self) -> str | None:
        """Get the module name where the function is defined.

        Returns:
            Module name or None if not available
        """
        return getattr(self.func, "__module__", None)

    def get_function_signature(self) -> str:
        """Get a string representation of the function signature.

        Returns:
            String representation of function signature
        """
        try:
            return str(inspect.signature(self.func))
        except (ValueError, TypeError):
            return "<signature unavailable>"

    # MARK: String Representation

    def __str__(self) -> str:
        """Return string representation with function name."""
        return f"{self.name} ({self.get_function_name()})"


__all__ = [
    "FunctionTool",
]
