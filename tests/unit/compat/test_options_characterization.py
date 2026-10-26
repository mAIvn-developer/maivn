"""Characterization tests for the v1-compatible typed option models.

Pins model/planning choice coercion, ``ModelConfig.for_all``, memory config
merging, and the exact v2 metadata patches each session option produces.
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from maivn import (
    AuthMode,
    MemoryAssetsConfig,
    MemoryConfig,
    MemoryResourceConfig,
    MemoryRetrievalConfig,
    MemorySkillConfig,
    ModelChoice,
    ModelConfig,
    PlanningChoice,
    ProviderCapability,
    ProviderMetadata,
    SessionExecutionConfig,
    SessionOrchestrationConfig,
    StructuredOutputConfig,
    SwarmAgentConfig,
    SwarmConfig,
    SystemToolsConfig,
)

_EXACTLY_ONE = 'Value error, exactly one of tier or model_id must be set'


def _errors(excinfo: pytest.ExceptionInfo[ValidationError]) -> list[tuple[object, str]]:
    return [(tuple(error['loc']), error['msg']) for error in excinfo.value.errors()]


@pytest.mark.parametrize(
    ('raw', 'expected'),
    [
        (' fast ', {'tier': 'fast', 'model_id': None}),
        ('auto', {'tier': 'auto', 'model_id': None}),
        ('ultra', {'tier': 'ultra', 'model_id': None}),
        (' example-model-x ', {'tier': None, 'model_id': 'example-model-x'}),
        ('deep', {'tier': None, 'model_id': 'deep'}),
    ],
)
def test_model_choice_strings_become_tiers_or_stripped_model_ids(
    raw: str,
    expected: dict[str, str | None],
) -> None:
    """Known model tiers become tiers; any other string is an exact model id."""
    assert ModelChoice.model_validate(raw).model_dump() == expected


@pytest.mark.parametrize(
    ('raw', 'expected'),
    [
        (' deep ', {'tier': 'deep', 'model_id': None}),
        ('standard', {'tier': 'standard', 'model_id': None}),
        ('fast', {'tier': None, 'model_id': 'fast'}),
        (' plan-model ', {'tier': None, 'model_id': 'plan-model'}),
    ],
)
def test_planning_choice_uses_its_own_tier_vocabulary(
    raw: str,
    expected: dict[str, str | None],
) -> None:
    """Planning tiers differ from model tiers; other strings are model ids."""
    assert PlanningChoice.model_validate(raw).model_dump() == expected


@pytest.mark.parametrize(
    ('model', 'raw', 'message'),
    [
        (ModelChoice, '  ', 'Value error, model choice strings must not be empty'),
        (PlanningChoice, '', 'Value error, planning choice strings must not be empty'),
    ],
)
def test_empty_choice_strings_are_refused(
    model: type[ModelChoice | PlanningChoice],
    raw: str,
    message: str,
) -> None:
    """Blank choice strings are rejected with a choice-specific message."""
    with pytest.raises(ValidationError) as excinfo:
        _ = model.model_validate(raw)
    assert _errors(excinfo) == [((), message)]


@pytest.mark.parametrize('model', [ModelChoice, PlanningChoice])
@pytest.mark.parametrize(
    'payload',
    [{}, {'tier': 'auto', 'model_id': 'example-model-x'}],
)
def test_choices_require_exactly_one_of_tier_or_model_id(
    model: type[ModelChoice | PlanningChoice],
    payload: dict[str, str],
) -> None:
    """Neither or both of tier/model_id is refused."""
    with pytest.raises(ValidationError) as excinfo:
        _ = model.model_validate(payload)
    assert _errors(excinfo) == [((), _EXACTLY_ONE)]


def test_planning_choice_refuses_an_empty_model_id_and_extras() -> None:
    """An explicit empty planning model id and unknown fields are refused."""
    with pytest.raises(ValidationError) as excinfo:
        _ = PlanningChoice(model_id='')
    assert _errors(excinfo) == [(('model_id',), 'String should have at least 1 character')]
    with pytest.raises(ValidationError) as extra:
        _ = ModelChoice.model_validate({'tier': 'fast', 'provider': 'x'})
    assert _errors(extra) == [(('provider',), 'Extra inputs are not permitted')]


def test_model_config_coerces_each_part_from_strings_and_dicts() -> None:
    """Every scoped part accepts a choice, a string or a dict."""
    config = ModelConfig.model_validate(
        {
            'response': 'fast',
            'thinking': {'model_id': 'think-1'},
            'compose_argument': ModelChoice(tier='max'),
            'repl': None,
            'planning': 'advanced',
        },
    )
    assert config.model_dump() == {
        'response': {'tier': 'fast', 'model_id': None},
        'thinking': {'tier': None, 'model_id': 'think-1'},
        'compose_argument': {'tier': 'max', 'model_id': None},
        'repl': None,
        'planning': {'tier': 'advanced', 'model_id': None},
    }
    assert config.selection_for('thinking') == ModelChoice(model_id='think-1')
    assert config.selection_for('repl') is None
    assert config.selection_for('planning') == PlanningChoice(tier='advanced')
    assert ModelConfig(planning=PlanningChoice(model_id='p')).model_ids() == ['p']


def test_model_ids_lists_exact_ids_in_part_order() -> None:
    """``model_ids`` skips tiers and unset parts and keeps part order."""
    config = ModelConfig.model_validate(
        {
            'response': 'resp-1',
            'thinking': 'fast',
            'compose_argument': 'compose-1',
            'repl': 'repl-1',
            'planning': 'plan-1',
        },
    )
    assert config.model_ids() == ['resp-1', 'compose-1', 'repl-1', 'plan-1']
    assert ModelConfig().model_ids() == []


def test_for_all_by_tier_leaves_planning_unset() -> None:
    """A tier applies to every response scope but never to planning."""
    config = ModelConfig.for_all(tier='max')
    tier_max = {'tier': 'max', 'model_id': None}
    assert config.model_dump() == {
        'response': tier_max,
        'thinking': tier_max,
        'compose_argument': tier_max,
        'repl': tier_max,
        'planning': None,
    }


def test_for_all_by_model_id_can_include_planning() -> None:
    """An exact model id may also be applied to planning on request."""
    without = ModelConfig.for_all(model_id='m-1')
    with_planning = ModelConfig.for_all('m-1', include_planning=True)
    assert without.planning is None
    assert without.model_ids() == ['m-1', 'm-1', 'm-1', 'm-1']
    assert with_planning.model_ids() == ['m-1', 'm-1', 'm-1', 'm-1', 'm-1']
    assert with_planning.planning == PlanningChoice(model_id='m-1')


def test_for_all_copies_the_choice_into_independent_parts() -> None:
    """Each part receives its own copy of a passed choice object."""
    choice = ModelChoice(tier='balanced')
    config = ModelConfig.for_all(choice)
    assert config.response == choice
    assert config.response is not choice
    assert config.response is not config.thinking


@pytest.mark.parametrize(
    'kwargs',
    [
        {},
        {'choice': 'fast', 'tier': 'fast'},
        {'tier': 'fast', 'model_id': 'x'},
        {'choice': 'x', 'model_id': 'x'},
    ],
)
def test_for_all_requires_exactly_one_selector(kwargs: dict[str, str]) -> None:
    """Exactly one of choice, tier or model_id must be provided."""
    with pytest.raises(ValueError, match=r'^') as excinfo:
        _ = ModelConfig.for_all(**kwargs)  # pyright: ignore[reportArgumentType]
    assert str(excinfo.value) == 'provide exactly one of choice, tier, or model_id'


def test_for_all_planning_requires_an_exact_model_id() -> None:
    """A tier cannot be applied to planning."""
    with pytest.raises(ValueError, match=r'^') as excinfo:
        _ = ModelConfig.for_all(tier='fast', include_planning=True)
    assert str(excinfo.value) == 'include_planning requires an exact model_id choice'


def test_memory_config_is_configured_only_with_a_value() -> None:
    """An all-None memory config is not configured."""
    assert MemoryConfig().is_configured() is False
    assert MemoryConfig(enabled=False).is_configured() is True


def test_memory_config_merge_overrides_left_to_right_and_skips_empty() -> None:
    """Later configured values win; empty and None configs are skipped."""
    base = MemoryConfig(enabled=True, level='glimpse')
    override = MemoryConfig(level='clarity', retrieval=MemoryRetrievalConfig(top_k=3))
    merged = MemoryConfig.merge(None, base, MemoryConfig(), override)
    assert merged is not None
    assert merged.model_dump(exclude_none=True) == {
        'enabled': True,
        'level': 'clarity',
        'retrieval': {'top_k': 3},
    }
    assert MemoryConfig.merge(None, MemoryConfig()) is None
    assert MemoryConfig.merge(base) is base


def test_memory_config_merged_with_nothing_returns_a_deep_copy() -> None:
    """Merging with None or an empty config copies the original."""
    base = MemoryConfig(retrieval=MemoryRetrievalConfig(top_k=2))
    for override in (None, MemoryConfig()):
        copy = base.merged_with(override)
        assert copy == base
        assert copy is not base
        assert copy.retrieval is not base.retrieval


def test_memory_retrieval_bounds_are_enforced() -> None:
    """Retrieval counts start at 1 and graph injection is capped at 16."""
    with pytest.raises(ValidationError) as excinfo:
        _ = MemoryRetrievalConfig(top_k=0, graph_injection_max_count=17)
    assert _errors(excinfo) == [
        (('top_k',), 'Input should be greater than or equal to 1'),
        (('graph_injection_max_count',), 'Input should be less than or equal to 16'),
    ]


def test_memory_assets_patch_uses_v2_metadata_keys() -> None:
    """Skills, resources and the recall flag map to their metadata keys."""
    assets = MemoryAssetsConfig(
        defined_skills=[MemorySkillConfig(name='triage', confidence=0.5)],
        bound_resources=[MemoryResourceConfig(title='Runbook', tags=['ops'])],
        recall_turn_active=False,
    )
    assert assets.is_configured() is True
    assert assets.to_metadata_patch() == {
        'memory_defined_skills': [
            {
                'name': 'triage',
                'steps': [],
                'preconditions': {},
                'postconditions': {},
                'confidence': 0.5,
                'metadata': {},
            },
        ],
        'memory_bound_resources': [{'title': 'Runbook', 'tags': ['ops']}],
        'memory_recall_turn_active': False,
    }


def test_empty_memory_assets_are_unconfigured_with_an_empty_patch() -> None:
    """No assets means no metadata at all."""
    assets = MemoryAssetsConfig()
    assert assets.is_configured() is False
    assert assets.to_metadata_patch() == {}
    assert MemoryAssetsConfig(recall_turn_active=True).to_metadata_patch() == {
        'memory_recall_turn_active': True,
    }


def test_memory_payload_configs_ignore_unknown_fields() -> None:
    """Skill and resource payloads drop keys they do not know."""
    skill = MemorySkillConfig.model_validate({'name': 's', 'unknown': 1})
    assert 'unknown' not in skill.to_metadata_payload()


def test_session_execution_patch_drops_unset_fields() -> None:
    """Only set execution fields reach the metadata patch."""
    assert SessionExecutionConfig(agent_id='a', timeout=1.5).to_metadata_patch() == {
        'agent_id': 'a',
        'timeout': 1.5,
    }
    assert SessionExecutionConfig().to_metadata_patch() == {}
    with pytest.raises(ValidationError) as excinfo:
        _ = SessionExecutionConfig(timeout=-1)
    assert _errors(excinfo) == [(('timeout',), 'Input should be greater than or equal to 0')]


def test_session_orchestration_patch_renames_keys() -> None:
    """Orchestration fields map to their v2 metadata names."""
    config = SessionOrchestrationConfig(
        mode='hybrid',
        final_output_mode='supervised',
        allow_followup_actions=False,
        stop_strategy='max_cycles',
        allow_reevaluate_loop=True,
        max_cycles=4,
    )
    assert config.to_metadata_patch() == {
        'orchestration_mode': 'hybrid',
        'final_output_mode': 'supervised',
        'allow_followup_actions': False,
        'stop_strategy': 'max_cycles',
        'allow_reevaluate_loop': True,
        'max_orchestration_cycles': 4,
    }
    assert SessionOrchestrationConfig().to_metadata_patch() == {}
    with pytest.raises(ValidationError) as excinfo:
        _ = SessionOrchestrationConfig(max_cycles=0)
    assert _errors(excinfo) == [(('max_cycles',), 'Input should be greater than 0')]


def test_structured_output_configuration_and_patch() -> None:
    """Structured output is configured when either field is set."""
    assert StructuredOutputConfig().is_configured() is False
    assert StructuredOutputConfig().to_metadata_patch() == {}
    assert StructuredOutputConfig(enabled=False).is_configured() is True
    assert StructuredOutputConfig(model='Answer').is_configured() is True
    assert StructuredOutputConfig(enabled=True, model='Answer').to_metadata_patch() == {
        'enabled': True,
        'model': 'Answer',
    }


def test_swarm_config_is_configured_only_beyond_defaults() -> None:
    """Default-valued swarm fields do not count as configuration."""
    assert SwarmConfig().is_configured() is False
    assert SwarmConfig(swarm_has_final_tool=False).is_configured() is False
    assert SwarmConfig(swarm_has_final_tool=True).is_configured() is True
    assert SwarmConfig(swarm_id='s').is_configured() is True


def test_swarm_config_patch_keeps_defaults_and_drops_none() -> None:
    """The swarm patch dumps every non-None field, including defaults."""
    config = SwarmConfig(
        swarm_id='s-1',
        agent_roster=[SwarmAgentConfig(agent_id='a-1', name='Analyst')],
        agent_invocation_tool_map={'a-1': 'tool-1'},
    )
    assert config.to_metadata_patch() == {
        'swarm_id': 's-1',
        'agent_roster': [
            {
                'agent_id': 'a-1',
                'name': 'Analyst',
                'use_as_final_output': False,
                'has_final_tool': False,
                'invokes_via_dependency': [],
                'memory_defined_skills': [],
                'memory_bound_resources': [],
            },
        ],
        'agent_invocation_tool_map': {'a-1': 'tool-1'},
        'swarm_has_final_tool': False,
    }


def test_system_tools_patch_renames_keys() -> None:
    """System tool controls map to their v2 metadata names."""
    config = SystemToolsConfig(
        allowed_tools=['web_search'],
        approved_compose_argument_targets=['send_email.body'],
        allow_private_data=False,
        allow_private_data_placeholders=True,
    )
    assert config.to_metadata_patch() == {
        'allowed_system_tools': ['web_search'],
        'approved_compose_argument_targets': ['send_email.body'],
        'allow_private_data_in_system_tools': False,
        'allow_private_data_placeholders_in_system_tools': True,
    }
    assert SystemToolsConfig().to_metadata_patch() == {}
    assert SystemToolsConfig(allowed_tools=[]).to_metadata_patch() == {'allowed_system_tools': []}


def test_auth_mode_and_capability_values() -> None:
    """The enum values are the v1 wire strings."""
    assert [mode.value for mode in AuthMode] == [
        'none',
        'api_key',
        'bearer',
        'basic',
        'oauth2_auth_code',
        'oauth2_pkce',
        'oauth2_client_credentials',
        'oauth2_device_code',
        'service_account',
        'custom',
    ]
    assert [capability.value for capability in ProviderCapability] == [
        'read',
        'write',
        'search',
        'export',
        'import',
        'webhooks',
        'streaming',
        'bulk',
        'dry_run',
        'pagination',
        'rate_limited',
    ]


def test_provider_metadata_queries_and_serialization() -> None:
    """Provider metadata answers auth/capability queries and sorts capabilities."""
    metadata = ProviderMetadata(
        name='crm',
        display_name='CRM',
        version='1.2.0',
        auth_modes=(AuthMode.API_KEY, AuthMode.OAUTH2_PKCE),
        scopes={'contacts.read': 'Read contacts'},
        capabilities=frozenset({ProviderCapability.WRITE, ProviderCapability.BULK}),
        tags=('sales',),
        extras={'tier': 'gold'},
    )
    assert metadata.supports_auth(AuthMode.API_KEY) is True
    assert metadata.supports_auth(AuthMode.BASIC) is False
    assert metadata.has_capability(ProviderCapability.BULK) is True
    assert metadata.has_capability(ProviderCapability.READ) is False
    assert metadata.to_dict() == {
        'name': 'crm',
        'display_name': 'CRM',
        'version': '1.2.0',
        'description': '',
        'auth_modes': ['api_key', 'oauth2_pkce'],
        'scopes': {'contacts.read': 'Read contacts'},
        'capabilities': ['bulk', 'write'],
        'documentation_url': None,
        'homepage_url': None,
        'tags': ['sales'],
        'extras': {'tier': 'gold'},
    }


@pytest.mark.parametrize(
    'kwargs',
    [
        {'name': '', 'display_name': 'D', 'version': '1'},
        {'name': 'n', 'display_name': '', 'version': '1'},
        {'name': 'n', 'display_name': 'D', 'version': ''},
    ],
)
def test_provider_metadata_requires_identity_fields(kwargs: dict[str, str]) -> None:
    """Name, display name and version must all be non-empty."""
    with pytest.raises(ValueError, match=r'^') as excinfo:
        _ = ProviderMetadata(
            name=kwargs['name'],
            display_name=kwargs['display_name'],
            version=kwargs['version'],
        )
    assert str(excinfo.value) == 'ProviderMetadata requires name, display_name, and version'
