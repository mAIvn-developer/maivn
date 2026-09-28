"""V1-compatible typed option models for SDK callers."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import ClassVar, Literal, TypeAlias, cast

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

JsonObject: TypeAlias = dict[str, object]
ModelTier: TypeAlias = Literal['auto', 'fast', 'balanced', 'max', 'ultra']
PlanningTier: TypeAlias = Literal['auto', 'standard', 'advanced', 'deep']
ModelConfigPart: TypeAlias = Literal['response', 'thinking', 'compose_argument', 'repl', 'planning']
MemoryLevel: TypeAlias = Literal['none', 'glimpse', 'focus', 'clarity']
MemoryPersistenceMode: TypeAlias = Literal['persist_none', 'vector_only', 'vector_plus_graph']
MemorySharingScope: TypeAlias = Literal['agent', 'swarm', 'project', 'org']
OrchestrationMode: TypeAlias = Literal[
    'single_shot_dag',
    'supervisor_loop',
    'strict_user_dag',
    'hybrid',
]
FinalOutputMode: TypeAlias = Literal['terminal', 'supervised', 'aggregator_only']
StopStrategy: TypeAlias = Literal[
    'orchestrator_decides',
    'final_tool_completed',
    'objective_satisfied',
    'max_cycles',
    'blocker_detected',
]
_MODEL_TIERS = frozenset(('auto', 'fast', 'balanced', 'max', 'ultra'))
_PLANNING_TIERS = frozenset(('auto', 'standard', 'advanced', 'deep'))


class ModelChoice(BaseModel):
    """A single scoped model choice: either a tier or an exact model id."""

    model_config: ClassVar[ConfigDict] = ConfigDict(extra='forbid')

    tier: ModelTier | None = None
    model_id: str | None = None

    @model_validator(mode='before')
    @classmethod
    def _coerce_string_choice(cls, value: object) -> object:
        if isinstance(value, str):
            stripped = value.strip()
            if not stripped:
                message = 'model choice strings must not be empty'
                raise ValueError(message)
            if stripped in _MODEL_TIERS:
                return {'tier': stripped}
            return {'model_id': stripped}
        return value

    @model_validator(mode='after')
    def _validate_exactly_one_choice(self) -> ModelChoice:
        if (self.tier is None) == (self.model_id is None):
            message = 'exactly one of tier or model_id must be set'
            raise ValueError(message)
        return self


class PlanningChoice(BaseModel):
    """A single planning choice: either a planning tier or an exact model id."""

    model_config: ClassVar[ConfigDict] = ConfigDict(
        extra='forbid',
        json_schema_extra={
            'x-maivn-closed-union-validation': True,
            'oneOf': [
                {
                    'type': 'object',
                    'additionalProperties': False,
                    'required': ['tier'],
                    'properties': {
                        'tier': {'type': 'string', 'enum': ['auto', 'standard', 'advanced', 'deep']}
                    },
                },
                {
                    'type': 'object',
                    'additionalProperties': False,
                    'required': ['model_id'],
                    'properties': {'model_id': {'type': 'string', 'minLength': 1}},
                },
            ],
        },
    )

    tier: PlanningTier | None = None
    model_id: str | None = Field(default=None, min_length=1)

    @model_validator(mode='before')
    @classmethod
    def _coerce_string_choice(cls, value: object) -> object:
        if isinstance(value, str):
            stripped = value.strip()
            if not stripped:
                message = 'planning choice strings must not be empty'
                raise ValueError(message)
            if stripped in _PLANNING_TIERS:
                return {'tier': stripped}
            return {'model_id': stripped}
        return value

    @model_validator(mode='after')
    def _validate_exactly_one_choice(self) -> PlanningChoice:
        if (self.tier is None) == (self.model_id is None):
            message = 'exactly one of tier or model_id must be set'
            raise ValueError(message)
        return self


class ModelConfig(BaseModel):
    """Scoped model choices for configurable framework parts."""

    model_config: ClassVar[ConfigDict] = ConfigDict(extra='forbid', populate_by_name=True)

    response: ModelChoice | None = None
    thinking: ModelChoice | None = None
    compose_argument: ModelChoice | None = None
    repl: ModelChoice | None = None
    planning: PlanningChoice | None = None

    @field_validator('response', 'thinking', 'compose_argument', 'repl', mode='before')
    @classmethod
    def _coerce_choice(cls, value: object) -> object:
        if value is None or isinstance(value, ModelChoice):
            return value
        return ModelChoice.model_validate(value)

    @field_validator('planning', mode='before')
    @classmethod
    def _coerce_planning_choice(cls, value: object) -> object:
        if value is None or isinstance(value, PlanningChoice):
            return value
        return PlanningChoice.model_validate(value)

    @classmethod
    def for_all(
        cls,
        choice: ModelChoice | str | None = None,
        *,
        tier: ModelTier | None = None,
        model_id: str | None = None,
        include_planning: bool = False,
    ) -> ModelConfig:
        """Apply one response choice to all response/system scopes.

        Planning has a distinct tier vocabulary, so it stays unset unless an exact
        model id is deliberately applied with ``include_planning=True``.
        """
        explicit_count = int(choice is not None) + int(tier is not None) + int(model_id is not None)
        if explicit_count != 1:
            message = 'provide exactly one of choice, tier, or model_id'
            raise ValueError(message)
        raw_choice: ModelChoice | str | dict[str, str]
        if choice is not None:
            raw_choice = choice
        elif tier is not None:
            raw_choice = {'tier': tier}
        else:
            raw_choice = {'model_id': cast('str', model_id)}
        resolved = ModelChoice.model_validate(raw_choice)
        if include_planning and resolved.model_id is None:
            message = 'include_planning requires an exact model_id choice'
            raise ValueError(message)
        return cls(
            response=resolved.model_copy(),
            thinking=resolved.model_copy(),
            compose_argument=resolved.model_copy(),
            repl=resolved.model_copy(),
            planning=(
                PlanningChoice(model_id=resolved.model_id)
                if include_planning and resolved.model_id is not None
                else None
            ),
        )

    def selection_for(self, part: ModelConfigPart) -> ModelChoice | PlanningChoice | None:
        """Return the configured choice for a framework part."""
        return getattr(self, part)

    def model_ids(self) -> list[str]:
        """Return exact model ids referenced by this config."""
        ids: list[str] = []
        for part in ('response', 'thinking', 'compose_argument', 'repl', 'planning'):
            choice = self.selection_for(part)
            if choice is not None and choice.model_id is not None:
                ids.append(choice.model_id)
        return ids


class MemoryRetrievalConfig(BaseModel):
    """Public retrieval controls for memory bootstrap and recall."""

    model_config: ClassVar[ConfigDict] = ConfigDict(extra='forbid')

    top_k: int | None = Field(default=None, ge=1)
    candidate_limit: int | None = Field(default=None, ge=1)
    skills_enabled: bool | None = None
    insights_enabled: bool | None = None
    resources_enabled: bool | None = None
    graph_enabled: bool | None = None
    graph_injection_max_count: int | None = Field(default=None, ge=1, le=16)
    skill_injection_max_count: int | None = Field(default=None, ge=1)
    insight_injection_max_count: int | None = Field(default=None, ge=1)
    resource_injection_max_count: int | None = Field(default=None, ge=1)
    insight_relevance_floor: float | None = Field(default=None, ge=0.0, le=1.0)


class MemorySkillExtractionConfig(BaseModel):
    """Public skill-extraction controls."""

    model_config: ClassVar[ConfigDict] = ConfigDict(extra='forbid')

    enabled: bool | None = None
    sharing_scope: MemorySharingScope | None = None
    confidence_threshold: float | None = Field(default=None, ge=0.0, le=1.0)
    max_count: int | None = Field(default=None, ge=1)


class MemoryInsightExtractionConfig(BaseModel):
    """Public insight-extraction controls."""

    model_config: ClassVar[ConfigDict] = ConfigDict(extra='forbid')

    enabled: bool | None = None
    sharing_scope: Literal['agent', 'swarm'] | None = None
    max_count: int | None = Field(default=None, ge=1)
    min_relevance_score: float | None = Field(default=None, ge=0.0, le=1.0)


class MemoryGraphExtractionConfig(BaseModel):
    """Optional factual graph extraction controls within the persistence ceiling."""

    model_config: ClassVar[ConfigDict] = ConfigDict(extra='forbid')

    enabled: bool | None = None
    max_count: int | None = Field(default=None, ge=1, le=16)


class MemoryConfig(BaseModel):
    """Public memory configuration for SDK invocations and scope defaults."""

    model_config: ClassVar[ConfigDict] = ConfigDict(extra='forbid')

    enabled: bool | None = None
    level: MemoryLevel | None = None
    summarization_enabled: bool | None = None
    persistence_mode: MemoryPersistenceMode | None = None
    retrieval: MemoryRetrievalConfig | None = None
    skill_extraction: MemorySkillExtractionConfig | None = None
    insight_extraction: MemoryInsightExtractionConfig | None = None
    graph_extraction: MemoryGraphExtractionConfig | None = None

    def is_configured(self) -> bool:
        """Return True when at least one option is set."""
        return bool(self.model_dump(exclude_none=True))

    def merged_with(self, override: MemoryConfig | None) -> MemoryConfig:
        """Return this config merged with ``override``."""
        if override is None or not override.is_configured():
            return self.model_copy(deep=True)
        payload = self.model_dump(exclude_none=True)
        payload.update(override.model_dump(exclude_none=True))
        return type(self).model_validate(payload)

    @classmethod
    def merge(cls, *configs: MemoryConfig | None) -> MemoryConfig | None:
        """Merge any configured values from left to right."""
        merged: MemoryConfig | None = None
        for config in configs:
            if config is None or not config.is_configured():
                continue
            merged = config if merged is None else merged.merged_with(config)
        return merged


class _MetadataPayloadConfig(BaseModel):
    model_config: ClassVar[ConfigDict] = ConfigDict(extra='ignore')

    def to_metadata_payload(self) -> JsonObject:
        return cast('JsonObject', self.model_dump(mode='json', exclude_none=True))


class MemorySkillConfig(_MetadataPayloadConfig):
    """Typed user-defined memory skill payload."""

    skill_id: str | None = None
    id: str | None = None
    name: str | None = None
    title: str | None = None
    description: str | None = None
    content: str | None = None
    steps: list[JsonObject] = Field(default_factory=list)
    preconditions: JsonObject = Field(default_factory=dict)
    postconditions: JsonObject = Field(default_factory=dict)
    sharing_scope: MemorySharingScope | None = None
    origin: str | None = None
    confidence: float | None = Field(default=None, ge=0.0, le=1.0)
    metadata: JsonObject = Field(default_factory=dict)
    agent_id: str | None = None
    swarm_id: str | None = None


class MemoryResourceConfig(_MetadataPayloadConfig):
    """Typed bound memory resource payload."""

    resource_id: str | None = None
    id: str | None = None
    title: str | None = None
    name: str | None = None
    description: str | None = None
    content: str | None = None
    source_url: str | None = None
    url: str | None = None
    content_base64: str | None = None
    mime_type: str | None = None
    tags: list[str] = Field(default_factory=list)
    binding_type: str | None = None
    sharing_scope: MemorySharingScope | None = None
    source_type: str | None = None
    agent_id: str | None = None
    swarm_id: str | None = None


class MemoryAssetsConfig(BaseModel):
    """Typed memory assets transported with a session request."""

    model_config: ClassVar[ConfigDict] = ConfigDict(extra='forbid')

    defined_skills: list[MemorySkillConfig] = Field(default_factory=list)
    bound_resources: list[MemoryResourceConfig] = Field(default_factory=list)
    recall_turn_active: bool | None = None

    def is_configured(self) -> bool:
        """Return True when any memory asset option is set."""
        return bool(
            self.defined_skills or self.bound_resources or self.recall_turn_active is not None
        )

    def to_metadata_patch(self) -> JsonObject:
        """Return the v2 metadata patch represented by this config."""
        metadata: JsonObject = {}
        if self.defined_skills:
            metadata['memory_defined_skills'] = [
                skill.to_metadata_payload() for skill in self.defined_skills
            ]
        if self.bound_resources:
            metadata['memory_bound_resources'] = [
                resource.to_metadata_payload() for resource in self.bound_resources
            ]
        if self.recall_turn_active is not None:
            metadata['memory_recall_turn_active'] = self.recall_turn_active
        return metadata


class SessionExecutionConfig(BaseModel):
    """Typed execution metadata for a session request."""

    model_config: ClassVar[ConfigDict] = ConfigDict(extra='forbid')

    agent_id: str | None = None
    timeout: int | float | None = Field(default=None, ge=0)
    sdk_delivery_mode: str | None = None
    client_timezone: str | None = None
    sdk_deployment_timezone: str | None = None

    def to_metadata_patch(self) -> JsonObject:
        """Return the v2 metadata patch represented by this config."""
        return cast('JsonObject', self.model_dump(mode='json', exclude_none=True))


class SessionOrchestrationConfig(BaseModel):
    """Typed orchestration loop controls for a session request."""

    model_config: ClassVar[ConfigDict] = ConfigDict(extra='forbid')

    mode: OrchestrationMode | None = None
    final_output_mode: FinalOutputMode | None = None
    allow_followup_actions: bool | None = None
    stop_strategy: StopStrategy | None = None
    allow_reevaluate_loop: bool | None = None
    max_cycles: int | None = Field(default=None, gt=0)

    def to_metadata_patch(self) -> JsonObject:
        """Return the v2 metadata patch represented by this config."""
        metadata: JsonObject = {}
        if self.mode is not None:
            metadata['orchestration_mode'] = self.mode
        if self.final_output_mode is not None:
            metadata['final_output_mode'] = self.final_output_mode
        if self.allow_followup_actions is not None:
            metadata['allow_followup_actions'] = self.allow_followup_actions
        if self.stop_strategy is not None:
            metadata['stop_strategy'] = self.stop_strategy
        if self.allow_reevaluate_loop is not None:
            metadata['allow_reevaluate_loop'] = self.allow_reevaluate_loop
        if self.max_cycles is not None:
            metadata['max_orchestration_cycles'] = self.max_cycles
        return metadata


class StructuredOutputConfig(BaseModel):
    """Typed structured-output transport intent."""

    model_config: ClassVar[ConfigDict] = ConfigDict(extra='forbid')

    enabled: bool | None = None
    model: str | None = None

    def is_configured(self) -> bool:
        """Return True when structured output is configured."""
        return self.enabled is not None or self.model is not None

    def to_metadata_patch(self) -> JsonObject:
        """Return the v2 metadata patch represented by this config."""
        return cast('JsonObject', self.model_dump(mode='json', exclude_none=True))


class SwarmAgentConfig(_MetadataPayloadConfig):
    """Typed swarm roster entry for one member agent."""

    agent_id: str | None = None
    name: str | None = None
    description: str | None = None
    use_as_final_output: bool = False
    included_nested_synthesis: Literal['auto', True, False] | None = None
    included_nested_synthesis_guidance: str | None = None
    has_final_tool: bool = False
    invocation_tool_id: str | None = None
    invokes_via_dependency: list[str] = Field(default_factory=list)
    memory_config: MemoryConfig | None = None
    memory_defined_skills: list[MemorySkillConfig] = Field(default_factory=list)
    memory_bound_resources: list[MemoryResourceConfig] = Field(default_factory=list)


class SwarmConfig(BaseModel):
    """Typed swarm orchestration transport config."""

    model_config: ClassVar[ConfigDict] = ConfigDict(extra='forbid')

    invocation_intent: bool | None = None
    swarm_id: str | None = None
    swarm_name: str | None = None
    swarm_description: str | None = None
    swarm_system_prompt: str | None = None
    agent_roster: list[SwarmAgentConfig] = Field(default_factory=list)
    agent_invocation_tool_map: dict[str, str] = Field(default_factory=dict)
    agent_invocation: bool | None = None
    use_as_final_output: bool | None = None
    invoked_agent_id: str | None = None
    invoked_agent_name: str | None = None
    included_nested_synthesis: Literal['auto', True, False] | None = None
    sdk_delivery_mode: str | None = None
    agent_dependency_context: JsonObject | None = None
    agent_dependency_context_keys: list[str] | None = None
    swarm_has_final_tool: bool = False

    def is_configured(self) -> bool:
        """Return True when at least one swarm option is set."""
        return bool(self.model_dump(exclude_none=True, exclude_defaults=True))

    def to_metadata_patch(self) -> JsonObject:
        """Return the v2 metadata patch represented by this config."""
        return cast('JsonObject', self.model_dump(mode='json', exclude_none=True))


class SystemToolsConfig(BaseModel):
    """Typed controls for server-side system tool availability and approvals."""

    model_config: ClassVar[ConfigDict] = ConfigDict(extra='forbid')

    allowed_tools: list[str] | None = None
    approved_compose_argument_targets: list[str] | None = None
    allow_private_data: bool | None = None
    allow_private_data_placeholders: bool | None = None

    def to_metadata_patch(self) -> JsonObject:
        """Return the v2 metadata patch represented by this config."""
        metadata: JsonObject = {}
        if self.allowed_tools is not None:
            metadata['allowed_system_tools'] = self.allowed_tools
        if self.approved_compose_argument_targets is not None:
            metadata['approved_compose_argument_targets'] = self.approved_compose_argument_targets
        if self.allow_private_data is not None:
            metadata['allow_private_data_in_system_tools'] = self.allow_private_data
        if self.allow_private_data_placeholders is not None:
            metadata['allow_private_data_placeholders_in_system_tools'] = (
                self.allow_private_data_placeholders
            )
        return metadata


class AuthMode(str, Enum):
    """Supported authentication flows for a toolset or provider."""

    NONE = 'none'
    API_KEY = 'api_key'
    BEARER = 'bearer'
    BASIC = 'basic'
    OAUTH2_AUTH_CODE = 'oauth2_auth_code'
    OAUTH2_PKCE = 'oauth2_pkce'
    OAUTH2_CLIENT_CREDENTIALS = 'oauth2_client_credentials'
    OAUTH2_DEVICE_CODE = 'oauth2_device_code'
    SERVICE_ACCOUNT = 'service_account'
    CUSTOM = 'custom'


class ProviderCapability(str, Enum):
    """Standard capability flags a toolset may advertise."""

    READ = 'read'
    WRITE = 'write'
    SEARCH = 'search'
    EXPORT = 'export'
    IMPORT = 'import'
    WEBHOOKS = 'webhooks'
    STREAMING = 'streaming'
    BULK = 'bulk'
    DRY_RUN = 'dry_run'
    PAGINATION = 'pagination'
    RATE_LIMITED = 'rate_limited'


@dataclass(frozen=True)
class ProviderMetadata:
    """Static metadata describing a toolset or provider."""

    name: str
    display_name: str
    version: str
    description: str = ''
    auth_modes: tuple[AuthMode, ...] = ()
    scopes: dict[str, str] = field(default_factory=dict)
    capabilities: frozenset[ProviderCapability] = field(default_factory=frozenset)
    documentation_url: str | None = None
    homepage_url: str | None = None
    tags: tuple[str, ...] = ()
    extras: JsonObject = field(default_factory=dict)

    def __post_init__(self) -> None:
        """Validate required catalog identity fields."""
        if not self.name or not self.display_name or not self.version:
            message = 'ProviderMetadata requires name, display_name, and version'
            raise ValueError(message)

    def supports_auth(self, mode: AuthMode) -> bool:
        """Return True when this provider supports ``mode``."""
        return mode in self.auth_modes

    def has_capability(self, capability: ProviderCapability) -> bool:
        """Return True when this provider advertises ``capability``."""
        return capability in self.capabilities

    def to_dict(self) -> JsonObject:
        """Return a JSON-compatible metadata representation."""
        return {
            'name': self.name,
            'display_name': self.display_name,
            'version': self.version,
            'description': self.description,
            'auth_modes': [mode.value for mode in self.auth_modes],
            'scopes': dict(self.scopes),
            'capabilities': sorted(capability.value for capability in self.capabilities),
            'documentation_url': self.documentation_url,
            'homepage_url': self.homepage_url,
            'tags': list(self.tags),
            'extras': dict(self.extras),
        }


__all__ = [
    'AuthMode',
    'FinalOutputMode',
    'JsonObject',
    'MemoryAssetsConfig',
    'MemoryConfig',
    'MemoryGraphExtractionConfig',
    'MemoryInsightExtractionConfig',
    'MemoryLevel',
    'MemoryPersistenceMode',
    'MemoryResourceConfig',
    'MemoryRetrievalConfig',
    'MemorySharingScope',
    'MemorySkillConfig',
    'MemorySkillExtractionConfig',
    'ModelChoice',
    'ModelConfig',
    'ModelConfigPart',
    'ModelTier',
    'OrchestrationMode',
    'ProviderCapability',
    'ProviderMetadata',
    'SessionExecutionConfig',
    'SessionOrchestrationConfig',
    'StopStrategy',
    'StructuredOutputConfig',
    'SwarmAgentConfig',
    'SwarmConfig',
    'SystemToolsConfig',
]
