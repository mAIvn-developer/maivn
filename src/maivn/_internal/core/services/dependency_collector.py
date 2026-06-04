"""Dependency collection utilities for tool creation.

This module provides the DependencyCollector class which extracts dependencies
from callables and Pydantic models, including nested model dependencies.
"""

# pyright: strict
from __future__ import annotations

from collections.abc import Iterable
from typing import cast, get_args, get_origin

from maivn_shared import BaseDependency
from pydantic import BaseModel

# MARK: DependencyCollector


class DependencyCollector:
    """Collects dependencies from objects and their nested structures.

    This class handles the extraction of BaseDependency instances from
    callables, Pydantic models, and their nested fields. It supports
    recursive collection through model hierarchies.
    """

    # MARK: - Public Methods

    def collect_all(self, obj: object) -> list[BaseDependency]:
        """Collect all dependencies from an object.

        Args:
            obj: Callable or Pydantic model

        Returns:
            List of all dependencies
        """
        dependencies: list[BaseDependency] = []

        self._collect_direct_dependencies(obj, dependencies)
        self._collect_pending_dependencies(obj, dependencies)
        self._collect_model_dependencies(obj, dependencies)

        return dependencies

    # MARK: - Direct Collection Methods

    def _collect_direct_dependencies(
        self,
        obj: object,
        dependencies: list[BaseDependency],
    ) -> None:
        """Collect dependencies from _dependencies attribute."""
        for dep in _iter_attached_dependencies(obj, "_dependencies"):
            if dep not in dependencies:
                dependencies.append(dep)

    def _collect_pending_dependencies(
        self,
        obj: object,
        dependencies: list[BaseDependency],
    ) -> None:
        """Collect pending dependencies from __maivn_pending_deps__ attribute."""
        for dep in _iter_attached_dependencies(obj, "__maivn_pending_deps__"):
            if dep not in dependencies:
                dependencies.append(dep)

    def _collect_model_dependencies(
        self,
        obj: object,
        dependencies: list[BaseDependency],
    ) -> None:
        """Collect nested dependencies from Pydantic models."""
        if not (isinstance(obj, type) and issubclass(obj, BaseModel)):
            return

        for dep in self._collect_from_model(obj):
            if dep not in dependencies:
                dependencies.append(dep)

    # MARK: - Model Dependency Collection

    def _collect_from_model(
        self,
        model: type[BaseModel],
        visited: set[type[BaseModel]] | None = None,
    ) -> list[BaseDependency]:
        """Recursively collect dependencies from a Pydantic model."""
        if visited is None:
            visited = set()

        if model in visited:
            return []
        visited.add(model)

        all_deps: list[BaseDependency] = []

        self._collect_model_direct_deps(model, all_deps)
        self._collect_model_method_deps(model, all_deps)
        self._collect_model_nested_deps(model, all_deps, visited)

        return all_deps

    def _collect_model_direct_deps(
        self,
        model: type[BaseModel],
        all_deps: list[BaseDependency],
    ) -> None:
        """Collect direct and pending dependencies from model."""
        for dep in _iter_attached_dependencies(model, "_dependencies"):
            if dep not in all_deps:
                all_deps.append(dep)

        for dep in _iter_attached_dependencies(model, "__maivn_pending_deps__"):
            if dep not in all_deps:
                all_deps.append(dep)

    def _collect_model_method_deps(
        self,
        model: type[BaseModel],
        all_deps: list[BaseDependency],
    ) -> None:
        """Collect dependencies from model methods."""
        if not hasattr(model, "__dict__"):
            return

        for attr_value in cast(Iterable[object], model.__dict__.values()):
            if not callable(attr_value):
                continue

            for dep in _iter_attached_dependencies(attr_value, "_dependencies"):
                if dep not in all_deps:
                    all_deps.append(dep)

            for dep in _iter_attached_dependencies(attr_value, "__maivn_pending_deps__"):
                if dep not in all_deps:
                    all_deps.append(dep)

    def _collect_model_nested_deps(
        self,
        model: type[BaseModel],
        all_deps: list[BaseDependency],
        visited: set[type[BaseModel]],
    ) -> None:
        """Collect dependencies from nested model fields."""
        if not hasattr(model, "model_fields"):
            return

        for field_info in model.model_fields.values():
            for nested_model in self._extract_nested_models(field_info.annotation):
                if nested_model is model:
                    continue

                for dep in self._collect_from_model(nested_model, visited):
                    if dep not in all_deps:
                        all_deps.append(dep)

    # MARK: - Type Extraction

    @staticmethod
    def _extract_nested_models(field_type: object) -> list[type[BaseModel]]:
        """Extract Pydantic model classes from a field type annotation."""
        if isinstance(field_type, type) and issubclass(field_type, BaseModel):
            return [field_type]

        origin = get_origin(field_type)
        if origin is None:
            return []

        models: list[type[BaseModel]] = []
        for arg in cast(tuple[object, ...], get_args(field_type)):
            models.extend(DependencyCollector._extract_nested_models(arg))

        return models


# MARK: - Attribute Helpers


def _iter_attached_dependencies(obj: object, attr_name: str) -> Iterable[BaseDependency]:
    """Return decorator-attached dependencies from a dynamic metadata attribute."""
    return cast(Iterable[BaseDependency], getattr(obj, attr_name, []))


__all__ = ["DependencyCollector"]
