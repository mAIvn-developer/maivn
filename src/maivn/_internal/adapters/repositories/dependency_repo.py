"""In-memory dependency repository implementation.
Stores dependency lists keyed by tool id.
"""

# pyright: strict
from __future__ import annotations

from dataclasses import dataclass
from typing import Final, cast

from maivn_shared import BaseDependency, InterruptDependency, dumps
from typing_extensions import override

from ...core.interfaces.repositories import DependencyRepoInterface

# MARK: Constants

DEPENDENCY_TYPE_KEY: Final = "dependency_type"
ARG_NAME_KEY: Final = "arg_name"
PROMPT_KEY: Final = "prompt"
IDENTIFIER_KEY: Final = "identifier"


# MARK: Types


@dataclass(frozen=True)
class _DependencyLocation:
    tool_id: str
    index: int


# MARK: In-Memory Dependency Repository


class DependencyRepo(DependencyRepoInterface):
    """In-memory implementation of DependencyRepoInterface."""

    def __init__(self) -> None:
        self.store: dict[str, list[BaseDependency]] = {}
        self._dependency_keys_by_tool: dict[str, list[str]] = {}
        self._dependency_index: dict[str, _DependencyLocation] = {}

    # MARK: - Private Helpers

    @staticmethod
    def _get_dependency_attr(dependency: BaseDependency, name: str) -> object | None:
        return cast(object | None, getattr(dependency, name, None))

    def _compute_dependency_key(self, dependency: BaseDependency) -> str:
        dep_type = dependency.dependency_type
        arg_name = dependency.arg_name

        if isinstance(dependency, InterruptDependency):
            return dumps(
                {
                    DEPENDENCY_TYPE_KEY: dep_type,
                    ARG_NAME_KEY: arg_name,
                    PROMPT_KEY: dependency.prompt,
                }
            )

        identifier = (
            self._get_dependency_attr(dependency, "tool_id")
            or self._get_dependency_attr(dependency, "agent_id")
            or self._get_dependency_attr(dependency, "data_key")
            or dependency.name
        )
        return dumps(
            {
                DEPENDENCY_TYPE_KEY: dep_type,
                ARG_NAME_KEY: arg_name,
                IDENTIFIER_KEY: str(identifier) if identifier else None,
            }
        )

    def compute_dependency_key(self, dependency: BaseDependency) -> str:
        """Compute the stable repository key for a dependency."""
        return self._compute_dependency_key(dependency)

    def _get_dependency_id(self, dependency: BaseDependency) -> str:
        return self._compute_dependency_key(dependency)

    def _refresh_dependency_index(self) -> None:
        self._dependency_index.clear()
        for tool_id, dependency_keys in self._dependency_keys_by_tool.items():
            for index, dependency_key in enumerate(dependency_keys):
                _ = self._dependency_index.setdefault(
                    dependency_key,
                    _DependencyLocation(tool_id=tool_id, index=index),
                )

    def _get_dependency_location(self, dependency_id: str) -> _DependencyLocation | None:
        return self._dependency_index.get(dependency_id)

    def _remove_dependency_at(self, location: _DependencyLocation) -> None:
        dependencies = self.store.get(location.tool_id)
        dependency_keys = self._dependency_keys_by_tool.get(location.tool_id)
        if dependencies is None or dependency_keys is None:
            return

        _ = dependencies.pop(location.index)
        _ = dependency_keys.pop(location.index)
        if dependencies:
            self._refresh_dependency_index()
            return

        del self.store[location.tool_id]
        del self._dependency_keys_by_tool[location.tool_id]
        self._refresh_dependency_index()

    def _replace_dependency_at(
        self,
        location: _DependencyLocation,
        dependency: BaseDependency,
    ) -> None:
        dependencies = self.store.get(location.tool_id)
        dependency_keys = self._dependency_keys_by_tool.get(location.tool_id)
        if dependencies is None or dependency_keys is None:
            return

        dependencies[location.index] = dependency
        dependency_keys[location.index] = self._compute_dependency_key(dependency)
        self._refresh_dependency_index()

    # MARK: - Dependency Methods

    @override
    def add_dependency(self, tool_id: str, dependency: BaseDependency) -> None:
        if not tool_id:
            return

        dependency_key = self._compute_dependency_key(dependency)
        dependencies = self.store.setdefault(tool_id, [])
        dependency_keys = self._dependency_keys_by_tool.setdefault(tool_id, [])
        dependencies.append(dependency)
        dependency_keys.append(dependency_key)
        _ = self._dependency_index.setdefault(
            dependency_key,
            _DependencyLocation(tool_id=tool_id, index=len(dependencies) - 1),
        )

    @override
    def get_dependency(self, dependency_id: str) -> BaseDependency | None:
        location = self._get_dependency_location(dependency_id)
        if location is None:
            return None
        return self.store[location.tool_id][location.index]

    @override
    def list_dependencies(self, tool_id: str) -> list[BaseDependency]:
        return list(self.store.get(tool_id, []))

    @override
    def remove_dependency(self, dependency_id: str) -> None:
        location = self._get_dependency_location(dependency_id)
        if location is not None:
            self._remove_dependency_at(location)

    @override
    def update_dependency(self, dependency_id: str, dependency: BaseDependency) -> None:
        location = self._get_dependency_location(dependency_id)
        if location is not None:
            self._replace_dependency_at(location, dependency)
