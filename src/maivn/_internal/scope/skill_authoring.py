"""Scope-side skill authoring: inline skill requests, skill sets, and auto-retrieval."""

from __future__ import annotations

from typing import TYPE_CHECKING

from maivn._internal.scope.types import AutoSkills
from maivn._internal.skills import (
    Skill,
    SkillScopeRequest,
    SkillSet,
    SkillStep,
    parse_skill_markdown,
)

if TYPE_CHECKING:
    from collections.abc import Sequence
    from pathlib import Path


_MAX_SKILL_RETRIEVAL_LIMIT = 16


def skill_request(
    skill: Skill | None,
    *,
    name: str | None,
    description: str | None,
    steps: Sequence[SkillStep | str] | None,
    scope: SkillScopeRequest,
) -> Skill:
    if skill is not None:
        if name is not None or description is not None or steps is not None:
            message = 'pass either a Skill or inline skill fields, not both'
            raise ValueError(message)
        return _skill_with_scope(skill, scope)
    if name is None or description is None or steps is None:
        message = 'inline add_skill requires name, description, and steps'
        raise ValueError(message)
    return Skill(
        scope=scope,
        name=name,
        description=description,
        steps=_skill_steps(steps),
    )


def skill_set_from_input(skill_set: SkillSet | str | Path) -> SkillSet:
    if isinstance(skill_set, SkillSet):
        return skill_set
    parsed = parse_skill_markdown(skill_set)
    if isinstance(parsed, SkillSet):
        return parsed
    message = 'add_skill_set path must resolve to a skill set'
    raise ValueError(message)


def _skill_with_scope(skill: Skill, scope: SkillScopeRequest) -> Skill:
    return skill.model_copy(update={'scope': scope})


def skill_set_with_scope(skill_set: SkillSet, scope: SkillScopeRequest) -> SkillSet:
    children = tuple(
        _skill_with_scope(child, scope)
        if isinstance(child, Skill)
        else skill_set_with_scope(child, scope)
        for child in skill_set.children
    )
    return skill_set.model_copy(update={'scope': scope, 'children': children})


def _skill_steps(steps: Sequence[SkillStep | str]) -> tuple[SkillStep, ...]:
    return tuple(
        step if isinstance(step, SkillStep) else SkillStep(index=index, action=step)
        for index, step in enumerate(steps, start=1)
    )


def agent_scope(agent_id: str) -> SkillScopeRequest:
    return SkillScopeRequest(agent_id=agent_id, sharing_scope='agent')


def swarm_scope(swarm_id: str) -> SkillScopeRequest:
    return SkillScopeRequest(swarm_id=swarm_id, sharing_scope='swarm')


def append_unique(values: list[str], value: str) -> None:
    if not value.strip():
        message = 'attached skill id must not be blank'
        raise ValueError(message)
    if value not in values:
        values.append(value)


def build_auto_skills(*, query: str, limit: int) -> AutoSkills:
    normalized_query = query.strip()
    if not normalized_query:
        message = 'skill retrieval query must not be blank'
        raise ValueError(message)
    if limit < 1:
        message = 'skill retrieval limit must be at least 1'
        raise ValueError(message)
    if limit > _MAX_SKILL_RETRIEVAL_LIMIT:
        message = f'skill retrieval limit must be at most {_MAX_SKILL_RETRIEVAL_LIMIT}'
        raise ValueError(message)
    return AutoSkills(query=normalized_query, limit=limit)
