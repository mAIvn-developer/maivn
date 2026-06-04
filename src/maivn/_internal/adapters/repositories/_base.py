"""Shared in-memory repository primitives."""

# pyright: strict
from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Generic, TypeVar, cast

# MARK: Types

TEntity = TypeVar("TEntity")


# MARK: Name-Indexed Repository


class NameIndexedRepo(Generic[TEntity], ABC):
    """Base repository for entities keyed by id with secondary name lookup.

    Concrete subclasses own the ``store`` attribute. Its element type is fixed
    by the matching repository interface (for example ``dict[str, Agent]``),
    so this generic base cannot declare ``store`` without colliding with that
    interface declaration under dict invariance; it reaches the subclass store
    through :meth:`_store` instead.
    """

    def __init__(self) -> None:
        self._name_index: dict[str, TEntity] = {}

    # MARK: - Entity Hooks

    @abstractmethod
    def _get_entity_id(self, entity: TEntity) -> str | None:
        """Return the entity's repository key."""
        raise NotImplementedError

    def _get_entity_name(self, entity: TEntity) -> str | None:
        return cast(str | None, getattr(entity, "name", None))

    # MARK: - Shared CRUD

    def _add(self, entity: TEntity) -> None:
        entity_id = self._get_entity_id(entity)
        store = self._store()
        if not entity_id or entity_id in store:
            return
        store[entity_id] = entity
        self._add_to_name_index(entity)

    def _get(self, entity_id: str) -> TEntity | None:
        return self._store().get(entity_id)

    def _get_by_name(self, name: str) -> TEntity | None:
        return self._name_index.get(name)

    def _list(self) -> list[TEntity]:
        return list(self._store().values())

    def _remove(self, entity_id: str) -> None:
        entity = self._store().pop(entity_id, None)
        if entity is not None:
            self._remove_from_name_index(entity)

    def _update(self, entity: TEntity) -> None:
        entity_id = self._get_entity_id(entity)
        if not entity_id:
            return
        store = self._store()
        existing = store.get(entity_id)
        if existing is not None:
            self._remove_from_name_index(existing)
        store[entity_id] = entity
        self._add_to_name_index(entity)

    # MARK: - Index Helpers

    def _store(self) -> dict[str, TEntity]:
        return cast(dict[str, TEntity], self.__dict__["store"])

    def _add_to_name_index(self, entity: TEntity) -> None:
        name = self._get_entity_name(entity)
        if name:
            self._name_index[name] = entity

    def _remove_from_name_index(self, entity: TEntity) -> None:
        name = self._get_entity_name(entity)
        if name and name in self._name_index:
            del self._name_index[name]
