"""A terminal error must name itself from whatever the wire actually carries.

BENCH-2026-08-16 (claude): found by the Studio demo benchmark. The complex-types demo
failed on `structured_output_invalid_json` and reported the bare string "invoke failed" -
in the raised exception and in the Studio trace panel alike. Durable error events omit the
human-readable message on purpose (see `durable_tool_outcome_source`, "without carrying an
error message"), so the message key the SDK read was never going to be there; the stable
`error_code` beside it was.
"""

from __future__ import annotations

import pytest

from maivn._internal.compat.invocation import (
    _raise_for_terminal_error,  # pyright: ignore[reportPrivateUsage] - private raiser under test.
    response_from_stream_events,
)
from maivn._internal.errors import DurableBoundaryRefusedError, MaivnSDKError
from maivn._internal.models import StreamEvent


def _error_event(payload: dict[str, object]) -> StreamEvent:
    return StreamEvent(position=0, event_type='error', data=dict(payload))


def test_recording_refusal_explains_private_transcript_alternative() -> None:
    """A value-free durable code still produces actionable user guidance."""
    payload: dict[str, object] = {'error_code': 'redacted_message_attachment_unshieldable'}
    with pytest.raises(MaivnSDKError) as raised:
        _raise_for_terminal_error(_error_event(payload), payload, payload)
    assert 'will not be sent to a model' in str(raised.value)
    assert 'transcript produced in your private environment' in str(raised.value)


def test_error_code_names_the_failure_when_no_message_is_carried() -> None:
    """The real shape of a durable loop error: a code, a class, and no message."""
    payload: dict[str, object] = {
        'stage': 'error',
        'error_code': 'structured_output_invalid_json',
        'error_class': 'InvokeLoopError',
    }

    with pytest.raises(MaivnSDKError) as raised:
        _raise_for_terminal_error(_error_event(payload), payload, payload)

    assert 'structured_output_invalid_json' in str(raised.value)
    assert str(raised.value) != 'invoke failed'


def test_a_durable_refusal_names_where_the_boundary_refused() -> None:
    """Thread P2: `durable_event_refused` alone tells the caller nothing to act on.

    The data plane already had the path - a projection name and a field path, value-free
    by construction - and it stopped at the operator log. A run that ends at the privacy
    boundary should say which field ended it.
    """
    payload: dict[str, object] = {
        'stage': 'error',
        'error_code': 'durable_event_refused',
        'error_class': 'DurableInvokeEventAdmissionError',
        'refused_path': 'DurableLoopToolResultEventPayload.outcome.result.headers.[2].value',
    }

    with pytest.raises(DurableBoundaryRefusedError) as raised:
        _raise_for_terminal_error(_error_event(payload), payload, payload)

    assert str(raised.value) == (
        'durable_event_refused at '
        'DurableLoopToolResultEventPayload.outcome.result.headers.[2].value'
    )
    # Typed as well as named, so a surface that wants better copy than the raw code -
    # Studio does - classifies on the type instead of matching on message text.
    assert isinstance(raised.value, MaivnSDKError)
    assert raised.value.refused_path == (
        'DurableLoopToolResultEventPayload.outcome.result.headers.[2].value'
    )


def test_an_explicit_message_still_wins_over_the_code() -> None:
    """Transports that do carry prose must not be downgraded to a code."""
    payload: dict[str, object] = {
        'message': 'the model refused the request',
        'error_code': 'model_refusal',
    }

    with pytest.raises(MaivnSDKError, match='the model refused the request'):
        _raise_for_terminal_error(_error_event(payload), payload, payload)


def test_termination_reason_is_used_before_giving_up() -> None:
    """Some terminations carry a reason and no code at all."""
    payload: dict[str, object] = {'stage': 'error', 'termination_reason': 'max_turns_exhausted'}

    with pytest.raises(MaivnSDKError, match='max_turns_exhausted'):
        _raise_for_terminal_error(_error_event(payload), payload, payload)


def test_a_nameless_error_still_raises() -> None:
    """With nothing to name it, the old fallback is still correct."""
    payload: dict[str, object] = {'stage': 'error'}

    with pytest.raises(MaivnSDKError, match='invoke failed'):
        _raise_for_terminal_error(_error_event(payload), payload, payload)


def test_a_non_error_terminal_raises_nothing() -> None:
    """Only error terminals raise; a completed run must pass straight through."""
    payload: dict[str, object] = {'error_code': 'ignored_because_not_an_error'}
    event = StreamEvent(position=0, event_type='final', data=dict(payload))

    _raise_for_terminal_error(event, payload, payload)


def test_repeated_tool_refusal_preserves_the_specific_wire_reason() -> None:
    """A generic retry terminal must not erase the value-free refusal code before it."""
    refusal = StreamEvent(
        position=0,
        event_type='system_tool_error',
        data={
            'payload': {
                'outcome': {
                    'status': 'error',
                    'error': {
                        'error_code': 'private_placeholder_unresolvable',
                        'error_class': 'ToolOutcomeError',
                        'retryable': False,
                    },
                },
            },
        },
    )
    terminal = _error_event({'stage': 'error', 'error_code': 'tool_repeated_failure'})

    with pytest.raises(MaivnSDKError, match=r'^private_placeholder_unresolvable$'):
        response_from_stream_events([refusal, terminal])


@pytest.mark.parametrize(
    'code',
    ['provider_mid_stream_error', 'durable_event_refused', 'value_fill_exhausted'],
)
def test_failed_invoke_retains_session_identity_for_usage_reconciliation(code: str) -> None:
    """Failure after dispatch preserves the public identifiers needed to query its cost."""
    event = StreamEvent(
        position=1,
        event_type='error',
        data={
            'session_id': 'ses-failed',
            'root_event_id': 'evt-root-failed',
            'payload': {'error_code': code},
        },
    )
    with pytest.raises(MaivnSDKError) as caught:
        response_from_stream_events([event])
    assert caught.value.session_id == 'ses-failed'
    assert caught.value.root_event_id == 'evt-root-failed'
    assert str(caught.value) == code
