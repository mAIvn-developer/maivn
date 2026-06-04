"""Model-based tool entity."""

# pyright: strict
from __future__ import annotations

from maivn_shared import ToolType
from pydantic import BaseModel, Field
from typing_extensions import override

from ..mixins import ModelToolIdentifiableMixin
from .base_tool import MODEL_TOOL_TYPE, BaseTool

# MARK: - ModelTool


class ModelTool(ModelToolIdentifiableMixin, BaseTool):
    """Model for model-based tools.

    Extends BaseTool with Pydantic model-specific functionality and
    uses ModelToolIdentifiableMixin for UUID generation based
    on the model class.
    """

    # MARK: - Fields

    tool_type: ToolType = Field(
        default=MODEL_TOOL_TYPE,
        description="Type of tool (always model for this class)",
    )
    model: type[BaseModel] = Field(
        ...,
        description="The Pydantic model class to use for validation and execution",
    )

    # MARK: - Introspection

    def get_model_name(self) -> str:
        """Get the name of the model class.

        Returns:
            Model class name
        """
        return self.model.__name__

    # MARK: - String Representation

    @override
    def __str__(self) -> str:
        """Return string representation with model name."""
        return self._format_tool_label(self.get_model_name())


__all__ = [
    "ModelTool",
]
