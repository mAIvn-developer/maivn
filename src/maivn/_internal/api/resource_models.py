from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field

MemoryPersistenceCeiling = Literal["persist_none", "vector_only", "vector_plus_graph"]
MemorySharingScope = Literal["agent", "swarm", "project", "org"]
MemorySkillOrigin = Literal["user_defined", "ai_generated"]
MemorySkillStatus = Literal["active", "deprecated", "quarantined"]
MemoryInsightType = Literal["lesson", "warning", "optimization", "failure_pattern"]
MemoryInsightOrigin = Literal["ai_generated", "user_promoted"]
MemoryResourceBindingType = Literal["message", "agent", "swarm", "portal", "unbound"]
MemoryResourceStatus = Literal["registered", "superseded", "deleted", "error"]


class OrganizationMemoryPolicy(BaseModel):
    enabled: bool = True
    persistence_ceiling: MemoryPersistenceCeiling
    vector_retention_days: int | None = None
    graph_retention_days: int | None = None


class OrganizationMemoryPurgeResult(BaseModel):
    success: bool
    project_ids: list[str] = Field(default_factory=list)
    session_id: str | None = None
    tables: list[str] = Field(default_factory=list)


class MemorySkill(BaseModel):
    id: str
    project_id: str
    organization_id: str | None = None
    agent_id: str | None = None
    swarm_id: str | None = None
    sharing_scope: MemorySharingScope
    name: str
    description: str
    steps: list[dict[str, Any]] = Field(default_factory=list)
    preconditions: dict[str, Any] = Field(default_factory=dict)
    postconditions: dict[str, Any] = Field(default_factory=dict)
    version: int
    confidence: float
    application_count: int
    success_rate: float
    origin: MemorySkillOrigin
    status: MemorySkillStatus
    created_at: str | None = None
    updated_at: str | None = None


class MemoryInsight(BaseModel):
    id: str
    project_id: str
    organization_id: str | None = None
    agent_id: str | None = None
    swarm_id: str | None = None
    sharing_scope: MemorySharingScope
    insight_type: MemoryInsightType
    content: str
    relevance_score: float
    decay_model: str
    half_life_days: int
    ttl_days: int | None = None
    origin: MemoryInsightOrigin
    promoted_from_id: str | None = None
    expires_at: str | None = None
    created_at: str | None = None
    updated_at: str | None = None


class MemoryResource(BaseModel):
    id: str
    project_id: str
    organization_id: str | None = None
    resource_thread_id: str
    name: str
    description: str | None = None
    tags: list[str] = Field(default_factory=list)
    format: str
    size_bytes: int
    sharing_scope: MemorySharingScope
    binding_type: MemoryResourceBindingType
    bound_agent_id: str | None = None
    bound_swarm_id: str | None = None
    registration_status: MemoryResourceStatus
    page_count: int | None = None
    extracted_page_count: int
    chunk_count: int
    source_type: str
    source_url: str | None = None
    query_count: int
    last_queried_at: str | None = None
    cleanup_candidate: bool
    cleanup_candidate_reason: str | None = None
    created_at: str | None = None
    updated_at: str | None = None


class MemoryResourceDetail(MemoryResource):
    content_hash: str
    storage_bucket: str
    storage_path: str
    metadata: dict[str, Any] = Field(default_factory=dict)
    superseded_by: str | None = None
    replaces_resource_id: str | None = None
    extractor_version: str
    version_chain: list[dict[str, Any]] = Field(default_factory=list)
    extraction_stats: dict[str, Any] = Field(default_factory=dict)


class MemoryUnboundResourceCandidate(BaseModel):
    id: str
    project_id: str
    organization_id: str | None = None
    name: str
    binding_type: MemoryResourceBindingType
    registration_status: MemoryResourceStatus
    query_count: int
    last_queried_at: str | None = None
    unbound_at: str | None = None
    cleanup_candidate_reason: str | None = None
    created_at: str | None = None
    updated_at: str | None = None


class ProjectMemoryResources(BaseModel):
    skills: list[MemorySkill] = Field(default_factory=list)
    insights: list[MemoryInsight] = Field(default_factory=list)
    resources: list[MemoryResource] = Field(default_factory=list)


__all__ = [
    "MemoryResource",
    "MemoryResourceBindingType",
    "MemoryResourceDetail",
    "MemoryResourceStatus",
    "MemoryInsight",
    "MemoryInsightOrigin",
    "MemoryInsightType",
    "MemoryPersistenceCeiling",
    "MemorySharingScope",
    "MemorySkill",
    "MemorySkillOrigin",
    "MemorySkillStatus",
    "MemoryUnboundResourceCandidate",
    "OrganizationMemoryPolicy",
    "OrganizationMemoryPurgeResult",
    "ProjectMemoryResources",
]
