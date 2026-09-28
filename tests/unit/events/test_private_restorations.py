"""Local restoration provenance survives text projection and stream deltas."""

import asyncio
from typing import Any, cast

from maivn._internal.client import (
    _hydrated_client_event,  # pyright: ignore[reportPrivateUsage] - local egress seam under test
)
from maivn._internal.models import StreamEvent
from maivn.events import NormalizedStreamState, forward_normalized_event, normalize_stream_event


def _event(name: str, payload: dict[str, Any]) -> StreamEvent:
    return _hydrated_client_event(
        StreamEvent(position=0, event_type=name, data={'payload': payload}),
        {'email': '**私**'},
    )


def test_snapshot_annotations_follow_delta_and_replacement_offsets() -> None:
    """Snapshot spans rebase onto emitted text for both append and replace."""
    state = NormalizedStreamState()
    normalize_stream_event(cast('Any', _event('update', {'streaming_content': '🙂 '})), state=state)
    event = normalize_stream_event(
        cast(
            'Any',
            _event(
                'update',
                {
                    'streaming_content': '🙂 {_{email}_}',
                },
            ),
        ),
        state=state,
    )[0]
    payload = event.model_dump()
    assert payload['text'] == '**私**'
    assert payload['private_value_restorations'] == [
        {'path': '/text', 'start': 0, 'end': 5, 'key': 'email'},
    ]
    replaced = normalize_stream_event(
        cast(
            'Any',
            _event(
                'update',
                {
                    'streaming_content': 'Changed {_{email}_}',
                },
            ),
        ),
        state=state,
    )[0].model_dump()
    assert replaced['replace_content'] is True
    assert replaced['private_value_restorations'][0]['start'] == len('Changed ')


def test_final_annotations_follow_selected_trimmed_response() -> None:
    """Terminal selectors and whitespace trimming preserve exact provenance."""
    event = normalize_stream_event(
        cast(
            'Any',
            _event(
                'final',
                {
                    'responses': ['ignored', '  🙂 {_{email}_}  '],
                    'response': 'Different {_{email}_}',
                },
            ),
        )
    )[0].model_dump()
    assert event['response'] == '🙂 **私**'
    assert {'path': '/responses/0', 'start': 2, 'end': 7, 'key': 'email'} in event[
        'private_value_restorations'
    ]


def test_forwarding_retains_provenance_for_supporting_reporters() -> None:
    """Capable reporters receive the local annotations along with the chunk."""
    received: list[dict[str, object]] = []

    class Reporter:
        def report_response_chunk(self, text: str, **kwargs: object) -> None:
            received.append({'text': text, **kwargs})

    event = normalize_stream_event(
        cast(
            'Any',
            _event(
                'update',
                {
                    'streaming_content': '🙂 {_{email}_}',
                },
            ),
        )
    )[0]
    asyncio.run(forward_normalized_event(event, reporter=Reporter()))
    assert received[0]['private_value_restorations'] == [
        {'path': '/text', 'start': 2, 'end': 7, 'key': 'email'},
    ]
