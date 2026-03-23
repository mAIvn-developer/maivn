"""Dependency detection for tool schema creation.

Identifies and serializes tool dependencies from decorator metadata,
including interrupt, data, agent, and model tool dependencies.
"""

from __future__ import annotations

from typing import Any

from maivn_shared import create_uuid

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
        dependencies: list[Any],
        arg_name: str,
        context_name: str,
    ) -> dict[str, Any] | None:
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
    ) -> dict[str, Any]:
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

    def _matches_arg(self, dep: Any, arg_name: str) -> bool:
        """Check if dependency matches the given argument name."""
        return hasattr(dep, "arg_name") and dep.arg_name == arg_name

    def _build_dependency_schema(
        self,
        dep: Any,
        context_name: str,
    ) -> dict[str, Any] | None:
        """Build appropriate schema based on dependency type."""
        dep_type = getattr(dep, "dependency_type", None)

        if dep_type == "user":
            return self._build_interrupt_dependency(dep, context_name)
        if dep_type == "data":
            return self._build_data_dependency(dep)
        if dep_type == "agent":
            return self._build_agent_dependency(dep)

        if hasattr(dep, "tool_id"):
            return self._build_tool_dependency(dep)

        return None

    # MARK: - Schema Builders

    def _build_interrupt_dependency(
        self,
        dep: Any,
        context_name: str,
    ) -> dict[str, Any]:
        """Build schema for interrupt dependency (@depends_on_interrupt)."""
        interrupt_id = create_uuid(f"interrupt_{context_name}_{dep.arg_name}")
        data_key = getattr(dep, "data_key", None) or dep.arg_name

        return {
            "type": "interrupt_dependency",
            "interrupt_id": interrupt_id,
            "prompt": dep.prompt,
            "data_key": data_key,
            "description": f"User input: {dep.prompt}",
        }

    def _build_data_dependency(self, dep: Any) -> dict[str, Any]:
        """Build schema for data dependency (@depends_on_private_data)."""
        return {
            "type": "data_dependency",
            "data_key": dep.data_key,
            "description": f"Data from private_data['{dep.data_key}']",
        }

    def _build_agent_dependency(self, dep: Any) -> dict[str, Any]:
        """Build schema for agent dependency (@depends_on_agent)."""
        agent_tool_id = create_uuid(f"agent_invoke_{dep.agent_id}")

        return self._create_tool_dependency_schema(
            tool_id=agent_tool_id,
            tool_name=dep.agent_id,
            tool_type="agent",
        )

    def _build_tool_dependency(self, dep: Any) -> dict[str, Any]:
        """Build schema for tool dependency (@depends_on_tool)."""
        tool_name = getattr(dep, "tool_name", dep.tool_id)

        return self._create_tool_dependency_schema(
            tool_id=dep.tool_id,
            tool_name=tool_name,
            tool_type="func",
        )

    def _create_tool_dependency_schema(
        self,
        tool_id: str,
        tool_name: str,
        tool_type: str,
    ) -> dict[str, Any]:
        """Create a standardized tool dependency schema."""
        return {
            "type": "tool_dependency",
            "tool_id": tool_id,
            "tool_name": tool_name,
            "tool_type": tool_type,
            "description": f"Output from {tool_name}",
            "output_type": "object",
        }


__all__ = ["DependencyDetector"]
