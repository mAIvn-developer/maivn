"""Unit tests for internal SDK response models."""

from __future__ import annotations

from maivn_contracts.messages import Message

from maivn._internal.models import InvokeResponse, JsonObject


def _invoke_response(usage: JsonObject) -> InvokeResponse:
    return InvokeResponse(
        final_message=Message.model_validate(
            {
                'message_id': 'msg-final',
                'role': 'assistant',
                'content': 'done',
                'ts': '2026-07-09T12:00:00Z',
            },
        ),
        session_id='ses-test',
        root_event_id='evt-root',
        event_positions=[1],
        usage=usage,
        response='done',
    )


_CACHE_READ_TOKENS = 100
_V1_TOTAL_TOKENS = 15


def test_token_usage_maps_brain_loop_names() -> None:
    """Brain-loop cache token names map to the v1 public response shape."""
    response = _invoke_response(
        {
            'input_tokens': 11,
            'output_tokens': 7,
            'cache_read_input_tokens': 5,
            'cache_creation_input_tokens': 3,
            'reasoning_tokens': 2,
        },
    )

    assert response.token_usage == {
        'input_tokens': 11,
        'output_tokens': 7,
        'cache_read_tokens': 5,
        'cache_creation_tokens': 3,
        'reasoning_tokens': 2,
        'total_tokens': 18,
    }


def test_token_usage_passes_through_v1_names() -> None:
    """Existing v1 cache token names remain available without renaming loss."""
    response = _invoke_response(
        {
            'input_tokens': 13,
            'output_tokens': 4,
            'cache_read_tokens': 6,
            'cache_creation_tokens': 2,
            'reasoning_tokens': 1,
        },
    )

    assert response.token_usage == {
        'input_tokens': 13,
        'output_tokens': 4,
        'cache_read_tokens': 6,
        'cache_creation_tokens': 2,
        'reasoning_tokens': 1,
        'total_tokens': 17,
    }


def test_token_usage_returns_none_for_empty_usage() -> None:
    """An empty terminal usage payload exposes no compatibility telemetry."""
    assert _invoke_response({}).token_usage is None


def test_token_usage_returns_none_without_input_or_output_fields() -> None:
    """Cache-only usage does not imply that a token total was reported."""
    response = _invoke_response({'cache_read_input_tokens': 9})

    assert response.token_usage is None


def test_token_usage_preserves_present_zero_values() -> None:
    """Present zero input and output counts produce a real zero-token summary."""
    response = _invoke_response({'input_tokens': 0, 'output_tokens': 0})

    assert response.token_usage == {
        'input_tokens': 0,
        'output_tokens': 0,
        'cache_read_tokens': 0,
        'cache_creation_tokens': 0,
        'reasoning_tokens': 0,
        'total_tokens': 0,
    }


def test_token_usage_coerces_non_integer_values_to_zero() -> None:
    """Malformed token values cannot break the compatibility response surface."""
    response = _invoke_response(
        {
            'input_tokens': 'invalid',
            'output_tokens': 'also-invalid',
            'cache_read_tokens': 'invalid',
            'cache_creation_input_tokens': None,
            'reasoning_tokens': False,
        },
    )

    assert response.token_usage == {
        'input_tokens': 0,
        'output_tokens': 0,
        'cache_read_tokens': 0,
        'cache_creation_tokens': 0,
        'reasoning_tokens': 0,
        'total_tokens': 0,
    }


def test_total_tokens_preserves_v1_input_plus_output_contract() -> None:
    """Public total excludes separately reported cache buckets, matching v1."""
    response = _invoke_response(
        {
            'input_tokens': 10,
            'output_tokens': 5,
            'cache_read_input_tokens': 100,
            'cache_creation_input_tokens': 20,
        },
    )

    usage = response.token_usage
    assert usage is not None
    assert usage['cache_read_tokens'] == _CACHE_READ_TOKENS
    assert usage['total_tokens'] == _V1_TOTAL_TOKENS
