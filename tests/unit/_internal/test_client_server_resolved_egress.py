"""Server-resolved content passes through this process untouched.

Owner ruling 2026-08-21 moved final rehydration to the server, which now resolves
BOTH tag classes into the terminal `final` frame this client builds `InvokeResponse`
from. Thread 51's client-side resolution predates that and still runs.

The reconciliation decision is to KEEP the client-side pass, as a compatibility
fallback rather than the primary mechanism:

- An SDK at this version still talks to servers that predate the ruling, and against
  those it is the only thing that resolves a caller's own value.
- It is the run's LAST resort for a key the server could not resolve, which is a real
  case: a continued thread cannot resolve LAST run's server-discovered keys until the
  vault owns cross-run map custody (that is the next order, deliberately out of scope).
- It remains load-bearing for tool-call arguments, which reach `tool_runtime` in
  placeholder form by design and are not a response surface at all.

Keeping it is only safe if it is idempotent over already-resolved content, which is
what this module pins. Substitution is a single non-recursive pass over
`{_{key}_}` tokens, so text with no tokens left is returned unchanged and a resolved
value is never re-examined as if it were a placeholder.
"""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING, Any

from maivn._internal.client import (
    _hydrated_client_events,  # pyright: ignore[reportPrivateUsage] - egress seam under test
)
from maivn._internal.compat.invocation import response_from_stream_events
from maivn._internal.models import StreamEvent

if TYPE_CHECKING:
    from collections.abc import AsyncIterator, Mapping

_SERIAL = 'SN-LPTP-8831'
_CONTACT_EMAIL = 'dana.lee@example.com'


def _server_resolved_final_event() -> StreamEvent:
    """Build the terminal frame a post-ruling server emits: already resolved."""
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
                    'content': f'Serial {_SERIAL} reached {_CONTACT_EMAIL}.',
                    'ts': '2026-08-21T12:00:00Z',
                },
                'result': {
                    'hardware_overview': {'serial_number': _SERIAL},
                    'notes': [f'unit {_SERIAL} ok', f'contact {_CONTACT_EMAIL}'],
                },
            },
        },
    )


def _collect(
    events: list[StreamEvent],
    private_data: Mapping[object, object] | None,
) -> list[StreamEvent]:
    async def source() -> AsyncIterator[StreamEvent]:
        for event in events:
            yield event

    async def drain() -> list[StreamEvent]:
        return [
            event async for event in _hydrated_client_events(source(), private_data=private_data)
        ]

    return asyncio.run(drain())


def test_server_resolved_content_survives_the_client_pass_unchanged() -> None:
    """The client still holds the map; running it over resolved text must be a no-op."""
    hydrated = _collect([_server_resolved_final_event()], {'serial_number': _SERIAL})
    response = response_from_stream_events(hydrated)

    result: Any = response.result
    assert response.response == f'Serial {_SERIAL} reached {_CONTACT_EMAIL}.'
    assert result['hardware_overview']['serial_number'] == _SERIAL
    assert result['notes'] == [f'unit {_SERIAL} ok', f'contact {_CONTACT_EMAIL}']


def test_the_value_is_not_doubled_when_both_halves_resolve() -> None:
    """Double substitution would show up as a repeated value, not a missing one."""
    hydrated = _collect([_server_resolved_final_event()], {'serial_number': _SERIAL})
    response = response_from_stream_events(hydrated)

    assert response.response.count(_SERIAL) == 1
    assert response.response.count(_CONTACT_EMAIL) == 1


def test_a_server_discovered_value_needs_no_client_key_to_survive() -> None:
    """`pii_email_1` is positional and unknown here; the resolved value still arrives."""
    hydrated = _collect([_server_resolved_final_event()], {'serial_number': _SERIAL})
    response = response_from_stream_events(hydrated)

    assert _CONTACT_EMAIL in response.response


def test_the_client_pass_still_resolves_against_a_server_that_predates_the_ruling() -> None:
    """The compatibility half of the decision: an older server sends placeholders."""
    legacy_event = StreamEvent(
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
                    'ts': '2026-08-21T12:00:00Z',
                },
                'result': {'hardware_overview': {'serial_number': '{_{serial_number}_}'}},
            },
        },
    )

    hydrated = _collect([legacy_event], {'serial_number': _SERIAL})
    response = response_from_stream_events(hydrated)

    result: Any = response.result
    assert _SERIAL in response.response
    assert result['hardware_overview']['serial_number'] == _SERIAL
