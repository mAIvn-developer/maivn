"""In-memory dependency repository implementation.
Stores dependency lists keyed by tool id.
"""

from __future__ import annotations

# MARK: In-Memory Dependency Repository
from maivn_shared import BaseDependency, InterruptDependency, dumps

from maivn._internal.core.interfaces.repositories import DependencyRepoInterface


class DependencyRepo(DependencyRepoInterface):
    """In-memory implementation of DependencyRepoInterface."""

    def __init__(self) -> None:
        self.store: dict[str, list[BaseDependency]] = {}

    # MARK: - Private Helpers

    def _get_dependency_id(self, dependency: BaseDependency) -> str | None:
        dep_type = getattr(dependency, "dependency_type", None)
        arg_name = getattr(dependency, "arg_name", None)

        if isinstance(dependency, InterruptDependency):
            return dumps(
                {
                    "dependency_type": dep_type or "user",
                    "arg_name": arg_name,
                    "prompt": getattr(dependency, "prompt", None),
                }
            )

        identifier = (
            getattr(dependency, "tool_id", None)
            or getattr(dependency, "agent_id", None)
            or getattr(dependency, "data_key", None)
            or getattr(dependency, "name", None)
        )
        if dep_type or arg_name or identifier:
            return dumps(
                {
                    "dependency_type": dep_type,
                    "arg_name": arg_name,
                    "identifier": str(identifier) if identifier else None,
                }
            )

        try:
            return dumps(dependency.model_dump(mode="json"))
        except Exception:
            return str(dependency)

    def _find_dependency_index(self, deps: list[BaseDependency], dependency_id: str) -> int | None:
        for i, dep in enumerate(deps):
            if self._get_dependency_id(dep) == dependency_id:
                return i
        return None

    # MARK: - Dependency Methods

    def add_dependency(self, tool_id: str, dependency: BaseDependency) -> None:
        if not tool_id or dependency is None:
            return
        self.store.setdefault(tool_id, []).append(dependency)

    def get_dependency(self, dependency_id: str) -> BaseDependency | None:
        for deps in self.store.values():
            for dep in deps:
                if self._get_dependency_id(dep) == dependency_id:
                    return dep
        return None

    def list_dependencies(self, tool_id: str) -> list[BaseDependency]:
        return list(self.store.get(tool_id, []))

    def remove_dependency(self, dependency_id: str) -> None:
        for tool_id, deps in list(self.store.items()):
            idx = self._find_dependency_index(deps, dependency_id)
            if idx is not None:
                deps.pop(idx)
                if not deps:
                    del self.store[tool_id]
                return

    def update_dependency(self, dependency_id: str, dependency: BaseDependency) -> None:
        for deps in self.store.values():
            idx = self._find_dependency_index(deps, dependency_id)
            if idx is not None:
                deps[idx] = dependency
                return
