"""Planning model choices project independently through the SDK wire contract."""

from __future__ import annotations

import pytest

from maivn import ModelChoice, ModelConfig, PlanningChoice
from maivn._internal.models import RunOptions
from maivn._internal.wire import run_config_payload


def test_model_config_coerces_planning_strings_without_changing_response_scopes() -> None:
    """Planning strings use their own vocabulary and preserve all existing scopes."""
    config = ModelConfig.model_validate(
        {
            'response': 'balanced',
            'thinking': 'max',
            'compose_argument': 'synthetic-compose-model',
            'planning': 'advanced',
        }
    )

    assert config.response == ModelChoice(tier='balanced')
    assert config.thinking == ModelChoice(tier='max')
    assert config.compose_argument == ModelChoice(model_id='synthetic-compose-model')
    assert config.planning == PlanningChoice(tier='advanced')


def test_for_all_requires_an_explicit_opt_in_to_apply_exact_model_to_planning() -> None:
    """Response tiers never imply a planning tier; exact all-model use is explicit."""
    response_config = ModelConfig.for_all(tier='fast')
    all_model_config = ModelConfig.for_all(model_id='synthetic-model', include_planning=True)

    assert response_config.planning is None
    assert all_model_config.planning == PlanningChoice(model_id='synthetic-model')
    with pytest.raises(ValueError, match='exact model_id'):
        ModelConfig.for_all(tier='fast', include_planning=True)


def test_run_config_wire_projects_planning_alongside_existing_model_scopes() -> None:
    """Planning, response, and compose_argument survive one complete SDK projection."""
    config = ModelConfig.model_validate(
        {
            'response': 'balanced',
            'compose_argument': 'synthetic-compose-model',
            'planning': 'deep',
        }
    )
    assert config.response is not None
    assert config.compose_argument is not None
    assert config.planning is not None
    options = RunOptions(user_id='usr-planning', thread_id='thr-planning')
    resolved = options.model_copy(
        update={
            'model_directive': config.response.tier,
            'system_model_choices': {
                'compose_argument': config.compose_argument.model_dump(
                    mode='json', exclude_none=True
                )
            },
            'planning': config.planning,
        }
    )

    assert run_config_payload(resolved) == {
        'user_id': 'usr-planning',
        'thread_id': 'thr-planning',
        'model_directive': 'balanced',
        'system_model_choices': {'compose_argument': {'model_id': 'synthetic-compose-model'}},
        'planning': {'tier': 'deep'},
    }


def test_omitted_planning_keeps_legacy_run_config_bytes() -> None:
    """Adding the optional field does not change old SDK payloads."""
    options = RunOptions(user_id='usr-legacy', thread_id='thr-legacy', model_directive='max')

    assert run_config_payload(options) == {
        'user_id': 'usr-legacy',
        'thread_id': 'thr-legacy',
        'model_directive': 'max',
    }
