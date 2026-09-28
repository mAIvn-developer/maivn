"""Typed SDK client surface for first-class skill routes."""

from __future__ import annotations

from typing import TYPE_CHECKING

from pydantic import BaseModel, ConfigDict

from maivn._internal.api.async_stream import run_blocking
from maivn._internal.skills.models import JsonObject, Skill, SkillScopeRequest, SkillSet, SkillStep
from maivn._internal.wire import (
    SKILL_PATH,
    SKILL_SET_CHILD_SET_PATH,
    SKILL_SET_PATH,
    SKILL_SET_SKILLS_COLLECTION_PATH,
    SKILL_SET_SKILLS_PATH,
    SKILL_SETS_PATH,
    SKILLS_PATH,
)

if TYPE_CHECKING:
    from collections.abc import Callable

    from maivn._internal.transport.http import HttpJsonClient


class SkillSetListResponse(BaseModel):
    """Internal list envelope for skill-set records."""

    model_config = ConfigDict(extra='forbid', frozen=True)

    items: tuple[SkillSet, ...] = ()


class SkillListResponse(BaseModel):
    """Internal list envelope for skill records."""

    model_config = ConfigDict(extra='forbid', frozen=True)

    items: tuple[Skill, ...] = ()


class SkillSetsClient:
    """Skill-set collection facade."""

    def __init__(self, http: Callable[[], HttpJsonClient]) -> None:
        """Create a skill-set facade backed by a bound HTTP client accessor."""
        self._get_http = http

    def list(self, *, query: str | None = None, limit: int = 16) -> tuple[SkillSet, ...]:
        """List or search first-class skill sets visible to the caller."""
        return run_blocking(lambda: self.alist(query=query, limit=limit))

    async def alist(
        self,
        *,
        query: str | None = None,
        limit: int = 16,
    ) -> tuple[SkillSet, ...]:
        """List or search first-class skill sets asynchronously."""
        payload = await self._get_http().get(
            SKILL_SETS_PATH,
            params=_list_params(query=query, limit=limit),
        )
        return SkillSetListResponse.model_validate(payload).items

    def get(self, set_id: str) -> SkillSet:
        """Fetch one first-class skill set."""
        return run_blocking(lambda: self.aget(set_id))

    async def aget(self, set_id: str) -> SkillSet:
        """Fetch one first-class skill set asynchronously."""
        path = SKILL_SET_PATH.format(set_id=set_id)
        return SkillSet.model_validate(await self._get_http().get(path))

    def create(self, skill_set: SkillSet) -> SkillSet:
        """Create one first-class skill set."""
        return run_blocking(lambda: self.acreate(skill_set))

    async def acreate(self, skill_set: SkillSet) -> SkillSet:
        """Create one first-class skill set asynchronously."""
        return SkillSet.model_validate(
            await self._get_http().post(SKILL_SETS_PATH, _dump_skill_set_create(skill_set)),
        )

    def add_skill(self, set_id: str, skill_id: str) -> SkillSet:
        """Add one skill to a skill set."""
        return run_blocking(lambda: self.aadd_skill(set_id, skill_id))

    async def aadd_skill(self, set_id: str, skill_id: str) -> SkillSet:
        """Add one skill to a skill set asynchronously."""
        path = SKILL_SET_SKILLS_PATH.format(set_id=set_id, skill_id=skill_id)
        return SkillSet.model_validate(await self._get_http().post(path, {}))

    def add_child_set(self, set_id: str, child_set_id: str) -> SkillSet:
        """Nest one child skill set below another skill set."""
        return run_blocking(lambda: self.aadd_child_set(set_id, child_set_id))

    async def aadd_child_set(self, set_id: str, child_set_id: str) -> SkillSet:
        """Nest one child skill set below another skill set asynchronously."""
        path = SKILL_SET_CHILD_SET_PATH.format(set_id=set_id, child_set_id=child_set_id)
        return SkillSet.model_validate(await self._get_http().post(path, {}))

    def search_skills(self, set_id: str, *, query: str, limit: int = 16) -> tuple[Skill, ...]:
        """Search skill records scoped to one skill set."""
        return run_blocking(lambda: self.asearch_skills(set_id, query=query, limit=limit))

    async def asearch_skills(
        self,
        set_id: str,
        *,
        query: str,
        limit: int = 16,
    ) -> tuple[Skill, ...]:
        """Search skill records scoped to one skill set asynchronously."""
        payload = await self._get_http().get(
            SKILL_SET_SKILLS_COLLECTION_PATH.format(set_id=set_id),
            params=_list_params(query=query, limit=limit),
        )
        return SkillListResponse.model_validate(payload).items


class SkillsClient:
    """Root facade for first-class skill and skill-set routes."""

    def __init__(self, http: Callable[[], HttpJsonClient]) -> None:
        """Create skill collection facades backed by a bound HTTP client accessor."""
        self._get_http = http
        self.skill_sets = SkillSetsClient(http)

    def create(self, skill: Skill) -> Skill:
        """Create one first-class skill."""
        return run_blocking(lambda: self.acreate(skill))

    async def acreate(self, skill: Skill) -> Skill:
        """Create one first-class skill asynchronously."""
        return Skill.model_validate(
            await self._get_http().post(SKILLS_PATH, _dump_skill_create(skill)),
        )

    def list(self, *, query: str | None = None, limit: int = 16) -> tuple[Skill, ...]:
        """List or search first-class skill records visible to the caller."""
        return run_blocking(lambda: self.alist(query=query, limit=limit))

    async def alist(self, *, query: str | None = None, limit: int = 16) -> tuple[Skill, ...]:
        """List or search first-class skill records asynchronously."""
        payload = await self._get_http().get(
            SKILLS_PATH,
            params=_list_params(query=query, limit=limit),
        )
        return SkillListResponse.model_validate(payload).items

    def get(self, skill_id: str) -> Skill:
        """Fetch one first-class skill."""
        return run_blocking(lambda: self.aget(skill_id))

    async def aget(self, skill_id: str) -> Skill:
        """Fetch one first-class skill asynchronously."""
        path = SKILL_PATH.format(skill_id=skill_id)
        return Skill.model_validate(await self._get_http().get(path))

    def register_skill_set_tree(self, skill_set: SkillSet) -> SkillSet:
        """Register a whole skill-set tree through first-class skill routes."""
        return run_blocking(lambda: self.aregister_skill_set_tree(skill_set))

    async def aregister_skill_set_tree(self, skill_set: SkillSet) -> SkillSet:
        """Register a whole skill-set tree asynchronously."""
        created = await self.skill_sets.acreate(skill_set)
        created_id = _required_set_id(created)
        registered_children: list[Skill | SkillSet] = []
        current = created
        for child in skill_set.children:
            if isinstance(child, Skill):
                registered_skill = await self.acreate(child)
                current = await self.skill_sets.aadd_skill(
                    created_id,
                    _required_skill_id(registered_skill),
                )
                registered_children.append(registered_skill)
                continue
            registered_set = await self.aregister_skill_set_tree(child)
            linked_set = await self.skill_sets.aadd_child_set(
                created_id,
                _required_set_id(registered_set),
            )
            registered_children.append(
                linked_set.model_copy(update={'children': registered_set.children}),
            )
        return current.model_copy(update={'children': tuple(registered_children)})


def _dump_skill_create(skill: Skill) -> JsonObject:
    payload: JsonObject = {
        'name': skill.name,
        'description': skill.description,
        'steps': [_dump_step(step) for step in skill.steps],
    }
    _set_optional(payload, 'memory_id', skill.memory_id)
    _set_optional(payload, 'scope', _scope_payload(skill.scope))
    _set_optional(payload, 'preconditions', _nonempty_json(skill.preconditions))
    _set_optional(payload, 'postconditions', _nonempty_json(skill.postconditions))
    if skill.confidence != 1.0:
        payload['confidence'] = skill.confidence
    if skill.origin != 'user_defined':
        payload['origin'] = skill.origin
    if skill.status != 'active':
        payload['status'] = skill.status
    _set_optional(payload, 'source_session_id', skill.source_session_id)
    _set_optional(payload, 'source_invocation_id', skill.source_invocation_id)
    return payload


def _dump_skill_set_create(skill_set: SkillSet) -> JsonObject:
    payload: JsonObject = {
        'name': skill_set.name,
        'description': skill_set.description,
    }
    _set_optional(payload, 'id', skill_set.id)
    _set_optional(payload, 'scope', _scope_payload(skill_set.scope))
    _set_optional(payload, 'parent_set_id', skill_set.parent_set_id)
    return payload


def _dump_step(step: SkillStep) -> JsonObject:
    return step.model_dump(mode='json', exclude_none=True, exclude_defaults=True)


def _scope_payload(scope: SkillScopeRequest) -> JsonObject | None:
    payload: JsonObject = {}
    _set_optional(payload, 'project_id', scope.project_id)
    _set_optional(payload, 'organization_id', scope.organization_id)
    _set_optional(payload, 'agent_id', scope.agent_id)
    _set_optional(payload, 'swarm_id', scope.swarm_id)
    if payload or scope.sharing_scope != 'project':
        payload['sharing_scope'] = scope.sharing_scope
    return payload or None


def _list_params(*, query: str | None, limit: int) -> dict[str, str | int]:
    params: dict[str, str | int] = {'limit': limit}
    if query is not None:
        params['query'] = query
    return params


def _set_optional(payload: JsonObject, key: str, value: object | None) -> None:
    if value is not None:
        payload[key] = value


def _nonempty_json(value: JsonObject) -> JsonObject | None:
    return value or None


def _required_skill_id(skill: Skill) -> str:
    if skill.memory_id is not None:
        return skill.memory_id
    message = 'created skill response did not include memory_id'
    raise RuntimeError(message)


def _required_set_id(skill_set: SkillSet) -> str:
    if skill_set.id is not None:
        return skill_set.id
    message = 'created skill-set response did not include id'
    raise RuntimeError(message)


__all__ = ['SkillSetsClient', 'SkillsClient']
