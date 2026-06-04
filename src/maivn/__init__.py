# pyright: strict
"""Top-level SDK exports for Maivn.
Re-exports clients, agents, decorators, logging, and configuration.
Configuration mutation helpers are intentionally not exported to avoid external rewiring.
"""

from __future__ import annotations

# MARK: - Shared Models
from maivn_shared import (
    HIPAA_SAFE_HARBOR_CATEGORIES,
    PERMISSION_FLAG_NAMES,
    AuthMode,
    FinalOutputMode,
    MemoryAssetsConfig,
    MemoryConfig,
    MemoryInsightExtractionConfig,
    MemoryLevel,
    MemoryPersistenceMode,
    MemoryResourceConfig,
    MemoryRetrievalConfig,
    MemorySharingScope,
    MemorySkillConfig,
    MemorySkillExtractionConfig,
    OrchestrationMode,
    PermissionFlag,
    PermissionSet,
    PIIWhitelist,
    PIIWhitelistEntry,
    PrivateData,
    ProviderCapability,
    ProviderMetadata,
    RedactedMessage,
    RedactionPreviewRequest,
    RedactionPreviewResponse,
    SessionExecutionConfig,
    SessionOrchestrationConfig,
    StopStrategy,
    StructuredOutputConfig,
    SwarmAgentConfig,
    SwarmConfig,
    SystemToolsConfig,
    require_permissions,
)

# MARK: - Version
from .__version__ import __version__

# MARK: - Core Classes
from ._internal.api import (
    Agent,
    BaseScope,
    Client,
    ClientBuilder,
    MCPAutoSetup,
    MCPServer,
    MCPSoftErrorHandling,
    Swarm,
    ToolOverride,
)
from ._internal.api.billing_models import (
    BillingCurrentUsage,
    BillingPlan,
    BillingUsage,
    BillingUsageApiKey,
    BillingUsageBreakdown,
    BillingUsageProject,
    BillingUsageTotals,
)
from ._internal.api.resource_models import (
    MemoryInsight,
    MemoryInsightOrigin,
    MemoryInsightType,
    MemoryPersistenceCeiling,
    MemoryResource,
    MemoryResourceBindingType,
    MemoryResourceDetail,
    MemoryResourceStatus,
    MemorySkill,
    MemorySkillOrigin,
    MemorySkillStatus,
    MemoryUnboundResourceCandidate,
    OrganizationMemoryPolicy,
    OrganizationMemoryPurgeResult,
    ProjectMemoryResources,
)

# MARK: - Scheduling
from ._internal.api.scheduling import (
    AtSchedule,
    CronInvocationBuilder,
    CronSchedule,
    IntervalSchedule,
    JitterDistribution,
    JitterSpec,
    MisfirePolicy,
    OverlapPolicy,
    Retry,
    RetryBackoff,
    RunRecord,
    RunStatus,
    Schedule,
    ScheduledJob,
    list_jobs,
    stop_all_jobs,
)

# MARK: - Interrupts
from ._internal.core.services.interrupt_service import (
    default_terminal_interrupt,
    get_interrupt_service,
    set_interrupt_service,
)

# MARK: - Decorators
from ._internal.utils import (
    compose_artifact_policy,
    depends_on_agent,
    depends_on_await_for,
    depends_on_interrupt,
    depends_on_private_data,
    depends_on_reevaluate,
    depends_on_tool,
    toolify,
    toolset,
)

# MARK: - Configuration
from ._internal.utils.configuration import (
    ConfigurationBuilder,
    MaivnConfiguration,
    get_configuration,
)

# MARK: - Logging
# Import maivn.logging directly when logging must be configured before root SDK imports.
from ._internal.utils.logging import (
    configure_logging,
    get_logger,
)

# MARK: - Events
from .events import (
    APP_EVENT_CONTRACT_VERSION,
    AppEvent,
    BackpressurePolicy,
    BridgeAudience,
    BridgeRegistry,
    EventBridge,
    EventBridgeSecurityPolicy,
    NormalizedEventForwardingState,
    NormalizedStreamState,
    RawSSEEvent,
    UIEvent,
    build_agent_assignment_payload,
    build_assistant_chunk_payload,
    build_enrichment_payload,
    build_error_payload,
    build_final_payload,
    build_interrupt_required_payload,
    build_session_start_payload,
    build_status_message_payload,
    build_system_tool_chunk_payload,
    build_system_tool_complete_payload,
    build_system_tool_start_payload,
    build_tool_event_payload,
    forward_normalized_event,
    forward_normalized_stream,
    normalize_stream,
    normalize_stream_event,
)

# MARK: - Public API

__all__ = [
    # Version
    "__version__",
    # Logging
    "configure_logging",
    "get_logger",
    # Event Contract
    "APP_EVENT_CONTRACT_VERSION",
    "AppEvent",
    "BackpressurePolicy",
    "BridgeAudience",
    "BridgeRegistry",
    "EventBridge",
    "EventBridgeSecurityPolicy",
    "NormalizedEventForwardingState",
    "NormalizedStreamState",
    "RawSSEEvent",
    "UIEvent",
    "build_agent_assignment_payload",
    "build_assistant_chunk_payload",
    "build_enrichment_payload",
    "build_error_payload",
    "build_final_payload",
    "build_interrupt_required_payload",
    "build_session_start_payload",
    "build_status_message_payload",
    "build_system_tool_chunk_payload",
    "build_system_tool_complete_payload",
    "build_system_tool_start_payload",
    "build_tool_event_payload",
    "forward_normalized_event",
    "forward_normalized_stream",
    "normalize_stream",
    "normalize_stream_event",
    # Decorators
    "compose_artifact_policy",
    "depends_on_agent",
    "depends_on_await_for",
    "depends_on_private_data",
    "depends_on_interrupt",
    "depends_on_reevaluate",
    "depends_on_tool",
    "toolify",
    "toolset",
    # Core Classes
    "Agent",
    "BaseScope",
    "Client",
    "ClientBuilder",
    "MCPAutoSetup",
    "MCPServer",
    "MCPSoftErrorHandling",
    "Swarm",
    "ToolOverride",
    "OrganizationMemoryPolicy",
    "OrganizationMemoryPurgeResult",
    "MemoryPersistenceCeiling",
    "MemoryResource",
    "MemoryResourceDetail",
    "MemoryResourceBindingType",
    "MemoryResourceStatus",
    "MemorySkill",
    "MemorySkillOrigin",
    "MemorySkillStatus",
    "MemoryInsight",
    "MemoryInsightType",
    "MemoryInsightOrigin",
    "MemoryUnboundResourceCandidate",
    "ProjectMemoryResources",
    # Billing / usage
    "BillingUsage",
    "BillingPlan",
    "BillingCurrentUsage",
    "BillingUsageBreakdown",
    "BillingUsageProject",
    "BillingUsageApiKey",
    "BillingUsageTotals",
    # Shared Models - Permissions
    "PERMISSION_FLAG_NAMES",
    "PermissionFlag",
    "PermissionSet",
    "require_permissions",
    # Shared Models - Provider Metadata
    "AuthMode",
    "ProviderCapability",
    "ProviderMetadata",
    # Shared Models
    "HIPAA_SAFE_HARBOR_CATEGORIES",
    "PIIWhitelist",
    "PIIWhitelistEntry",
    "PrivateData",
    "MemoryConfig",
    "MemoryAssetsConfig",
    "MemoryInsightExtractionConfig",
    "MemoryLevel",
    "MemoryPersistenceMode",
    "MemoryResourceConfig",
    "MemoryRetrievalConfig",
    "MemorySharingScope",
    "MemorySkillConfig",
    "MemorySkillExtractionConfig",
    "FinalOutputMode",
    "OrchestrationMode",
    "SessionExecutionConfig",
    "SessionOrchestrationConfig",
    "StopStrategy",
    "StructuredOutputConfig",
    "SwarmAgentConfig",
    "SwarmConfig",
    "SystemToolsConfig",
    "RedactedMessage",
    "RedactionPreviewRequest",
    "RedactionPreviewResponse",
    # Configuration
    "ConfigurationBuilder",
    "MaivnConfiguration",
    "get_configuration",
    # Interrupts
    "default_terminal_interrupt",
    "get_interrupt_service",
    "set_interrupt_service",
    # Scheduling
    "AtSchedule",
    "CronInvocationBuilder",
    "CronSchedule",
    "IntervalSchedule",
    "JitterDistribution",
    "JitterSpec",
    "MisfirePolicy",
    "OverlapPolicy",
    "Retry",
    "RetryBackoff",
    "RunRecord",
    "RunStatus",
    "Schedule",
    "ScheduledJob",
    "list_jobs",
    "stop_all_jobs",
]
