"""The caller's own private values come back resolved, not as placeholders."""

from __future__ import annotations

import asyncio
import json
from typing import TYPE_CHECKING, Any

import httpx
from pydantic import AnyUrl

from maivn import Client, ClientConfig
from maivn._internal.client import (
    _hydrated_client_events,  # pyright: ignore[reportPrivateUsage] - egress seam under test
    _local_resolution_data,  # pyright: ignore[reportPrivateUsage] - egress seam under test
)
from maivn._internal.compat.invocation import response_from_stream_events
from maivn._internal.compat.privacy import PrivateData, RedactedMessage
from maivn._internal.models import StreamEvent
from maivn._internal.reporting.stream_events import StreamEventProjector

if TYPE_CHECKING:
    from collections.abc import AsyncIterator, Mapping

_SERIAL = 'SN-LPTP-8831'


def _final_event() -> StreamEvent:
    """Build the terminal event the data plane publishes, redacted as it persists it."""
    return StreamEvent(
        position=1,
        event_type='final',
        data={
            'type': 'final',
            'session_id': 'ses-private',
            'payload': {
                'final_message': {
                    'message_id': 'msg-final',
                    'role': 'assistant',
                    'content': 'Serial {_{serial_number}_} passed every check.',
                    'ts': '2026-08-20T12:00:00Z',
                },
                'result': {
                    'hardware_overview': {'serial_number': '{_{serial_number}_}'},
                    'notes': ['unit {_{serial_number}_} ok'],
                },
            },
        },
    )


async def _collect(
    events: list[StreamEvent],
    private_data: Mapping[object, object] | None,
) -> list[StreamEvent]:
    async def source() -> AsyncIterator[StreamEvent]:
        for event in events:
            yield event

    return [event async for event in _hydrated_client_events(source(), private_data=private_data)]


def test_declared_private_value_is_resolved_in_the_returned_result() -> None:
    """Studio and terminal output must show the serial, exactly as v1 did."""
    hydrated = asyncio.run(_collect([_final_event()], {'serial_number': _SERIAL}))
    response = response_from_stream_events(hydrated)

    result: Any = response.result
    assert result['hardware_overview']['serial_number'] == _SERIAL
    assert result['notes'] == [f'unit {_SERIAL} ok']
    assert _SERIAL in response.response
    assert '{_{serial_number}_}' not in response.response


def test_invoke_response_retains_local_display_provenance_outside_serialization() -> None:
    """Final display metadata is local and excluded from ordinary response exports."""
    hydrated = asyncio.run(_collect([_final_event()], {'serial_number': _SERIAL}))
    response = response_from_stream_events(hydrated)
    assert response.private_value_restorations == [
        {'path': '/response', 'start': 7, 'end': 7 + len(_SERIAL), 'key': 'serial_number'},
    ]
    assert 'private_value_restorations' not in response.model_dump()


def test_display_only_reference_survives_sdk_hydration() -> None:
    """The caller can compare a protected field name with its restored value."""
    event = _final_event()
    payload: Any = event.data['payload']
    payload['final_message']['content'] = (
        'Observed [private:serial_number]; actual {_{serial_number}_}'
    )
    response = response_from_stream_events(
        asyncio.run(_collect([event], {'serial_number': _SERIAL}))
    )
    assert response.response == f'Observed [private:serial_number]; actual {_SERIAL}'
    assert len(response.private_value_restorations) == 1


def test_an_undeclared_key_stays_a_visible_placeholder() -> None:
    """A value this process cannot resolve must not be silently blanked."""
    hydrated = asyncio.run(_collect([_final_event()], {'other_key': 'x'}))
    response = response_from_stream_events(hydrated)

    result: Any = response.result
    assert result['hardware_overview']['serial_number'] == '{_{serial_number}_}'


def test_local_restoration_marks_exact_payload_spans_without_mutating_source() -> None:
    """Canonical and compatibility payloads each use their own pointer roots."""
    original = _final_event().model_copy(
        update={
            'compat_payload': {'response': '🙂 {_{serial_number}_}'},
        }
    )
    hydrated = asyncio.run(_collect([original], {'serial_number': _SERIAL}))[0]
    assert hydrated.payload['private_value_restorations'] == [
        {'path': '/response', 'start': 2, 'end': 2 + len(_SERIAL), 'key': 'serial_number'},
    ]
    canonical = hydrated.data['payload']
    assert isinstance(canonical, dict)
    assert canonical['private_value_restorations'][0] == {
        'path': '/final_message/content',
        'start': 7,
        'end': 7 + len(_SERIAL),
        'key': 'serial_number',
    }
    assert 'private_value_restorations' not in original.payload
    assert 'private_value_restorations' not in hydrated.data


def test_a_run_without_private_data_passes_events_through_untouched() -> None:
    """No private map means no rewriting, and the same event objects come back."""
    original = _final_event()
    hydrated = asyncio.run(_collect([original], None))

    assert hydrated == [original]


def test_server_restoration_provenance_survives_local_hydration() -> None:
    """The real terminal frame arrives already restored by server custody."""
    original = _final_event()
    payload: Any = original.data['payload']
    payload['message'] = payload.pop('final_message')
    payload['message']['content'] = f'Serial {_SERIAL} passed every check.'
    payload['private_value_restorations'] = [
        {'path': '/message/content', 'start': 7, 'end': 19, 'key': 'serial_number'},
    ]
    original = StreamEventProjector().project(original)
    hydrated = asyncio.run(_collect([original], {'serial_number': _SERIAL}))
    response = response_from_stream_events(hydrated)
    assert response.private_value_restorations == [
        {'path': '/response', 'start': 7, 'end': 19, 'key': 'serial_number'},
    ]
    assert {'path': '/responses/0', 'start': 7, 'end': 19, 'key': 'serial_number'} in (
        hydrated[0].payload['private_value_restorations']
    )


def test_a_named_known_pii_value_joins_the_local_resolution_map() -> None:
    """A value named on the message is the caller's own and must resolve locally."""
    message = RedactedMessage(
        content='Review the claim.',
        known_pii_values=[PrivateData(name='claim_id', value='CLM-2026-4401')],
    )

    resolution = _local_resolution_data(message, {'serial_number': _SERIAL})

    assert resolution['claim_id'] == 'CLM-2026-4401'
    assert resolution['serial_number'] == _SERIAL


def test_an_explicit_private_value_outranks_a_message_name() -> None:
    """`private_data` is the caller speaking for this turn and wins."""
    message = RedactedMessage(
        content='Review the claim.',
        known_pii_values=[PrivateData(name='claim_id', value='FROM-MESSAGE')],
    )

    resolution = _local_resolution_data(message, {'claim_id': 'FROM-ARGUMENT'})

    assert resolution['claim_id'] == 'FROM-ARGUMENT'


def test_client_can_request_placeholder_only_output_without_changing_default() -> None:
    """Raw final SSE plus the named map exercises the SDK fallback and query flag."""
    seen_queries: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == '/v1/invoke':
            return httpx.Response(202, json={'session_id': 'ses-private', 'stream_position': 0})
        seen_queries.append(request.url.query.decode())
        event = {
            'event_id': 'evt-final',
            'ordinal': 'ord-final',
            'type': 'final',
            'session_id': 'ses-private',
            'root_event_id': 'evt-root',
            'thread_id': 'thr-private',
            'payload': {
                'message': {
                    'message_id': 'msg-final',
                    'role': 'assistant',
                    'content': 'Serial {_{user_serial_number}_}.',
                    'ts': '2026-08-20T12:00:00Z',
                },
                'result': {'serial_number': '{_{user_serial_number}_}'},
            },
            'ts': '2026-08-20T12:00:00Z',
        }
        return httpx.Response(
            200,
            headers={'content-type': 'text/event-stream'},
            content=f'id: 1\nevent: final\ndata: {json.dumps(event)}\n\n',
        )

    message = RedactedMessage(
        content=f'Serial {_SERIAL}.',
        known_pii_values=[PrivateData(name='serial_number', value=_SERIAL)],
    )
    for restore in (True, False):
        client = Client(
            config=ClientConfig(api_key='test-key', base_url=AnyUrl('http://testserver')),
            transport=httpx.MockTransport(handler),
            private_data_store=None,
            restore_private_values=restore,
        )
        response = client.invoke(message)
        expected = _SERIAL if restore else '{_{user_serial_number}_}'
        assert response.response == f'Serial {expected}.'
        assert response.result == {'serial_number': expected}
    assert seen_queries == ['', 'redacted_output=true']
