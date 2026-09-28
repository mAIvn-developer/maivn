"""Contract tests for the SDK public import surface."""

from __future__ import annotations

import inspect
from typing import TYPE_CHECKING

import maivn
from maivn import events as maivn_events
from maivn import logging as maivn_logging
from maivn import messages as maivn_messages
from maivn import telemetry as maivn_telemetry

if TYPE_CHECKING:
    from collections.abc import Iterable


V1_CATEGORY_C_ROOT_EXCLUSIONS = frozenset(
    {
        'APP_EVENT_CONTRACT_VERSION',
        'AppEvent',
        'BridgeAudience',
        'BridgeRegistry',
        'EventBridge',
        'EventBridgeSecurityPolicy',
        'NormalizedEventForwardingState',
        'NormalizedStreamState',
        'RawSSEEvent',
        'UIEvent',
        'build_agent_assignment_payload',
        'build_assistant_chunk_payload',
        'build_enrichment_payload',
        'build_error_payload',
        'build_final_payload',
        'build_interrupt_required_payload',
        'build_session_start_payload',
        'build_status_message_chunk_payload',
        'build_status_message_payload',
        'build_system_tool_chunk_payload',
        'build_system_tool_complete_payload',
        'build_system_tool_start_payload',
        'build_tool_event_payload',
        'forward_normalized_event',
        'forward_normalized_stream',
        'get_interrupt_service',
        'normalize_stream',
        'normalize_stream_event',
        'set_interrupt_service',
    },
)

V1_PUBLIC_EXPORTS = (
    '__version__',
    'configure_logging',
    'get_logger',
    'APP_EVENT_CONTRACT_VERSION',
    'AppEvent',
    'BackpressurePolicy',
    'BridgeAudience',
    'BridgeRegistry',
    'EventBridge',
    'EventBridgeSecurityPolicy',
    'NormalizedEventForwardingState',
    'NormalizedStreamState',
    'RawSSEEvent',
    'UIEvent',
    'build_agent_assignment_payload',
    'build_assistant_chunk_payload',
    'build_enrichment_payload',
    'build_error_payload',
    'build_final_payload',
    'build_interrupt_required_payload',
    'build_session_start_payload',
    'build_status_message_chunk_payload',
    'build_status_message_payload',
    'build_system_tool_chunk_payload',
    'build_system_tool_complete_payload',
    'build_system_tool_start_payload',
    'build_tool_event_payload',
    'forward_normalized_event',
    'forward_normalized_stream',
    'normalize_stream',
    'normalize_stream_event',
    'depends_on_agent',
    'depends_on_await_for',
    'depends_on_private_data',
    'depends_on_interrupt',
    'depends_on_reevaluate',
    'depends_on_tool',
    'tool_output',
    'toolify',
    'toolset',
    'Agent',
    'BaseScope',
    'Client',
    'ClientBuilder',
    'MCPAutoSetup',
    'MCPServer',
    'MCPSoftErrorHandling',
    'Swarm',
    'ToolOverride',
    'OrganizationMemoryPolicy',
    'OrganizationMemoryPurgeResult',
    'MemoryPersistenceCeiling',
    'MemoryResource',
    'MemoryResourceDetail',
    'MemoryResourceBindingType',
    'MemoryResourceStatus',
    'MemorySkill',
    'MemorySkillOrigin',
    'MemorySkillStatus',
    'MemoryInsight',
    'MemoryInsightType',
    'MemoryInsightOrigin',
    'MemoryUnboundResourceCandidate',
    'ProjectMemoryResources',
    'BillingUsage',
    'BillingPlan',
    'BillingCurrentUsage',
    'BillingUsageBreakdown',
    'BillingUsageProject',
    'BillingUsageApiKey',
    'BillingUsageTotals',
    'PERMISSION_FLAG_NAMES',
    'PermissionFlag',
    'PermissionSet',
    'require_permissions',
    'AuthMode',
    'ProviderCapability',
    'ProviderMetadata',
    'HIPAA_SAFE_HARBOR_CATEGORIES',
    'PIIWhitelist',
    'PIIWhitelistEntry',
    'PrivateData',
    'MemoryConfig',
    'MemoryAssetsConfig',
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
    'PlanningChoice',
    'PlanningTier',
    'FinalOutputMode',
    'OrchestrationMode',
    'SessionExecutionConfig',
    'SessionOrchestrationConfig',
    'StopStrategy',
    'StructuredOutputConfig',
    'SwarmAgentConfig',
    'SwarmConfig',
    'SystemToolsConfig',
    'RedactedMessage',
    'RedactionPreviewRequest',
    'RedactionPreviewResponse',
    'ConfigurationBuilder',
    'MaivnConfiguration',
    'get_configuration',
    'default_terminal_interrupt',
    'get_interrupt_service',
    'set_interrupt_service',
    'AtSchedule',
    'CronInvocationBuilder',
    'CronSchedule',
    'IntervalSchedule',
    'JitterDistribution',
    'JitterSpec',
    'MisfirePolicy',
    'OverlapPolicy',
    'Retry',
    'RetryBackoff',
    'RunRecord',
    'RunStatus',
    'Schedule',
    'ScheduledJob',
    'list_jobs',
    'stop_all_jobs',
)

TARGET_ROOT_EXPORTS = tuple(
    name for name in V1_PUBLIC_EXPORTS if name not in V1_CATEGORY_C_ROOT_EXCLUSIONS
)


def test_agent_generated_prompt_is_exported_from_the_root() -> None:
    """The opt-in prompt sentinel is visible on the documented SDK surface."""
    assert isinstance(maivn.AgentGenerated, str)
    assert 'AgentGenerated' in maivn.__all__


def test_returnable_artifact_surface_is_exported_from_the_root() -> None:
    """The SDK exposes canonical refs plus typed ordinary retrieval values."""
    expected = {
        'ArtifactDownload',
        'ArtifactListPage',
        'ArtifactMetadata',
        'ArtifactPreview',
        'ArtifactRef',
        'OrdinaryArtifactRef',
        'PrivateArtifactRef',
    }

    assert expected.issubset(maivn.__all__)
    assert all(hasattr(maivn, name) for name in expected)


def test_inline_followup_surface_is_exported_and_has_no_enabled_switch() -> None:
    """The per-call builder and typed terminal handler stay visible and explicit."""
    assert {
        'FollowupInputHandler',
        'FollowupOption',
        'FollowupQuestion',
        'FollowupQuestionsConfig',
        'default_terminal_followup',
    }.issubset(maivn.__all__)
    assert _parameter_names(maivn.Agent.allow_followup_questions) == (
        'self',
        'input_handler',
        'max_questions',
        'max_questions_per_thread',
        'response_schema',
        'timeout',
    )


def test_public_exports_are_v1_compatible_minus_internal_event_plumbing() -> None:
    """Every v1 public root name except locked Category C plumbing imports."""
    missing = [name for name in TARGET_ROOT_EXPORTS if not hasattr(maivn, name)]

    assert missing == []
    # maivn.__all__ is the UNION of the v2-native surface and the restored v1
    # API (minus Category C). Assert v1 compatibility as a subset of __all__
    # rather than exact equality, so preserving the v2-native names is allowed.
    not_exported = [name for name in TARGET_ROOT_EXPORTS if name not in maivn.__all__]
    assert not_exported == []


def test_internal_event_plumbing_is_not_reexported_at_root() -> None:
    """Category C names intentionally stay off the developer-facing root API."""
    leaked = [name for name in V1_CATEGORY_C_ROOT_EXCLUSIONS if name in maivn.__all__]

    assert leaked == []


def test_v1_adapter_signatures_keep_keyword_surface() -> None:
    """Key v1 decorators and builders keep their old argument names."""
    expected = {
        'depends_on_agent': ('agent_ref', 'arg_name'),
        'depends_on_await_for': ('tool_ref', 'timing', 'instance_control'),
        'depends_on_interrupt': ('arg_name', 'input_handler', 'prompt', 'input_type', 'choices'),
        'depends_on_private_data': ('data_key', 'arg_name'),
        'depends_on_reevaluate': ('tool_ref', 'timing', 'instance_control'),
        'depends_on_tool': ('tool_ref', 'arg_name', 'revision_policy', 'result_scope'),
        'tool_output': ('schema',),
        'toolify': (
            'func',
            'name',
            'description',
            'permissions',
            'destructive',
            'always_execute',
            'final_tool',
            'metadata',
            'tags',
            'before_execute',
            'after_execute',
        ),
        'toolset': ('cls', 'prefix', 'tags', 'require_marker', 'metadata'),
    }

    for name, parameters in expected.items():
        assert _parameter_names(getattr(maivn, name)) == parameters

    result_scope = inspect.signature(maivn.depends_on_tool).parameters['result_scope']
    assert result_scope.kind == inspect.Parameter.KEYWORD_ONLY
    assert result_scope.default == 'invocation'


def test_compose_argument_policy_replaces_the_retired_artifact_spelling() -> None:
    """Argument synthesis has its own public name without reviving artifact authoring."""
    assert 'compose_artifact_policy' not in maivn.__all__
    assert not hasattr(maivn, 'compose_artifact_policy')
    assert 'compose_argument_policy' in maivn.__all__
    assert hasattr(maivn, 'compose_argument_policy')
    assert hasattr(maivn, 'requires_producer_reference')
    assert not hasattr(maivn.SystemToolsConfig, 'approved_compose_artifact_targets')
    assert 'approved_compose_argument_targets' in maivn.SystemToolsConfig.model_fields


def test_messages_exports_preserve_v1_import_style() -> None:
    """Developers can keep importing v1-style message classes from maivn.messages."""
    for name in ('BaseMessage', 'HumanMessage', 'PrivateData', 'RedactedMessage', 'SystemMessage'):
        assert hasattr(maivn_messages, name)

    user = maivn_messages.HumanMessage(content='hello')
    system = maivn_messages.SystemMessage(content='be brief')
    assistant = maivn_messages.AIMessage(content='hi')
    tool = maivn_messages.ToolMessage(content='{}', metadata={'tool_call_id': 'call-1'})
    redacted = maivn_messages.RedactedMessage(
        content='Process claim for Maria Santos.',
        known_pii_values=[maivn_messages.PrivateData(value='Maria Santos')],
    )

    assert user.to_contract().role == 'user'
    assert system.to_contract().role == 'system'
    assert assistant.to_contract().role == 'assistant'
    assert tool.to_contract().role == 'tool'
    assert redacted.to_contract().role == 'user'


def test_events_submodule_points_to_v2_contracts() -> None:
    """The public events submodule is canonical v2 contracts, not v1 bridge plumbing."""
    assert maivn_events.Event.__module__.startswith('maivn_contracts.events')
    assert maivn_events.StreamEvent.__module__ == 'maivn._internal.models'


def test_logging_submodule_exports_are_available() -> None:
    """The v1 logging import path remains available."""
    assert maivn_logging.__all__ == ['configure_logging', 'get_logger']
    assert maivn_logging.configure_logging is maivn.configure_logging
    assert maivn_logging.get_logger is maivn.get_logger


def test_telemetry_submodule_exports_the_versioned_contract_only() -> None:
    """Telemetry is a dedicated stable surface, not a root-module expansion."""
    expected = [
        'TELEMETRY_SCHEMA_VERSION',
        'RunTelemetryEvent',
        'RunTelemetryScope',
        'TelemetryListener',
        'TelemetrySubscription',
        'TokenUsageTelemetry',
        'ToolCallTelemetry',
        'register_listener',
    ]

    assert maivn_telemetry.__all__ == expected
    assert maivn_telemetry.TELEMETRY_SCHEMA_VERSION == '1'
    assert _parameter_names(maivn_telemetry.register_listener) == ('listener',)
    assert all(hasattr(maivn_telemetry, name) for name in expected)
    assert all(name not in maivn.__all__ for name in expected)
    assert not hasattr(maivn_telemetry, 'RunTelemetryContent')


def _parameter_names(value: object) -> tuple[str, ...]:
    if not callable(value):
        message = f'expected a callable, got {type(value).__name__}'
        raise TypeError(message)
    signature = inspect.signature(value)
    return tuple(_visible_parameters(signature.parameters.values()))


def _visible_parameters(parameters: Iterable[inspect.Parameter]) -> tuple[str, ...]:
    visible: list[str] = []
    for parameter in parameters:
        if parameter.kind is inspect.Parameter.VAR_KEYWORD:
            continue
        if parameter.kind is inspect.Parameter.VAR_POSITIONAL:
            continue
        visible.append(parameter.name)
    return tuple(visible)
