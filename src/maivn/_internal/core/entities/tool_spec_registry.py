"""Registry for tool schema factories."""

from __future__ import annotations

from collections.abc import Callable

from maivn_shared import ArgsSchema
from pydantic import BaseModel, ConfigDict, Field

from .tools import BaseTool

# MARK: - Types

SchemaFactory = Callable[[BaseTool], ArgsSchema]

# MARK: ToolSpecRegistry


class ToolSpecRegistry(BaseModel):
    """Pydantic model that tracks schema factories for tools."""

    model_config = ConfigDict(arbitrary_types_allowed=True)

    factories: dict[type[BaseTool], SchemaFactory] = Field(default_factory=dict)

    # MARK: - Public Methods

    def register(
        self,
        *,
        tool_type: type[BaseTool],
        factory: SchemaFactory,
    ) -> None:
        """Register a schema factory for a tool type."""
        self.factories[tool_type] = factory

    def get(self, tool_type: type[BaseTool]) -> SchemaFactory | None:
        """Retrieve a schema factory by tool type."""
        return self.factories.get(tool_type)

    # MARK: - Dunder Methods

    def __hash__(self) -> int:
        """Provide hash support by delegating to object id."""
        return id(self)
