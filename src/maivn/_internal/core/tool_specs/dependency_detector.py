"""Dependency detection for tool schema creation.

Identifies and serializes tool dependencies from decorator metadata,
including interrupt, data, agent, and model tool dependencies.
"""

# pyright: strict
from __future__ import annotations

from collections.abc import Sequence
from typing import TypeAlias

from maivn_shared import (
    AgentDependency,
    AwaitForDependency,
    BaseDependency,
    DataDependency,
    InterruptDependency,
    ReevaluateDependency,
    ToolDependency,
    create_uuid,
)
from pydantic import JsonValue

# MARK: Types

JsonObject: TypeAlias = dict[str, JsonValue]
ToolIdDependency: TypeAlias = ToolDependency | AwaitForDependency | ReevaluateDependency

# MARK: Dependency Detector


class DependencyDetector:
    """Detects and processes tool dependencies from decorator metadata.

    Handles detection of dependencies decorated with @depends_on_interrupt,
    @depends_on_private_data, @depends_on_agent, and @depends_on_tool.
    Also builds dependency schemas for nested Pydantic models.
    """

    # MARK: - Public Methods

    def detect_dependency(
        self,
        dependencies: Sequence[BaseDependency],
        arg_name: str,
        context_name: str,
    ) -> JsonObject | None:
        """Detect if an argument has a dependency decorator.

        Args:
            dependencies: List of dependency objects from decorators
            arg_name: Name of the argument/property to check
            context_name: Name of the function/model for interrupt ID generation

        Returns:
            Dependency schema dict if found, None otherwise
        """
        for dep in dependencies:
            if not self._matches_arg(dep, arg_name):
                continue

            schema = self._build_dependency_schema(dep, context_name)
            if schema:
                return schema

        return None

    def build_model_tool_dependency(
        self,
        tool_id: str,
        model_name: str,
        ref_path: str | None = None,
    ) -> JsonObject:
        """Build schema for a Pydantic model tool dependency.

        Args:
            tool_id: The tool's UUID
            model_name: Name of the model class
            ref_path: Optional original $ref path for debugging

        Returns:
            Tool dependency schema dict
        """
        schema = self._create_tool_dependency_schema(
            tool_id=tool_id,
            tool_name=model_name,
            tool_type="model",
        )

        if ref_path:
            schema["original_ref"] = ref_path

        return schema

    # MARK: - Dependency Detection

    def _matches_arg(self, dep: BaseDependency, arg_name: str) -> bool:
        """Check if dependency matches the given argument name."""
        return dep.arg_name == arg_name

    def _build_dependency_schema(
        self,
        dep: BaseDependency,
        context_name: str,
    ) -> JsonObject | None:
        """Build appropriate schema based on dependency type."""
        if isinstance(dep, InterruptDependency):
            return self._build_interrupt_dependency(dep, context_name)
        if isinstance(dep, DataDependency):
            return self._build_data_dependency(dep)
        if isinstance(dep, AgentDependency):
            return self._build_agent_dependency(dep)
        if isinstance(dep, ToolDependency | AwaitForDependency | ReevaluateDependency):
            return self._build_tool_dependency(dep)

        return None

    # MARK: - Schema Builders

    def _build_interrupt_dependency(
        self,
        dep: InterruptDependency,
        context_name: str,
    ) -> JsonObject:
        """Build schema for interrupt dependency (@depends_on_interrupt)."""
        interrupt_id = create_uuid(f"interrupt_{context_name}_{dep.arg_name}")

        return {
            "type": "interrupt_dependency",
            "interrupt_id": interrupt_id,
            "prompt": dep.prompt,
            "data_key": dep.arg_name,
            "description": f"User input: {dep.prompt}",
        }

    def _build_data_dependency(self, dep: DataDependency) -> JsonObject:
        """Build schema for data dependency (@depends_on_private_data)."""
        return {
            "type": "data_dependency",
            "data_key": dep.data_key,
            "description": f"Data from private_data['{dep.data_key}']",
        }

    def _build_agent_dependency(self, dep: AgentDependency) -> JsonObject:
        """Build schema for agent dependency (@depends_on_agent)."""
        agent_tool_id = create_uuid(f"agent_invoke_{dep.agent_id}")

        return self._create_tool_dependency_schema(
            tool_id=agent_tool_id,
            tool_name=dep.agent_id,
            tool_type="agent",
        )

    def _build_tool_dependency(self, dep: ToolIdDependency) -> JsonObject:
        """Build schema for tool dependency (@depends_on_tool)."""
        return self._create_tool_dependency_schema(
            tool_id=dep.tool_id,
            tool_name=_tool_dependency_name(dep),
            tool_type="func",
        )

    def _create_tool_dependency_schema(
        self,
        tool_id: str,
        tool_name: str,
        tool_type: str,
    ) -> JsonObject:
        """Create a standardized tool dependency schema."""
        return {
            "type": "tool_dependency",
            "tool_id": tool_id,
            "tool_name": tool_name,
            "tool_type": tool_type,
            "description": f"Output from {tool_name}",
            "output_type": "object",
        }


# MARK: Helpers


def _tool_dependency_name(dep: ToolIdDependency) -> str:
    """Return the best available display name for a tool-like dependency."""
    if isinstance(dep, ToolDependency):
        return dep.tool_id
    return dep.tool_name or dep.tool_id


__all__ = ["DependencyDetector"]
