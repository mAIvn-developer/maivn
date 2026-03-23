"""Tool registrar.
Registers tools in a repository and enforces scope-level registration policies.
"""

from __future__ import annotations

# MARK: Tool Registrar Service
from collections.abc import Iterable

from maivn._internal.core.entities.tools import BaseTool
from maivn._internal.core.interfaces.repositories import ToolRepoInterface


class ToolRegistrar:
    """Register tools with policy enforcement.

    Responsibilities:
    - Add tools to a repository
    - Enforce single final_tool per scope
    """

    # MARK: Initialization

    def __init__(self, repo: ToolRepoInterface) -> None:
        self._repo = repo

    # MARK: Public API

    def __call__(self, tool: BaseTool) -> None:
        """Register a tool, enforcing repository-level policies.

        Policies enforced:
        - Only one final_output tool allowed per scope/repository.
        """
        self._enforce_single_final_tool(tool)
        self._repo.add_tool(tool)

    # MARK: - Policy Enforcement

    def _enforce_single_final_tool(self, tool: BaseTool) -> None:
        """Enforce that only one final_tool exists per scope."""
        if not self._is_final_tool(tool):
            return

        existing = self._get_final_tools()
        if existing:
            names = ", ".join(getattr(t, "name", "<unnamed>") for t in existing)
            raise ValueError(
                f"Only one final_tool is allowed per scope. Existing final_tool tool(s): {names}"
            )

    # MARK: - Helpers

    def _is_final_tool(self, tool: BaseTool) -> bool:
        """Check if a tool is marked as final_tool."""
        return bool(getattr(tool, "final_tool", False))

    def _get_final_tools(self) -> list[BaseTool]:
        """Retrieve all existing final_tool tools from the repository."""
        try:
            tools: Iterable[object] = self._repo.list_tools()
        except Exception as e:
            raise RuntimeError("Failed to list tools while enforcing final_tool policy.") from e
        return [t for t in tools if self._is_final_tool(t)]
