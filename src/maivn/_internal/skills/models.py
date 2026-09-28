"""Typed SDK models for first-class skill authoring."""

from __future__ import annotations

from typing import Annotated, Any, Literal, TypeAlias

from pydantic import BaseModel, ConfigDict, Field, StrictInt, StrictStr

JsonObject: TypeAlias = dict[str, Any]
SkillSharingScope: TypeAlias = Literal['agent', 'swarm', 'project', 'org']
SkillOrigin: TypeAlias = Literal['user_defined', 'ai_generated']
SkillStatus: TypeAlias = Literal['active', 'deprecated', 'quarantined']
Confidence: TypeAlias = Annotated[float, Field(ge=0.0, le=1.0)]


class SkillScopeRequest(BaseModel):
    """Request scope for first-class skills and skill sets."""

    model_config = ConfigDict(extra='forbid', frozen=True)

    project_id: StrictStr | None = Field(default=None, min_length=1)
    organization_id: StrictStr | None = Field(default=None, min_length=1)
    agent_id: StrictStr | None = Field(default=None, min_length=1)
    swarm_id: StrictStr | None = Field(default=None, min_length=1)
    sharing_scope: SkillSharingScope = 'project'


class SkillStep(BaseModel):
    """One executable step inside a first-class skill."""

    model_config = ConfigDict(extra='forbid', frozen=True)

    index: StrictInt = Field(..., ge=1)
    action: StrictStr = Field(..., min_length=1, max_length=240)
    tool: StrictStr | None = Field(default=None, min_length=1, max_length=160)
    parameters: JsonObject = Field(default_factory=dict)
    expected_output: StrictStr | None = Field(default=None, min_length=1, max_length=400)


class Skill(BaseModel):
    """SDK-owned model for authoring and receiving first-class skill records."""

    model_config = ConfigDict(extra='forbid', frozen=True)

    kind: Literal['skill'] = 'skill'
    memory_id: StrictStr | None = Field(default=None, min_length=1, max_length=160)
    scope: SkillScopeRequest = Field(default_factory=SkillScopeRequest)
    name: StrictStr = Field(..., min_length=1, max_length=160)
    description: StrictStr = Field(..., min_length=1, max_length=4000)
    steps: tuple[SkillStep, ...] = Field(..., min_length=1, max_length=8)
    preconditions: JsonObject = Field(default_factory=dict)
    postconditions: JsonObject = Field(default_factory=dict)
    confidence: Confidence = 1.0
    origin: SkillOrigin = 'user_defined'
    status: SkillStatus = 'active'
    created_at: StrictStr | None = Field(default=None, min_length=1)
    updated_at: StrictStr | None = Field(default=None, min_length=1)
    source_session_id: StrictStr | None = Field(default=None, min_length=1)
    source_invocation_id: StrictStr | None = Field(default=None, min_length=1)
    set_id: StrictStr | None = Field(default=None, min_length=1)
    set_path: tuple[StrictStr, ...] | None = Field(default=None, min_length=1, max_length=16)


class SkillSet(BaseModel):
    """SDK-owned model for authoring and receiving skill-set trees."""

    model_config = ConfigDict(extra='forbid', frozen=True)

    kind: Literal['skill_set'] = 'skill_set'
    id: StrictStr | None = Field(default=None, min_length=1, max_length=160)
    scope: SkillScopeRequest = Field(default_factory=SkillScopeRequest)
    name: StrictStr = Field(..., min_length=1, max_length=160)
    description: StrictStr = Field(..., min_length=1, max_length=4000)
    parent_set_id: StrictStr | None = Field(default=None, min_length=1, max_length=160)
    member_skill_ids: tuple[StrictStr, ...] = Field(default=(), max_length=256)
    children: tuple[Skill | SkillSet, ...] = ()


SkillSet.model_rebuild()

__all__ = [
    'JsonObject',
    'Skill',
    'SkillOrigin',
    'SkillScopeRequest',
    'SkillSet',
    'SkillSharingScope',
    'SkillStatus',
    'SkillStep',
]
