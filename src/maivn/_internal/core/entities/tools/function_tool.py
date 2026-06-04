"""Function-based tool entity."""

# pyright: strict
from __future__ import annotations

from collections.abc import Callable

from maivn_shared import ToolType
from pydantic import Field
from typing_extensions import override

from ..mixins import FunctionToolIdentifiableMixin
from .base_tool import FUNCTION_TOOL_TYPE, BaseTool

# MARK: - FunctionTool


class FunctionTool(FunctionToolIdentifiableMixin, BaseTool):
    """Model for function-based tools.

    Extends BaseTool with function-specific functionality and
    uses FunctionToolIdentifiableMixin for UUID generation based
    on the function object.
    """

    tool_type: ToolType = Field(
        default=FUNCTION_TOOL_TYPE,
        description="Type of tool (always func for this class)",
    )
    func: Callable[..., object] = Field(
        ...,
        description="The function that implements this tool's behavior",
    )

    # MARK: Function Metadata

    def get_function_name(self) -> str:
        """Get the name of the wrapped function.

        Returns:
            Function name or '<lambda>' for lambda functions
        """
        return self._callable_name(self.func, "<lambda>")

    # MARK: String Representation

    @override
    def __str__(self) -> str:
        """Return string representation with function name."""
        return self._format_tool_label(self.get_function_name())


__all__ = [
    "FunctionTool",
]
