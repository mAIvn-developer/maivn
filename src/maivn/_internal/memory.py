"""Typed SDK client surface for memory routes."""

from __future__ import annotations

from typing import TYPE_CHECKING, Annotated, Any, Literal, TypeAlias
from urllib.parse import quote

from pydantic import BaseModel, ConfigDict, Field, StrictBool, StrictInt, StrictStr

from maivn._internal.api.async_stream import run_blocking
from maivn._internal.wire import (
    MEMORY_ENRICHMENTS_PATH,
    MEMORY_INSIGHT_PATH,
    MEMORY_INSIGHT_PROMOTE_PATH,
    MEMORY_INSIGHTS_PATH,
    MEMORY_SKILL_PATH,
    MEMORY_SKILLS_PATH,
)

if TYPE_CHECKING:
    from collections.abc import Callable

    from maivn._internal.transport.http import HttpJsonClient

JsonObject: TypeAlias = dict[str, Any]
MemorySharingScope: TypeAlias = Literal['agent', 'swarm', 'project', 'org']
SkillOrigin: TypeAlias = Literal['user_defined', 'ai_generated']
SkillStatus: TypeAlias = Literal['active', 'deprecated', 'quarantined']
InsightType: TypeAlias = Literal['lesson', 'warning', 'optimization', 'failure_pattern']
InsightOrigin: TypeAlias = Literal['ai_generated', 'user_promoted']
LiteralPromotionScope: TypeAlias = Literal['project', 'org']
Confidence: TypeAlias = Annotated[float, Field(ge=0.0, le=1.0)]


class MemoryScopeRequest(BaseModel):
    """Request scope for creating or updating memory records."""

    model_config = ConfigDict(extra='forbid', frozen=True)

    project_id: StrictStr | None = Field(default=None, min_length=1)
    organization_id: StrictStr | None = Field(default=None, min_length=1)
    agent_id: StrictStr | None = Field(default=None, min_length=1)
    swarm_id: StrictStr | None = Field(default=None, min_length=1)
    sharing_scope: MemorySharingScope = 'project'


class MemoryScope(BaseModel):
    """Tenant, agent, and sharing scope returned on memory records."""

    model_config = ConfigDict(extra='forbid', frozen=True)

    project_id: StrictStr = Field(..., min_length=1)
    organization_id: StrictStr | None = Field(default=None, min_length=1)
    agent_id: StrictStr | None = Field(default=None, min_length=1)
    swarm_id: StrictStr | None = Field(default=None, min_length=1)
    sharing_scope: MemorySharingScope = 'project'


class MemorySkillStep(BaseModel):
    """One executable step inside a skill memory record."""

    model_config = ConfigDict(extra='forbid', frozen=True)

    index: StrictInt = Field(..., ge=1)
    action: StrictStr = Field(..., min_length=1, max_length=240)
    tool: StrictStr | None = Field(default=None, min_length=1, max_length=160)
    parameters: JsonObject = Field(default_factory=dict)
    expected_output: StrictStr | None = Field(default=None, min_length=1, max_length=400)


class MemorySkillCreate(BaseModel):
    """Request body for creating a skill memory record."""

    model_config = ConfigDict(extra='forbid', frozen=True)

    memory_id: StrictStr | None = Field(default=None, min_length=1)
    scope: MemoryScopeRequest = Field(default_factory=MemoryScopeRequest)
    name: StrictStr = Field(..., min_length=1, max_length=160)
    description: StrictStr = Field(..., min_length=1, max_length=4000)
    steps: tuple[MemorySkillStep, ...] = Field(..., min_length=1, max_length=8)
    preconditions: JsonObject = Field(default_factory=dict)
    postconditions: JsonObject = Field(default_factory=dict)
    confidence: Confidence = 1.0
    origin: SkillOrigin = 'user_defined'
    status: SkillStatus = 'active'
    source_session_id: StrictStr | None = Field(default=None, min_length=1)
    source_invocation_id: StrictStr | None = Field(default=None, min_length=1)


class MemorySkillUpdate(BaseModel):
    """Request body for updating a skill memory record."""

    model_config = ConfigDict(extra='forbid', frozen=True)

    scope: MemoryScopeRequest | None = None
    name: StrictStr | None = Field(default=None, min_length=1, max_length=160)
    description: StrictStr | None = Field(default=None, min_length=1, max_length=4000)
    steps: tuple[MemorySkillStep, ...] | None = Field(default=None, min_length=1, max_length=8)
    preconditions: JsonObject | None = None
    postconditions: JsonObject | None = None
    confidence: Confidence | None = None
    status: SkillStatus | None = None


class MemorySkill(BaseModel):
    """Typed SDK skill memory record."""

    model_config = ConfigDict(extra='forbid', frozen=True)

    kind: Literal['skill'] = 'skill'
    memory_id: StrictStr = Field(..., min_length=1)
    scope: MemoryScope
    name: StrictStr = Field(..., min_length=1, max_length=160)
    description: StrictStr = Field(..., min_length=1, max_length=4000)
    steps: tuple[MemorySkillStep, ...] = Field(..., min_length=1, max_length=8)
    preconditions: JsonObject = Field(default_factory=dict)
    postconditions: JsonObject = Field(default_factory=dict)
    confidence: Confidence
    origin: SkillOrigin
    status: SkillStatus
    created_at: StrictStr = Field(..., min_length=1)
    updated_at: StrictStr = Field(..., min_length=1)
    source_session_id: StrictStr | None = Field(default=None, min_length=1)
    source_invocation_id: StrictStr | None = Field(default=None, min_length=1)
    set_id: StrictStr | None = Field(default=None, min_length=1)
    set_path: tuple[StrictStr, ...] | None = Field(default=None)


class MemoryInsightCreate(BaseModel):
    """Request body for creating an insight memory record."""

    model_config = ConfigDict(extra='forbid', frozen=True)

    memory_id: StrictStr | None = Field(default=None, min_length=1)
    scope: MemoryScopeRequest = Field(default_factory=MemoryScopeRequest)
    insight_type: InsightType
    content: StrictStr = Field(..., min_length=1, max_length=4000)
    relevance_score: Confidence = 0.7
    origin: InsightOrigin = 'user_promoted'
    ttl_days: StrictInt | None = Field(default=None, ge=1, le=3650)
    half_life_days: StrictInt | None = Field(default=None, ge=1, le=365)
    context_signature: StrictStr | None = Field(default=None, min_length=1, max_length=256)
    source_session_id: StrictStr | None = Field(default=None, min_length=1)
    source_invocation_id: StrictStr | None = Field(default=None, min_length=1)


class MemoryInsightUpdate(BaseModel):
    """Request body for updating an insight memory record."""

    model_config = ConfigDict(extra='forbid', frozen=True)

    scope: MemoryScopeRequest | None = None
    insight_type: InsightType | None = None
    content: StrictStr | None = Field(default=None, min_length=1, max_length=4000)
    relevance_score: Confidence | None = None
    ttl_days: StrictInt | None = Field(default=None, ge=1, le=3650)
    half_life_days: StrictInt | None = Field(default=None, ge=1, le=365)
    context_signature: StrictStr | None = Field(default=None, min_length=1, max_length=256)


class MemoryInsight(BaseModel):
    """Typed SDK insight memory record."""

    model_config = ConfigDict(extra='forbid', frozen=True)

    kind: Literal['insight'] = 'insight'
    memory_id: StrictStr = Field(..., min_length=1)
    scope: MemoryScope
    insight_type: InsightType
    content: StrictStr = Field(..., min_length=1, max_length=4000)
    relevance_score: Confidence
    origin: InsightOrigin
    ttl_days: StrictInt | None = Field(default=None, ge=1)
    half_life_days: StrictInt | None = Field(default=None, ge=1)
    context_signature: StrictStr | None = Field(default=None, min_length=1, max_length=256)
    created_at: StrictStr = Field(..., min_length=1)
    updated_at: StrictStr = Field(..., min_length=1)
    source_session_id: StrictStr | None = Field(default=None, min_length=1)
    source_invocation_id: StrictStr | None = Field(default=None, min_length=1)


class MemoryDeleteResponse(BaseModel):
    """Typed SDK response for successful memory deletes."""

    model_config = ConfigDict(extra='forbid', frozen=True)

    success: StrictBool


class _MemorySkillListResponse(BaseModel):
    """Internal list envelope for skill memory records."""

    model_config = ConfigDict(extra='forbid', frozen=True)

    items: tuple[MemorySkill, ...] = ()


class _MemoryInsightListResponse(BaseModel):
    """Internal list envelope for insight memory records."""

    model_config = ConfigDict(extra='forbid', frozen=True)

    items: tuple[MemoryInsight, ...] = ()


class MemorySkillsClient:
    """Skill memory collection facade."""

    def __init__(self, http: Callable[[], HttpJsonClient]) -> None:
        """Create a skill memory facade backed by a bound HTTP client accessor."""
        self._get_http = http

    def list(self, *, query: str | None = None, limit: int = 16) -> tuple[MemorySkill, ...]:
        """List skill memory records visible to the caller."""
        return run_blocking(lambda: self.alist(query=query, limit=limit))

    async def alist(
        self,
        *,
        query: str | None = None,
        limit: int = 16,
    ) -> tuple[MemorySkill, ...]:
        """List skill memory records asynchronously."""
        payload = await self._get_http().get(
            MEMORY_SKILLS_PATH,
            params=_list_params(query, limit),
        )
        return _MemorySkillListResponse.model_validate(payload).items

    def get(self, memory_id: str) -> MemorySkill:
        """Fetch one skill memory record."""
        return run_blocking(lambda: self.aget(memory_id))

    async def aget(self, memory_id: str) -> MemorySkill:
        """Fetch one skill memory record asynchronously."""
        path = MEMORY_SKILL_PATH.format(memory_id=memory_id)
        return MemorySkill.model_validate(await self._get_http().get(path))

    def create(self, request: MemorySkillCreate) -> MemorySkill:
        """Create one skill memory record."""
        return run_blocking(lambda: self.acreate(request))

    async def acreate(self, request: MemorySkillCreate) -> MemorySkill:
        """Create one skill memory record asynchronously."""
        return MemorySkill.model_validate(
            await self._get_http().post(MEMORY_SKILLS_PATH, _dump_model(request)),
        )

    def update(self, memory_id: str, request: MemorySkillUpdate) -> MemorySkill:
        """Update one skill memory record."""
        return run_blocking(lambda: self.aupdate(memory_id, request))

    async def aupdate(self, memory_id: str, request: MemorySkillUpdate) -> MemorySkill:
        """Update one skill memory record asynchronously."""
        path = MEMORY_SKILL_PATH.format(memory_id=memory_id)
        return MemorySkill.model_validate(
            await self._get_http().patch(path, _dump_model(request)),
        )

    def delete(self, memory_id: str) -> MemoryDeleteResponse:
        """Soft-delete one skill memory record."""
        return run_blocking(lambda: self.adelete(memory_id))

    async def adelete(self, memory_id: str) -> MemoryDeleteResponse:
        """Soft-delete one skill memory record asynchronously."""
        path = MEMORY_SKILL_PATH.format(memory_id=memory_id)
        return MemoryDeleteResponse.model_validate(await self._get_http().delete(path))


class MemoryInsightsClient:
    """Insight memory collection facade."""

    def __init__(self, http: Callable[[], HttpJsonClient]) -> None:
        """Create an insight memory facade backed by a bound HTTP client accessor."""
        self._get_http = http

    def list(self, *, query: str | None = None, limit: int = 16) -> tuple[MemoryInsight, ...]:
        """List insight memory records visible to the caller."""
        return run_blocking(lambda: self.alist(query=query, limit=limit))

    async def alist(
        self,
        *,
        query: str | None = None,
        limit: int = 16,
    ) -> tuple[MemoryInsight, ...]:
        """List insight memory records asynchronously."""
        payload = await self._get_http().get(
            MEMORY_INSIGHTS_PATH,
            params=_list_params(query, limit),
        )
        return _MemoryInsightListResponse.model_validate(payload).items

    def get(self, memory_id: str) -> MemoryInsight:
        """Fetch one insight memory record."""
        return run_blocking(lambda: self.aget(memory_id))

    async def aget(self, memory_id: str) -> MemoryInsight:
        """Fetch one insight memory record asynchronously."""
        path = MEMORY_INSIGHT_PATH.format(memory_id=memory_id)
        return MemoryInsight.model_validate(await self._get_http().get(path))

    def create(self, request: MemoryInsightCreate) -> MemoryInsight:
        """Create one insight memory record."""
        return run_blocking(lambda: self.acreate(request))

    async def acreate(self, request: MemoryInsightCreate) -> MemoryInsight:
        """Create one insight memory record asynchronously."""
        return MemoryInsight.model_validate(
            await self._get_http().post(MEMORY_INSIGHTS_PATH, _dump_model(request)),
        )

    def update(self, memory_id: str, request: MemoryInsightUpdate) -> MemoryInsight:
        """Update one insight memory record."""
        return run_blocking(lambda: self.aupdate(memory_id, request))

    async def aupdate(self, memory_id: str, request: MemoryInsightUpdate) -> MemoryInsight:
        """Update one insight memory record asynchronously."""
        path = MEMORY_INSIGHT_PATH.format(memory_id=memory_id)
        return MemoryInsight.model_validate(
            await self._get_http().patch(path, _dump_model(request)),
        )

    def promote(self, memory_id: str, *, target_scope: LiteralPromotionScope) -> MemoryInsight:
        """Promote one insight to a durable project or org scope."""
        return run_blocking(lambda: self.apromote(memory_id, target_scope=target_scope))

    async def apromote(
        self,
        memory_id: str,
        *,
        target_scope: LiteralPromotionScope,
    ) -> MemoryInsight:
        """Promote one insight asynchronously to a durable project or org scope."""
        path = MEMORY_INSIGHT_PROMOTE_PATH.format(memory_id=memory_id)
        return MemoryInsight.model_validate(
            await self._get_http().post(path, {'target_scope': target_scope}),
        )

    def delete(self, memory_id: str) -> MemoryDeleteResponse:
        """Delete one insight memory record."""
        return run_blocking(lambda: self.adelete(memory_id))

    async def adelete(self, memory_id: str) -> MemoryDeleteResponse:
        """Delete one insight memory record asynchronously."""
        path = MEMORY_INSIGHT_PATH.format(memory_id=memory_id)
        return MemoryDeleteResponse.model_validate(await self._get_http().delete(path))


class MemoryEnrichmentReceipt(BaseModel):
    """Persisted extraction outcome; completed can legitimately contain zero assets."""

    model_config = ConfigDict(extra='forbid', frozen=True)

    session_id: StrictStr
    invocation_id: StrictStr
    status: Literal['completed', 'failed']
    skill_count: StrictInt = Field(ge=0)
    insight_count: StrictInt = Field(ge=0)
    graph_edge_count: StrictInt = Field(default=0, ge=0)
    created_at: StrictStr


class _MemoryEnrichmentListResponse(BaseModel):
    model_config = ConfigDict(extra='forbid', frozen=True)

    items: tuple[MemoryEnrichmentReceipt, ...] = ()


class MemoryEnrichmentsClient:
    """Read durable outcomes after a run; no receipts does not imply completion."""

    def __init__(self, http: Callable[[], HttpJsonClient]) -> None:
        """Bind the authenticated HTTP accessor."""
        self._get_http = http

    def list(self, session_id: str) -> tuple[MemoryEnrichmentReceipt, ...]:
        """Read known terminal outcomes for one session in the caller's project."""
        return run_blocking(lambda: self.alist(session_id))

    async def alist(self, session_id: str) -> tuple[MemoryEnrichmentReceipt, ...]:
        """Read known terminal outcomes asynchronously."""
        if not session_id:
            message = 'session_id must not be empty'
            raise ValueError(message)
        path = MEMORY_ENRICHMENTS_PATH.format(session_id=quote(session_id, safe=''))
        payload = await self._get_http().get(path)
        return _MemoryEnrichmentListResponse.model_validate(payload).items


class MemoryClient:
    """Root facade for memory routes."""

    def __init__(self, http: Callable[[], HttpJsonClient]) -> None:
        """Create memory collection facades backed by a bound HTTP client accessor."""
        self.skills = MemorySkillsClient(http)
        self.insights = MemoryInsightsClient(http)
        self.enrichments = MemoryEnrichmentsClient(http)


def _list_params(query: str | None, limit: int) -> dict[str, str | int]:
    params: dict[str, str | int] = {'limit': limit}
    if query is not None:
        params['query'] = query
    return params


def _dump_model(model: BaseModel) -> JsonObject:
    return model.model_dump(mode='json', exclude_none=True, exclude_defaults=True)


__all__ = [
    'MemoryClient',
    'MemoryDeleteResponse',
    'MemoryEnrichmentReceipt',
    'MemoryInsight',
    'MemoryInsightCreate',
    'MemoryInsightUpdate',
    'MemoryScope',
    'MemoryScopeRequest',
    'MemorySkill',
    'MemorySkillCreate',
    'MemorySkillStep',
    'MemorySkillUpdate',
]
