"""Public Pydantic models for the SDK memory-resource API.

These models mirror the canonical wire shapes returned by ``maivn-server``'s
memory endpoints (skills, insights, resources) plus the policy/purge result
envelopes used by the organization memory controls. The SDK exposes them so
consumers can type-check responses without duplicating field declarations.
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field

MemoryPersistenceCeiling = Literal["persist_none", "vector_only", "vector_plus_graph"]
"""Maximum persistence tier permitted for memory writes in an organization."""

MemorySharingScope = Literal["agent", "swarm", "project", "org"]
"""Visibility scope for a memory skill, insight, or resource."""

MemorySkillOrigin = Literal["user_defined", "ai_generated"]
"""How a memory skill came to exist (developer-authored vs LLM-extracted)."""

MemorySkillStatus = Literal["active", "deprecated", "quarantined"]
"""Lifecycle state of a memory skill."""

MemoryInsightType = Literal["lesson", "warning", "optimization", "failure_pattern"]
"""Categorical kind for an extracted memory insight."""

MemoryInsightOrigin = Literal["ai_generated", "user_promoted"]
"""How a memory insight came to exist."""

MemoryResourceBindingType = Literal["message", "agent", "swarm", "portal", "unbound"]
"""Where a memory resource is anchored (or whether it is unbound)."""

MemoryResourceStatus = Literal["registered", "superseded", "deleted", "error"]
"""Registration state of a memory resource."""


class OrganizationMemoryPolicy(BaseModel):
    """Organization-wide ceiling and retention policy for memory persistence."""

    enabled: bool = True
    persistence_ceiling: MemoryPersistenceCeiling
    vector_retention_days: int | None = None
    graph_retention_days: int | None = None


class OrganizationMemoryPurgeResult(BaseModel):
    """Result envelope returned by an organization-memory purge call."""

    success: bool
    project_ids: list[str] = Field(default_factory=list)
    session_id: str | None = None
    tables: list[str] = Field(default_factory=list)


class MemorySkill(BaseModel):
    """A reusable, scoped procedural memory ("skill") tracked by the server.

    Skills capture step-by-step procedures with pre/postconditions and rolling
    success metrics; they are surfaced to agents that operate within the
    skill's ``sharing_scope``.
    """

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
    """A scoped declarative memory item (lesson, warning, optimization, …).

    Insights are short pieces of distilled knowledge with a decay model and
    relevance score; they are retrieved alongside skills and resources during
    memory enrichment.
    """

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
    """A registered file/document-style memory resource (without content body).

    The summary view used in listings — call ``MemoryResourceDetail`` to get
    the storage and version-chain fields.
    """

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
    """Full view of a memory resource, including storage and version chain.

    Extends :class:`MemoryResource` with the bytes-level locator
    (``storage_bucket`` / ``storage_path``), the supersede/replace pointers,
    extractor metadata, and the chronological version chain.
    """

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
    """A resource currently in the ``unbound`` binding-type pool.

    Returned by cleanup-candidate listings so operators can decide whether to
    rebind or purge each entry.
    """

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
    """Aggregated view of a project's skills, insights, and resources."""

    skills: list[MemorySkill] = Field(default_factory=list)
    insights: list[MemoryInsight] = Field(default_factory=list)
    resources: list[MemoryResource] = Field(default_factory=list)


__all__ = [
    "MemoryInsight",
    "MemoryInsightOrigin",
    "MemoryInsightType",
    "MemoryPersistenceCeiling",
    "MemoryResource",
    "MemoryResourceBindingType",
    "MemoryResourceDetail",
    "MemoryResourceStatus",
    "MemorySharingScope",
    "MemorySkill",
    "MemorySkillOrigin",
    "MemorySkillStatus",
    "MemoryUnboundResourceCandidate",
    "OrganizationMemoryPolicy",
    "OrganizationMemoryPurgeResult",
    "ProjectMemoryResources",
]
