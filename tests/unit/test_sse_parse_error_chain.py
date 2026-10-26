"""A malformed SSE frame's error carries no copy of the frame (U05-D11)."""

from __future__ import annotations

import pytest

from maivn._internal.transport.sse_client import SseParseError, parse_sse_frame

FRAME_VALUE = 'private-frame-value-123'


def test_malformed_json_data_raises_without_a_cause_holding_the_payload() -> None:
    """Error reporters that walk exception chains never see the frame's data."""
    frame = f'id: 1\nevent: final\ndata: {{"text": "{FRAME_VALUE}"\n\n'

    with pytest.raises(SseParseError) as caught:
        parse_sse_frame(frame)

    error = caught.value
    assert str(error) == 'SSE data field must contain a JSON object'
    assert error.__cause__ is None
    assert error.__context__ is None
