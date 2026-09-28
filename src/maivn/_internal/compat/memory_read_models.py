"""V1-compatible memory read models."""

from __future__ import annotations

from typing import ClassVar, Literal, TypeAlias

from pydantic import BaseModel, ConfigDict, Field

JsonObject: TypeAlias = dict[str, object]
MemoryPersistenceCeiling: TypeAlias = Literal['persist_none', 'vector_only', 'vector_plus_graph']
MemorySharingScope: TypeAlias = Literal['agent', 'swarm', 'project', 'org']
MemorySkillOrigin: TypeAlias = Literal['user_defined', 'ai_generated']
MemorySkillStatus: TypeAlias = Literal['active', 'deprecated', 'quarantined']
MemoryInsightType: TypeAlias = Literal['lesson', 'warning', 'optimization', 'failure_pattern']
MemoryInsightOrigin: TypeAlias = Literal['ai_generated', 'user_promoted']
MemoryResourceBindingType: TypeAlias = Literal['message', 'agent', 'swarm', 'portal', 'unbound']
MemoryResourceStatus: TypeAlias = Literal['registered', 'superseded', 'deleted', 'error']


class OrganizationMemoryPolicy(BaseModel):
    """Organization-wide ceiling and retention policy for memory persistence."""

    model_config: ClassVar[ConfigDict] = ConfigDict(extra='ignore')

    enabled: bool = True
    persistence_ceiling: MemoryPersistenceCeiling
    vector_retention_days: int | None = None
    graph_retention_days: int | None = None


class OrganizationMemoryPurgeResult(BaseModel):
    """Result envelope returned by an organization-memory purge call."""

    model_config: ClassVar[ConfigDict] = ConfigDict(extra='ignore')

    success: bool
    project_ids: list[str] = Field(default_factory=list)
    session_id: str | None = None
    tables: list[str] = Field(default_factory=list)


class MemorySkill(BaseModel):
    """Reusable scoped procedural memory tracked by the server."""

    model_config: ClassVar[ConfigDict] = ConfigDict(extra='ignore')

    id: str
    project_id: str
    organization_id: str | None = None
    agent_id: str | None = None
    swarm_id: str | None = None
    sharing_scope: MemorySharingScope
    name: str
    description: str
    steps: list[JsonObject] = Field(default_factory=list)
    preconditions: JsonObject = Field(default_factory=dict)
    postconditions: JsonObject = Field(default_factory=dict)
    version: int = 1
    confidence: float = 1.0
    application_count: int = 0
    success_rate: float = 0.0
    origin: MemorySkillOrigin = 'user_defined'
    status: MemorySkillStatus = 'active'
    created_at: str | None = None
    updated_at: str | None = None


class MemoryInsight(BaseModel):
    """Scoped declarative memory item."""

    model_config: ClassVar[ConfigDict] = ConfigDict(extra='ignore')

    id: str
    project_id: str
    organization_id: str | None = None
    agent_id: str | None = None
    swarm_id: str | None = None
    sharing_scope: MemorySharingScope
    insight_type: MemoryInsightType
    content: str
    relevance_score: float
    decay_model: str = 'linear'
    half_life_days: int = 30
    ttl_days: int | None = None
    origin: MemoryInsightOrigin = 'ai_generated'
    promoted_from_id: str | None = None
    expires_at: str | None = None
    created_at: str | None = None
    updated_at: str | None = None


class MemoryResource(BaseModel):
    """Registered file/document-style memory resource summary."""

    model_config: ClassVar[ConfigDict] = ConfigDict(extra='ignore')

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
    extracted_page_count: int = 0
    chunk_count: int = 0
    source_type: str = 'upload'
    source_url: str | None = None
    query_count: int = 0
    last_queried_at: str | None = None
    cleanup_candidate: bool = False
    cleanup_candidate_reason: str | None = None
    created_at: str | None = None
    updated_at: str | None = None


class MemoryResourceDetail(MemoryResource):
    """Full view of a memory resource, including storage and version chain."""

    content_hash: str
    storage_bucket: str
    storage_path: str
    metadata: JsonObject = Field(default_factory=dict)
    superseded_by: str | None = None
    replaces_resource_id: str | None = None
    extractor_version: str = 'v2'
    version_chain: list[JsonObject] = Field(default_factory=list)
    extraction_stats: JsonObject = Field(default_factory=dict)


class MemoryUnboundResourceCandidate(BaseModel):
    """Resource currently in the unbound pool."""

    model_config: ClassVar[ConfigDict] = ConfigDict(extra='ignore')

    id: str
    project_id: str
    organization_id: str | None = None
    name: str
    binding_type: MemoryResourceBindingType
    registration_status: MemoryResourceStatus
    query_count: int = 0
    last_queried_at: str | None = None
    unbound_at: str | None = None
    cleanup_candidate_reason: str | None = None
    created_at: str | None = None
    updated_at: str | None = None


class ProjectMemoryResources(BaseModel):
    """Aggregated view of a project's skills, insights, and resources."""

    model_config: ClassVar[ConfigDict] = ConfigDict(extra='ignore')

    skills: list[MemorySkill] = Field(default_factory=list)
    insights: list[MemoryInsight] = Field(default_factory=list)
    resources: list[MemoryResource] = Field(default_factory=list)


__all__ = [
    'MemoryInsight',
    'MemoryInsightOrigin',
    'MemoryInsightType',
    'MemoryPersistenceCeiling',
    'MemoryResource',
    'MemoryResourceBindingType',
    'MemoryResourceDetail',
    'MemoryResourceStatus',
    'MemorySharingScope',
    'MemorySkill',
    'MemorySkillOrigin',
    'MemorySkillStatus',
    'MemoryUnboundResourceCandidate',
    'OrganizationMemoryPolicy',
    'OrganizationMemoryPurgeResult',
    'ProjectMemoryResources',
]
