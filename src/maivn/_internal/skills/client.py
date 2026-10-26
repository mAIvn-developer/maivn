"""Typed SDK client surface for first-class skill routes."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from http import HTTPStatus
from typing import TYPE_CHECKING, Literal

from pydantic import BaseModel, ConfigDict, Field, StrictStr

from maivn._internal.api.async_stream import run_blocking
from maivn._internal.errors import MaivnHTTPError, MaivnSDKError
from maivn._internal.skills.models import JsonObject, Skill, SkillScopeRequest, SkillSet, SkillStep
from maivn._internal.wire import (
    SKILL_PATH,
    SKILL_SET_CHILD_SET_PATH,
    SKILL_SET_INDEX_PATH,
    SKILL_SET_SKILLS_COLLECTION_PATH,
    SKILL_SET_SKILLS_PATH,
    SKILL_SETS_PATH,
    SKILLS_PATH,
)

if TYPE_CHECKING:
    from collections.abc import Callable, Iterator, Mapping

    from maivn._internal.transport.http import HttpJsonClient


# A skill's `set_path` names at most 16 sets, so no fetched tree may be deeper.
_MAX_SKILL_SET_DEPTH = 16
_MAX_CONCURRENT_SKILL_READS = 8


class _SkillSetRecord(BaseModel):
    """One skill set exactly as the API returns it: replies never carry ``children``."""

    model_config = ConfigDict(extra='forbid', frozen=True)

    kind: Literal['skill_set'] = 'skill_set'
    id: StrictStr | None = Field(default=None, min_length=1, max_length=160)
    scope: SkillScopeRequest = Field(default_factory=SkillScopeRequest)
    name: StrictStr = Field(..., min_length=1, max_length=160)
    description: StrictStr = Field(..., min_length=1, max_length=4000)
    parent_set_id: StrictStr | None = Field(default=None, min_length=1, max_length=160)
    member_skill_ids: tuple[StrictStr, ...] = Field(default=(), max_length=256)

    def to_skill_set(self, children: tuple[Skill | SkillSet, ...] = ()) -> SkillSet:
        """Return the public model, with ``children`` assembled by the caller."""
        return SkillSet(
            id=self.id,
            scope=self.scope,
            name=self.name,
            description=self.description,
            parent_set_id=self.parent_set_id,
            member_skill_ids=self.member_skill_ids,
            children=children,
        )


class _SkillSetIndexRecord(BaseModel):
    """The skill-set index reply: one set plus every set nested below it."""

    model_config = ConfigDict(extra='forbid', frozen=True)

    skill_set: _SkillSetRecord
    descendant_skill_sets: tuple[_SkillSetRecord, ...] = ()
    skills: tuple[Skill, ...] = ()


@dataclass(frozen=True)
class _SetNode:
    record: _SkillSetRecord
    children: tuple[_SetNode, ...]


class SkillSetListResponse(BaseModel):
    """Internal list envelope for skill-set records."""

    model_config = ConfigDict(extra='forbid', frozen=True)

    items: tuple[_SkillSetRecord, ...] = ()


class SkillListResponse(BaseModel):
    """Internal list envelope for skill records."""

    model_config = ConfigDict(extra='forbid', frozen=True)

    items: tuple[Skill, ...] = ()


class SkillSetsClient:
    """Skill-set collection facade."""

    def for_user(self, user_id: str) -> SkillSetsClient:
        """Return independent customer management; derive user_id on a trusted backend."""
        http = self._get_http().for_user(user_id)
        return SkillSetsClient(lambda: http)

    def __init__(self, http: Callable[[], HttpJsonClient]) -> None:
        """Create a skill-set facade backed by a bound HTTP client accessor."""
        self._get_http = http

    def list(self, *, query: str | None = None, limit: int = 16) -> tuple[SkillSet, ...]:
        """List or search first-class skill sets visible to the caller.

        Listed sets carry ``member_skill_ids`` and ``parent_set_id`` but no
        ``children``; fetch one set with ``get`` to receive its whole tree.
        """
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
        items = SkillSetListResponse.model_validate(payload).items
        return tuple(record.to_skill_set() for record in items)

    def get(self, set_id: str) -> SkillSet:
        """Fetch one first-class skill set with its whole tree in ``children``."""
        return run_blocking(lambda: self.aget(set_id))

    async def aget(self, set_id: str) -> SkillSet:
        """Fetch one first-class skill set with its whole tree, asynchronously.

        ``children`` lists the set's member skills in ``member_skill_ids`` order,
        then its child sets, each assembled the same way. A member skill that no longer
        exists (deleted skills stay listed in their sets) is left out of ``children``.
        The tree costs one index request plus one request per distinct member skill
        (at most eight at once).
        """
        http = self._get_http()
        index = _SkillSetIndexRecord.model_validate(
            await http.get(SKILL_SET_INDEX_PATH.format(set_id=set_id)),
        )
        root = _set_tree(index)
        skill_ids = list(dict.fromkeys(_member_ids(root)))
        limit = asyncio.Semaphore(_MAX_CONCURRENT_SKILL_READS)

        async def read(skill_id: str) -> Skill | None:
            async with limit:
                try:
                    payload = await http.get(SKILL_PATH.format(skill_id=skill_id))
                except MaivnHTTPError as error:
                    # Deleting a skill leaves its id in every set that listed it.
                    if error.status_code == HTTPStatus.NOT_FOUND:
                        return None
                    raise
                return Skill.model_validate(payload)

        read_skills = await asyncio.gather(*map(read, skill_ids))
        skills = {
            skill_id: skill
            for skill_id, skill in zip(skill_ids, read_skills, strict=True)
            if skill is not None
        }
        return _assemble(root, skills)

    def create(self, skill_set: SkillSet) -> SkillSet:
        """Create one first-class skill set."""
        return run_blocking(lambda: self.acreate(skill_set))

    async def acreate(self, skill_set: SkillSet) -> SkillSet:
        """Create one first-class skill set asynchronously."""
        return _SkillSetRecord.model_validate(
            await self._get_http().post(SKILL_SETS_PATH, _dump_skill_set_create(skill_set)),
        ).to_skill_set()

    def add_skill(self, set_id: str, skill_id: str) -> SkillSet:
        """Add one skill to a skill set."""
        return run_blocking(lambda: self.aadd_skill(set_id, skill_id))

    async def aadd_skill(self, set_id: str, skill_id: str) -> SkillSet:
        """Add one skill to a skill set asynchronously."""
        path = SKILL_SET_SKILLS_PATH.format(set_id=set_id, skill_id=skill_id)
        return _SkillSetRecord.model_validate(await self._get_http().post(path, {})).to_skill_set()

    def add_child_set(self, set_id: str, child_set_id: str) -> SkillSet:
        """Nest one child skill set below another skill set."""
        return run_blocking(lambda: self.aadd_child_set(set_id, child_set_id))

    async def aadd_child_set(self, set_id: str, child_set_id: str) -> SkillSet:
        """Nest one child skill set below another skill set asynchronously."""
        path = SKILL_SET_CHILD_SET_PATH.format(set_id=set_id, child_set_id=child_set_id)
        return _SkillSetRecord.model_validate(await self._get_http().post(path, {})).to_skill_set()

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

    def for_user(self, user_id: str) -> SkillsClient:
        """Return independent customer management; derive user_id on a trusted backend."""
        http = self._get_http().for_user(user_id)
        return SkillsClient(lambda: http)

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


def _set_tree(index: _SkillSetIndexRecord) -> _SetNode:
    """Link the index's descendants below their parents, refusing over-deep trees."""
    by_parent: dict[str, list[_SkillSetRecord]] = {}
    for record in index.descendant_skill_sets:
        by_parent.setdefault(record.parent_set_id or '', []).append(record)
    root = index.skill_set
    visited = {root.id or ''}

    def node(record: _SkillSetRecord, depth: int) -> _SetNode:
        if depth > _MAX_SKILL_SET_DEPTH:
            message = f'skill set {root.id} nests deeper than {_MAX_SKILL_SET_DEPTH} levels'
            raise MaivnSDKError(message)
        children: list[_SetNode] = []
        for child in by_parent.get(record.id or '', ()):
            if (child.id or '') not in visited:
                visited.add(child.id or '')
                children.append(node(child, depth + 1))
        return _SetNode(record=record, children=tuple(children))

    return node(root, 1)


def _member_ids(node: _SetNode) -> Iterator[str]:
    yield from node.record.member_skill_ids
    for child in node.children:
        yield from _member_ids(child)


def _assemble(node: _SetNode, skills: Mapping[str, Skill]) -> SkillSet:
    members = tuple(
        skills[skill_id] for skill_id in node.record.member_skill_ids if skill_id in skills
    )
    nested = tuple(_assemble(child, skills) for child in node.children)
    return node.record.to_skill_set((*members, *nested))


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
    if scope.shared:
        payload['shared'] = True
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
