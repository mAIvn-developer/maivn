"""Final-event privacy guidance reaches the ordinary SDK response."""

from __future__ import annotations

import pytest

from maivn._internal.compat.invocation import response_from_stream_events
from maivn._internal.models import StreamEvent


@pytest.mark.parametrize('guidance', [None, [], 'malformed', [None]])
def test_absent_or_malformed_guidance_keeps_the_final_response(guidance: object) -> None:
    """An optional advisory cannot hide a successfully completed invocation."""
    event = StreamEvent(
        position=1,
        event_type='final',
        data={'payload': {'content': 'done', 'privacy_guidance': guidance}},
    )
    response = response_from_stream_events([event])
    assert response.response == 'done'
    assert response.privacy_guidance == []


def test_delivered_guidance_preserves_actual_values_without_diagnostic_repr() -> None:
    """The SDK exposes the client-bound advice without redacting or resolving it again."""
    advice = {'value': 'May', 'reason': 'month_name', 'suggestions': [{'value': 'May'}]}
    event = StreamEvent(
        position=1,
        event_type='final',
        data={'payload': {'content': 'done', 'privacy_guidance': [advice]}},
    )
    response = response_from_stream_events([event])
    assert response.privacy_guidance == [advice]
    assert response.model_dump()['privacy_guidance'] == [advice]
    assert 'month_name' not in repr(response)
    response.privacy_guidance[0]['value'] = 'changed'
    assert advice['value'] == 'May'
