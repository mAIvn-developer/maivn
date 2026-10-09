# ruff: noqa: SLF001 - DecisionClient._from_backend is the in-memory test seam.
"""Boundary contracts for immutable, explicitly public decision inputs."""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest
from pydantic import ValidationError

from maivn._internal.decisions.platform import ProviderError
from maivn.decisions import (
    DecisionAnswer,
    DecisionChoice,
    DecisionClient,
    DecisionConfig,
    DecisionPrediction,
    DecisionRequest,
    DecisionStatus,
    DecisionUsage,
)

if TYPE_CHECKING:
    from collections.abc import Mapping


def _payload() -> dict[str, object]:
    return {
        'name': 'intent',
        'state': 'synthetic-state-marker',
        'instructions': 'synthetic-instructions-marker',
        'choices': (
            DecisionChoice(key='proceed', description='synthetic-description-marker'),
            DecisionChoice(key='skip', description='No response needed'),
        ),
    }


def test_unknown_is_default_and_sensitive_text_is_not_in_repr() -> None:
    """Constructing a request never implicitly grants public-data clearance."""
    request = DecisionRequest.model_validate(_payload())
    assert request.privacy == 'unknown'
    assert 'synthetic-state-marker' not in repr(request)
    assert 'synthetic-instructions-marker' not in repr(request)
    assert 'synthetic-description-marker' not in repr(request.choices[0])
    with pytest.raises(ValidationError, match='frozen'):
        request.state = 'replacement'
    with pytest.raises(ValidationError, match='frozen'):
        request.choices[0].description = 'replacement'


@pytest.mark.parametrize(
    'override',
    [
        {'state': {'content': 'raw-message-marker'}},
        {'state': object()},
        {'state': 'x' * 16385},
        {'instructions': 'x' * 4097},
        {'privacy': 'redacted'},
        {'choices': (DecisionChoice(key='only', description='Only choice'),)},
        {
            'choices': (
                DecisionChoice(key='same', description='One'),
                DecisionChoice(key='same', description='Two'),
            )
        },
        # Marked private values are a declared field (shield, then decide); malformed
        # declarations are still refused.
        {'private_data': {'email': ''}},
        {'private_data': (('email', 'first-private-value'), ('email', 'second-private-value'))},
        {'private_data': {1: 'first-private-value', '1': 'second-private-value'}},
        {'private_data': {'email': {'nested': 'raw-private-marker'}}},
    ],
)
def test_request_rejects_unsafe_or_ambiguous_shapes(override: Mapping[str, object]) -> None:
    """Arbitrary message objects, excess fields and invalid choices stay out."""
    with pytest.raises(ValidationError):
        DecisionRequest.model_validate({**_payload(), **override})


def test_choices_are_bounded_and_validation_errors_hide_input() -> None:
    """An error string must not echo a rejected field's private contents."""
    with pytest.raises(ValidationError) as error:
        DecisionChoice(key='proceed', description='raw-private-marker' * 2048)
    assert 'raw-private-marker' not in str(error.value)
    with pytest.raises(ValidationError):
        DecisionChoice(key='proceed', description='x' * 2049)
    choices = tuple(DecisionChoice(key=str(index), description='Option') for index in range(33))
    with pytest.raises(ValidationError):
        DecisionRequest.model_validate({**_payload(), 'choices': choices})


@pytest.mark.parametrize(
    'override',
    [
        {'model': 'synthetic-model'},
        {'timeout_seconds': 0},
        {'timeout_seconds': float('inf')},
        {'min_confidence': -0.01},
        {'min_confidence': 1.01},
        {'min_confidence': float('nan')},
        {'min_confidence': True},
        {'enabled': 'true'},
    ],
)
def test_configuration_cannot_silently_coerce_invalid_policy(
    override: Mapping[str, object],
) -> None:
    """Policy values are explicit, finite and constrained before model calls."""
    with pytest.raises(ValidationError):
        DecisionConfig.model_validate(override)


@pytest.mark.parametrize('value', [-1, True, 1.5, '10'])
def test_usage_requires_nonnegative_integer_receipts(value: object) -> None:
    """Estimated or coerced values cannot masquerade as provider token counts."""
    with pytest.raises(ValidationError):
        DecisionUsage.model_validate({'input_tokens': value})


def test_unknown_usage_remains_distinct_from_reported_zero() -> None:
    """No receipt is not evidence of a free call."""
    assert DecisionUsage().input_tokens is None
    assert DecisionUsage(input_tokens=0).input_tokens == 0


def test_custom_provider_failure_retains_its_identity() -> None:
    """A future provider's failed attempt must retain its own provider attribution."""

    class AlternateProvider:
        """Report a failed attempt from a different provider."""

        async def predict(self, request: DecisionRequest, /) -> DecisionPrediction:
            """Return only a safe error receipt."""
            assert request.privacy == 'public'
            raise ProviderError(
                DecisionStatus.INVALID,
                provider='alternate-provider',
                model='fake-model',
                usage=DecisionUsage(input_tokens=123),
            )

    async def fallback(request: DecisionRequest) -> DecisionAnswer:
        return DecisionAnswer(value=request.choices[0].key, provider='existing-workflow')

    client = DecisionClient._from_backend(  # pyright: ignore[reportPrivateUsage] - authentic backend fixture.
        AlternateProvider(),
        fallback=fallback,
        config=DecisionConfig(),
    )
    result = client.decide(DecisionRequest.model_validate({**_payload(), 'privacy': 'public'}))
    assert result.source == 'fallback'
    assert result.primary.provider == 'alternate-provider'
    assert result.primary.usage == DecisionUsage(input_tokens=123)
