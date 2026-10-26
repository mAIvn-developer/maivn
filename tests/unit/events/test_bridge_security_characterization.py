"""Characterization of the EventBridge audience policy (``EventBridgeSecurityPolicy``).

``internal`` bridges pass payloads through untouched. ``frontend_safe`` bridges
summarize injected private-data fields, mask redaction receipts, replace error
text that leaks internals and drop error details before anything reaches the
browser. These tests pin the exact output of each sanitizer through the public
policy class and through ``EventBridge``.

No async pytest plugin is configured for this package, so bridge tests drive
their coroutine with ``asyncio.run()``.
"""

from __future__ import annotations

import asyncio
import copy
import dataclasses
import logging
from typing import TYPE_CHECKING, cast

import pytest

from maivn.events import EventBridge, EventBridgeSecurityPolicy

if TYPE_CHECKING:
    from maivn.events import BridgeAudience

INTERNAL_ERROR = 'An internal error occurred. Please try again.'
LEAKY_ERROR = 'Traceback (most recent call last): /srv/app/tool.py'
WINDOWS_LEAK = 'failed at C:\\srv\\app'
REDACTED = '<redacted>'
SAFE = EventBridgeSecurityPolicy(audience='frontend_safe')
INTERNAL = EventBridgeSecurityPolicy()
UNKNOWN_WARNING = (
    "Unknown event type 'custom_event' emitted to frontend_safe bridge; "
    'applying generic injected-fields sanitization. '
    'Add the type to _KNOWN_FRONTEND_SAFE_EVENT_TYPES or extend '
    'sanitize_event() if it needs custom handling.'
)


def _sanitize(event_type: str, data: dict[str, object]) -> dict[str, object]:
    """Sanitize a deep copy for ``frontend_safe`` and prove the input is not mutated."""
    before = copy.deepcopy(data)
    result = SAFE.sanitize_event(event_type, data)
    assert data == before
    return result


# MARK: Policy construction


def test_default_policy_audience_is_internal() -> None:
    """A policy built without arguments targets the internal audience."""
    assert EventBridgeSecurityPolicy().audience == 'internal'
    assert EventBridge('s').audience == 'internal'


@pytest.mark.parametrize(
    ('raw', 'expected'),
    [
        (' Frontend_Safe ', 'frontend_safe'),
        ('FRONTEND_SAFE', 'frontend_safe'),
        ('\tInternal\n', 'internal'),
    ],
)
def test_audience_is_trimmed_and_lowercased(raw: str, expected: str) -> None:
    """Audience spelling is normalized on the policy and on the bridge."""
    assert EventBridgeSecurityPolicy(audience=cast('BridgeAudience', raw)).audience == expected
    assert EventBridge('s', audience=cast('BridgeAudience', raw)).audience == expected


@pytest.mark.parametrize('raw', ['public', '', 'frontend-safe', 'internals'])
def test_unknown_audience_is_rejected(raw: str) -> None:
    """Only ``internal`` and ``frontend_safe`` are accepted."""
    message = "EventBridge audience must be 'internal' or 'frontend_safe'"
    with pytest.raises(ValueError, match=f'^{message}$'):
        _ = EventBridgeSecurityPolicy(audience=cast('BridgeAudience', raw))
    with pytest.raises(ValueError, match=f'^{message}$'):
        _ = EventBridge('s', audience=cast('BridgeAudience', raw))


def test_policy_is_immutable() -> None:
    """The audience cannot be changed after construction."""
    policy = EventBridgeSecurityPolicy(audience='frontend_safe')
    with pytest.raises(dataclasses.FrozenInstanceError):
        policy.audience = 'internal'  # pyright: ignore[reportAttributeAccessIssue]
    assert policy == EventBridgeSecurityPolicy(audience='frontend_safe')


# MARK: Internal audience


@pytest.mark.parametrize(
    'event_type',
    ['tool_event', 'final', 'enrichment', 'error', 'agent_assignment', 'custom_event'],
)
def test_internal_audience_returns_the_same_payload(
    event_type: str,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Internal bridges see payloads exactly as emitted, without warnings."""
    data: dict[str, object] = {
        'args': {'private_data_injected': {'ssn': '123-45-6789'}},
        'result': {'interrupt_data_injected': {'pin': '0000'}},
        'error': LEAKY_ERROR,
        'details': {'trace': 'x'},
        'redaction': {'added_private_data': {'ssn': '123-45-6789'}},
    }
    with caplog.at_level(logging.DEBUG):
        assert INTERNAL.sanitize_event(event_type, data) is data
    assert caplog.records == []


# MARK: Tool events


def test_tool_event_summarizes_injected_fields_in_args_and_results() -> None:
    """Injected private data collapses to its key names at both payload levels."""
    data: dict[str, object] = {
        'tool_name': 'lookup',
        'args': {'q': 'x', 'private_data_injected': {'ssn': '123-45-6789', 'dob': '1990'}},
        'result': {'ok': True, 'interrupt_data_injected': {'pin': '0000'}},
        'tool': {
            'id': 't1',
            'args': {'private_data_injected': {'ssn': '123-45-6789'}},
            'result': {'interrupt_data_injected': {'pin': '0000'}, 'value': 3},
        },
    }

    assert _sanitize('tool_event', data) == {
        'tool_name': 'lookup',
        'args': {'q': 'x', 'private_data_injected': ['ssn', 'dob']},
        'result': {'ok': True, 'interrupt_data_injected': ['pin']},
        'tool': {
            'id': 't1',
            'args': {'private_data_injected': ['ssn']},
            'result': {'interrupt_data_injected': ['pin'], 'value': 3},
        },
    }


@pytest.mark.parametrize(
    ('injected', 'summary'),
    [
        ({'ssn': 'raw', 7: 'raw'}, ['ssn', '7']),
        (['ssn', 'dob'], ['str', 'str']),
        (None, []),
        ('123-45-6789', ['str']),
        (42, ['int']),
        (('ssn',), ['tuple']),
    ],
)
def test_injected_field_summary_never_carries_mapping_values(
    injected: object,
    summary: list[str],
) -> None:
    """Mappings become their keys, list items and scalars their type, None nothing."""
    data: dict[str, object] = {'args': {'private_data_injected': injected}}

    assert _sanitize('tool_event', data) == {'args': {'private_data_injected': summary}}


def test_tool_event_without_sensitive_content_keeps_its_identity() -> None:
    """Nothing to sanitize means the same payload object comes back."""
    data: dict[str, object] = {
        'args': {'q': 'x'},
        'result': {'ok': True},
        'error': 'Rate limited',
        'tool': {'args': {'q': 'x'}, 'result': {'ok': True}, 'error': 'Rate limited'},
    }

    assert SAFE.sanitize_event('tool_event', data) is data


def test_tool_event_ignores_non_mapping_containers() -> None:
    """Non-mapping args, results and tool values pass, but injected fields inside are summarized."""
    data: dict[str, object] = {
        'args': [{'private_data_injected': {'ssn': 'raw'}}],
        'result': 'private_data_injected',
        'error': 17,
        'tool': 'not-a-mapping',
    }
    nested: dict[str, object] = {
        'tool': {'args': ['private_data_injected'], 'result': None, 'error': '   '},
    }

    assert SAFE.sanitize_event('tool_event', data) == {
        'args': [{'private_data_injected': ['ssn']}],
        'result': 'private_data_injected',
        'error': 17,
        'tool': 'not-a-mapping',
    }
    assert SAFE.sanitize_event('tool_event', nested) is nested


def test_event_type_is_matched_after_trimming_and_lowercasing() -> None:
    """``' Error '`` is sanitized as an error event, not as an unknown type."""
    data: dict[str, object] = {'error': 'Rate limited', 'details': {'trace': 'x'}}

    assert SAFE.sanitize_event(' Error ', data) == {'error': 'Rate limited', 'details': {}}


# MARK: Error text


@pytest.mark.parametrize(
    ('error', 'expected'),
    [
        (LEAKY_ERROR, INTERNAL_ERROR),
        (WINDOWS_LEAK, INTERNAL_ERROR),
        ('  Rate limited  ', 'Rate limited'),
        ('Agent execution failed: Quota exceeded', 'Quota exceeded'),
        (
            'LLM payload contains private data values: ssn',
            'LLM payload contains private data values: ssn',
        ),
    ],
)
def test_tool_event_error_text_is_made_user_safe(error: str, expected: str) -> None:
    """Top-level and nested tool error text pass through the user-facing sanitizer."""
    data: dict[str, object] = {'error': error, 'tool': {'error': error}}

    assert _sanitize('tool_event', data) == {'error': expected, 'tool': {'error': expected}}


def test_nested_error_rewrites_error_and_message_together() -> None:
    """A leaking nested error replaces both the error and message fields."""
    data: dict[str, object] = {'tool': {'error': LEAKY_ERROR, 'message': 'friendly', 'id': 't1'}}

    assert _sanitize('tool_event', data) == {
        'tool': {'error': INTERNAL_ERROR, 'message': INTERNAL_ERROR, 'id': 't1'},
    }


@pytest.mark.parametrize(('event_type', 'key'), [('tool_event', 'tool'), ('error', 'error_info')])
def test_nested_leaking_message_is_sanitized_beside_a_safe_error(event_type: str, key: str) -> None:
    """A safe nested error does not let a leaking message through (U05 low item)."""
    data: dict[str, object] = {key: {'error': 'Quota exceeded', 'message': LEAKY_ERROR}}

    assert _sanitize(event_type, data)[key] == {
        'error': 'Quota exceeded',
        'message': INTERNAL_ERROR,
    }


def test_nested_message_alone_is_sanitized_without_adding_an_error_key() -> None:
    """When only ``message`` is present it is the field that gets rewritten."""
    data: dict[str, object] = {'tool': {'message': LEAKY_ERROR}}

    assert _sanitize('tool_event', data) == {'tool': {'message': INTERNAL_ERROR}}


def test_nested_error_alone_is_sanitized_without_adding_a_message_key() -> None:
    """When only ``error`` is present no ``message`` key appears."""
    data: dict[str, object] = {'tool': {'error': WINDOWS_LEAK}}

    assert _sanitize('tool_event', data) == {'tool': {'error': INTERNAL_ERROR}}


# MARK: Other result-bearing events


def test_system_tool_complete_summarizes_result_fields() -> None:
    """System tool results are scrubbed at the top level and under ``tool``."""
    data: dict[str, object] = {
        'tool_id': 't1',
        'result': {'private_data_injected': {'ssn': 'raw'}},
        'tool': {'result': {'interrupt_data_injected': None}},
    }

    assert _sanitize('system_tool_complete', data) == {
        'tool_id': 't1',
        'result': {'private_data_injected': ['ssn']},
        'tool': {'result': {'interrupt_data_injected': []}},
    }


def test_agent_assignment_summarizes_results_and_sanitizes_errors() -> None:
    """Assignment results are scrubbed and both error locations are made safe."""
    data: dict[str, object] = {
        'agent_name': 'researcher',
        'result': {'private_data_injected': {'ssn': 'raw'}},
        'error': LEAKY_ERROR,
        'assignment': {
            'result': {'private_data_injected': {'dob': 'raw'}},
            'error': WINDOWS_LEAK,
        },
    }

    assert _sanitize('agent_assignment', data) == {
        'agent_name': 'researcher',
        'result': {'private_data_injected': ['ssn']},
        'error': INTERNAL_ERROR,
        'assignment': {'result': {'private_data_injected': ['dob']}, 'error': INTERNAL_ERROR},
    }


def test_final_summarizes_result_and_output_result() -> None:
    """Final results are scrubbed at the top level and under ``output``."""
    data: dict[str, object] = {
        'response': 'done',
        'result': {'interrupt_data_injected': {'pin': 'raw'}},
        'output': {'response': 'done', 'result': {'private_data_injected': {'ssn': 'raw'}}},
    }

    assert _sanitize('final', data) == {
        'response': 'done',
        'result': {'interrupt_data_injected': ['pin']},
        'output': {'response': 'done', 'result': {'private_data_injected': ['ssn']}},
    }


@pytest.mark.parametrize('event_type', ['final', 'system_tool_complete', 'agent_assignment'])
def test_result_events_without_sensitive_content_keep_identity(event_type: str) -> None:
    """Clean result-bearing payloads come back as the same object."""
    data: dict[str, object] = {
        'result': {'ok': True},
        'output': {'result': {'ok': True}},
        'tool': {'result': 'text'},
        'assignment': {'result': {'ok': True}, 'error': 'Rate limited'},
        'error': 'Rate limited',
    }

    assert SAFE.sanitize_event(event_type, data) is data


# MARK: Enrichment receipts


def test_enrichment_masks_every_redaction_receipt_value() -> None:
    """Redaction receipts keep their keys and counts but never their values."""
    redaction: dict[str, object] = {
        'added_private_data': {'ssn': '123-45-6789', 'dob': '1990-01-01'},
        'merged_private_data': 'raw-string',
        'matched_known_pii_values': ['123-45-6789', 'jane@example.com'],
        'unmatched_known_pii_values': 'raw-string',
        'count': 2,
    }
    data: dict[str, object] = {
        'phase': 'redaction',
        'redaction': redaction,
        'enrichment': {'phase': 'redaction', 'redaction': dict(redaction)},
    }
    masked: dict[str, object] = {
        'added_private_data': {'ssn': REDACTED, 'dob': REDACTED},
        'merged_private_data': REDACTED,
        'matched_known_pii_values': [REDACTED, REDACTED],
        'unmatched_known_pii_values': [REDACTED],
        'count': 2,
    }

    assert _sanitize('enrichment', data) == {
        'phase': 'redaction',
        'redaction': masked,
        'enrichment': {'phase': 'redaction', 'redaction': masked},
    }


def test_enrichment_mask_handles_empty_and_absent_receipts() -> None:
    """Empty receipts stay empty and ``None`` receipts are left as ``None``."""
    data: dict[str, object] = {
        'redaction': {
            'added_private_data': {},
            'merged_private_data': None,
            'matched_known_pii_values': [],
        },
    }

    assert _sanitize('enrichment', data) == {
        'redaction': {
            'added_private_data': {},
            'merged_private_data': None,
            'matched_known_pii_values': [],
        },
    }


def test_enrichment_masks_nested_receipt_when_top_level_is_absent() -> None:
    """The nested ``enrichment.redaction`` is masked on its own."""
    data: dict[str, object] = {
        'enrichment': {'message': 'm', 'redaction': {'unmatched_known_pii_values': ['raw']}},
    }

    assert _sanitize('enrichment', data) == {
        'enrichment': {'message': 'm', 'redaction': {'unmatched_known_pii_values': [REDACTED]}},
    }


def test_enrichment_without_receipts_keeps_identity() -> None:
    """Enrichment payloads with no receipt values come back unchanged."""
    data: dict[str, object] = {
        'redaction': {'count': 0, 'merged_private_data': None},
        'enrichment': {'redaction': {'count': 0}},
    }
    non_mapping: dict[str, object] = {'redaction': ['raw'], 'enrichment': 'text'}
    nested_non_mapping: dict[str, object] = {'enrichment': {'redaction': 'raw'}}

    assert SAFE.sanitize_event('enrichment', data) is data
    assert SAFE.sanitize_event('enrichment', non_mapping) is non_mapping
    assert SAFE.sanitize_event('enrichment', nested_non_mapping) is nested_non_mapping


# MARK: Error events


def test_error_event_sanitizes_text_and_drops_details() -> None:
    """Frontend error events carry safe text and empty details."""
    data: dict[str, object] = {
        'error': LEAKY_ERROR,
        'details': {'trace': 'x'},
        'error_info': {'message': WINDOWS_LEAK, 'details': {'trace': 'x'}},
    }

    assert _sanitize('error', data) == {
        'error': INTERNAL_ERROR,
        'details': {},
        'error_info': {'message': INTERNAL_ERROR, 'details': {}},
    }


def test_error_event_drops_nested_details_even_when_text_is_safe() -> None:
    """``error_info.details`` is cleared on its own."""
    data: dict[str, object] = {
        'error': 'Rate limited',
        'error_info': {'message': 'Rate limited', 'details': {'retry_after': 3}},
    }

    assert _sanitize('error', data) == {
        'error': 'Rate limited',
        'error_info': {'message': 'Rate limited', 'details': {}},
    }


def test_error_event_without_details_keeps_identity() -> None:
    """Safe text with empty or absent details is returned as the same object."""
    data: dict[str, object] = {
        'error': 'Rate limited',
        'details': {},
        'error_info': {'message': 'Rate limited', 'details': {}},
    }
    bare: dict[str, object] = {'error': 'Rate limited', 'error_info': 'text'}

    assert SAFE.sanitize_event('error', data) is data
    assert SAFE.sanitize_event('error', bare) is bare


# MARK: Unknown and handler-free event types


def test_unknown_event_type_is_scrubbed_recursively_with_a_warning(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Custom types get injected fields summarized at any dict/list depth."""
    data: dict[str, object] = {
        'label': 'kept',
        'items': [
            {'private_data_injected': {'ssn': 'raw'}},
            'plain',
            [{'deeper': {'interrupt_data_injected': ['pin']}}],
        ],
        'private_data_injected': None,
    }

    with caplog.at_level(logging.WARNING):
        result = _sanitize('custom_event', data)

    assert result == {
        'label': 'kept',
        'items': [
            {'private_data_injected': ['ssn']},
            'plain',
            [{'deeper': {'interrupt_data_injected': ['str']}}],
        ],
        'private_data_injected': [],
    }
    assert [record.getMessage() for record in caplog.records] == [UNKNOWN_WARNING]
    assert caplog.records[0].levelno == logging.WARNING


def test_unknown_event_type_without_injected_fields_keeps_identity(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """A clean custom payload is returned as the same object; a cycle is cut with a marker."""
    data: dict[str, object] = {'items': [{'a': 1}, [2, 3]], 'nested': {'b': 'c'}}
    cyclic: dict[str, object] = dict(data)
    cyclic['self'] = cyclic

    with caplog.at_level(logging.WARNING):
        assert SAFE.sanitize_event('custom_event', data) is data
        assert SAFE.sanitize_event('custom_event', cyclic) == {**data, 'self': '<redacted>'}
    assert [record.getMessage() for record in caplog.records] == [UNKNOWN_WARNING] * 2


def test_unknown_event_type_rebuilds_only_changed_branches() -> None:
    """Untouched sub-lists and sub-dicts keep their identity inside a rebuilt payload."""
    untouched_list: list[object] = [1, {'a': 2}]
    untouched_dict: dict[str, object] = {'b': [3]}
    data: dict[str, object] = {
        'keep_list': untouched_list,
        'keep_dict': untouched_dict,
        'change': [{'private_data_injected': {'k': 'v'}}],
    }

    result = SAFE.sanitize_event('custom_event', data)

    assert result is not data
    assert result['keep_list'] is untouched_list
    assert result['keep_dict'] is untouched_dict
    assert result['change'] == [{'private_data_injected': ['k']}]


@pytest.mark.parametrize(
    'event_type',
    [
        'system_tool_start',
        'system_tool_chunk',
        'assistant_chunk',
        'status_message',
        'status_message_chunk',
        'interrupt_required',
        'hook_fired',
        'session_start',
        'session_end',
    ],
)
def test_known_types_without_a_handler_pass_clean_payloads_silently(
    event_type: str,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Known display-only types skip the unknown-type warning and keep identity."""
    data: dict[str, object] = {'text': 'hello', 'error': 'Rate limited', 'nested': {'a': [1]}}

    with caplog.at_level(logging.WARNING):
        assert SAFE.sanitize_event(event_type, data) is data
    assert caplog.records == []


# MARK: Through the bridge


def _history(audience: BridgeAudience) -> list[dict[str, object]]:
    bridge = EventBridge('security-session', audience=audience)

    async def _run() -> None:
        await bridge.emit_tool_event(
            'lookup',
            't1',
            'completed',
            args={'private_data_injected': {'ssn': '123-45-6789'}},
            error=LEAKY_ERROR,
        )
        await bridge.emit_error(LEAKY_ERROR, details={'trace': 'x'})

    asyncio.run(_run())
    return [cast('dict[str, object]', event['data']) for event in bridge.get_history()]


def test_frontend_safe_bridge_stores_sanitized_payloads() -> None:
    """History, the SSE queue and observers all see the sanitized payload."""
    tool, error = _history('frontend_safe')
    tool_nested = cast('dict[str, object]', tool['tool'])

    assert tool['args'] == {'private_data_injected': ['ssn']}
    assert tool['error'] == INTERNAL_ERROR
    assert tool_nested['args'] == {'private_data_injected': ['ssn']}
    assert tool_nested['error'] == INTERNAL_ERROR
    assert error['error'] == INTERNAL_ERROR
    assert error['details'] == {}
    assert error['error_info'] == {'message': INTERNAL_ERROR, 'details': {}}


def test_internal_bridge_stores_raw_payloads() -> None:
    """Internal bridges keep the full detail for trusted consumers."""
    tool, error = _history('internal')

    assert tool['args'] == {'private_data_injected': {'ssn': '123-45-6789'}}
    assert tool['error'] == LEAKY_ERROR
    assert error['error'] == LEAKY_ERROR
    assert error['details'] == {'trace': 'x'}
