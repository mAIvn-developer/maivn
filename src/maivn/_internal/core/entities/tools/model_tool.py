"""Model-based tool entity.

This module provides a tool implementation for Pydantic models,
using mixins for common functionality.
"""

from __future__ import annotations

from typing import Any

from maivn_shared import ToolType
from pydantic import BaseModel, Field

from ..mixins import ModelToolIdentifiableMixin
from .base_tool import BaseTool

# MARK: - ModelTool


class ModelTool(ModelToolIdentifiableMixin, BaseTool):
    """Model for model-based tools.

    Extends BaseTool with Pydantic model-specific functionality and
    uses ModelToolIdentifiableMixin for UUID generation based
    on the model class.
    """

    # MARK: - Fields

    tool_type: ToolType = Field(
        default="model",
        description="Type of tool (always model for this class)",
    )
    model: type[BaseModel] = Field(
        ...,
        description="The Pydantic model class to use for validation and execution",
    )

    # MARK: - Execution

    def is_executable(self) -> bool:
        """Check if tool can be executed.

        Returns:
            True if model is a valid Pydantic model class
        """
        return isinstance(self.model, type) and issubclass(self.model, BaseModel)

    def create_instance(self, **kwargs: Any) -> BaseModel:
        """Create an instance of the model with the provided arguments.

        Args:
            kwargs: Arguments to pass to model constructor

        Returns:
            Model instance

        Raises:
            ValidationError: If model validation fails
        """
        return self.model(**kwargs)

    # MARK: - Introspection

    def get_model_name(self) -> str:
        """Get the name of the model class.

        Returns:
            Model class name
        """
        return self.model.__name__

    def get_model_module(self) -> str | None:
        """Get the module name where the model is defined.

        Returns:
            Module name or None if not available
        """
        return getattr(self.model, "__module__", None)

    def get_model_fields(self) -> dict[str, Any]:
        """Get information about the model's fields.

        Returns:
            Dictionary mapping field names to field information
        """
        return {
            name: self._extract_field_info(field) for name, field in self.model.model_fields.items()
        }

    def get_model_schema(self) -> dict[str, Any]:
        """Get the JSON schema for the model.

        Returns:
            JSON schema dictionary
        """
        return self.model.model_json_schema()

    # MARK: - Private Methods

    @staticmethod
    def _extract_field_info(field: Any) -> dict[str, Any]:
        """Extract field information from a Pydantic field.

        Args:
            field: Pydantic field info object

        Returns:
            Dictionary with field metadata
        """
        return {
            "type": str(field.annotation) if field.annotation else "Any",
            "required": field.is_required(),
            "default": field.default if field.default is not ... else None,
            "description": field.description,
        }

    # MARK: - String Representation

    def __str__(self) -> str:
        """Return string representation with model name."""
        return f"{self.name} ({self.get_model_name()})"


__all__ = [
    "ModelTool",
]
