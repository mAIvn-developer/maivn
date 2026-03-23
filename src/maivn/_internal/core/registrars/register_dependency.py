"""Dependency registrar.
Registers dependencies for tools in the dependency repository.
"""

from __future__ import annotations

from maivn_shared import BaseDependency

from maivn._internal.core.interfaces.repositories import DependencyRepoInterface

# MARK: Dependency Registrar


class DependencyRegistrar:
    """Register dependencies with repository management."""

    # MARK: - Initialization

    def __init__(self, repo: DependencyRepoInterface) -> None:
        self._repo = repo

    # MARK: - Public Interface

    def __call__(self, tool_id: str, dependency: BaseDependency) -> None:
        """Register a dependency.

        Args:
            tool_id: The ID of the tool to register the dependency for.
            dependency: The dependency to register.
        """
        self._repo.add_dependency(tool_id, dependency)
