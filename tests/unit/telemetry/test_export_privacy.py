# pyright: reportPrivateUsage=false
"""Mutation-worthy privacy guard for every emitted OpenTelemetry span field."""

from __future__ import annotations

import asyncio
import json
from typing import TYPE_CHECKING, Any, cast

import pytest
from opentelemetry.sdk.trace import ReadableSpan, TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

from maivn._internal.client import _hydrated_client_events, _telemetry_client_events
from maivn._internal.models import StreamEvent
from maivn.telemetry import register_listener
from maivn.telemetry._registry import reset_listeners_for_testing
from maivn.telemetry.opentelemetry import OpenTelemetryAdapter

if TYPE_CHECKING:
    from collections.abc import AsyncIterator, Iterable, Mapping

_PLACEHOLDER = '{_{account_number}_}'
_RESOLVED = 'ACCOUNT-PRIVATE-4242'
_MESSAGE_PREFIX = 'Account verification result:'
_TOOL_ARGUMENT_MARKER = 'tool-argument-marker'
_TOOL_RESULT_MARKER = 'tool-result-marker'


def setup_function() -> None:
    """Start the guard with no process-wide listeners."""
    reset_listeners_for_testing()


def teardown_function() -> None:
    """Remove process-wide listeners after the guard."""
    reset_listeners_for_testing()


def _events(private_value: str) -> tuple[StreamEvent, ...]:
    common = {
        'session_id': 'ses-privacy-guard',
        'root_event_id': 'evt-root',
        'ts': '2026-09-01T12:00:00Z',
    }
    return (
        StreamEvent(
            position=1,
            event_type='system_tool_start',
            data={
                **common,
                'event_id': 'evt-tool-start',
                'type': 'system_tool_start',
                'correlation_id': 'call-private',
                'payload': {
                    'tool_name': 'lookup_account',
                    'tool_call': {
                        'call_id': 'call-private',
                        'spec_ref': {
                            'tool_id': 'lookup_account',
                            'namespace': 'sdk',
                            'version': 'v1',
                        },
                        'arguments': {
                            'account': private_value,
                            'marker': _TOOL_ARGUMENT_MARKER,
                        },
                    },
                },
            },
        ),
        StreamEvent(
            position=2,
            event_type='system_tool_complete',
            data={
                **common,
                'event_id': 'evt-tool-complete',
                'type': 'system_tool_complete',
                'correlation_id': 'call-private',
                'latency_ms': 8,
                'payload': {
                    'tool_name': 'lookup_account',
                    'outcome': {
                        'call_id': 'call-private',
                        'status': 'ok',
                        'duration_ms': 8,
                        'result': {
                            'account': private_value,
                            'marker': _TOOL_RESULT_MARKER,
                        },
                    },
                },
            },
        ),
        StreamEvent(
            position=3,
            event_type='final',
            data={
                **common,
                'event_id': 'evt-final',
                'type': 'final',
                'payload': {
                    'message': {
                        'message_id': 'msg-final',
                        'role': 'assistant',
                        'content': f'{_MESSAGE_PREFIX} {private_value}',
                        'ts': '2026-09-01T12:00:00Z',
                    },
                    'result': {'account': private_value},
                    'usage': {'input_tokens': 5, 'output_tokens': 3},
                },
            },
        ),
    )


@pytest.mark.parametrize('arrival_value', [_PLACEHOLDER, _RESOLVED])
def test_default_export_contains_no_content_in_any_arrival_form(arrival_value: str) -> None:
    """Placeholder and server-resolved streams both export metadata only."""
    exporter = InMemorySpanExporter()
    provider = TracerProvider()
    provider.add_span_processor(SimpleSpanProcessor(exporter))
    adapter = OpenTelemetryAdapter(tracer=provider.get_tracer('privacy.guard'))

    with register_listener(adapter):
        returned = asyncio.run(_collect(_events(arrival_value)))
    provider.force_flush()

    spans = exporter.get_finished_spans()
    assert {span.name for span in spans} == {
        'maivn.run',
        'execute_tool lookup_account',
    }
    serialized = _serialize_spans(spans)
    caller_json = json.dumps(
        [event.model_dump(mode='json') for event in returned],
        sort_keys=True,
    )

    assert _RESOLVED in caller_json
    assert _PLACEHOLDER not in serialized
    assert _RESOLVED not in serialized
    assert _MESSAGE_PREFIX not in serialized
    assert _TOOL_ARGUMENT_MARKER not in serialized
    assert _TOOL_RESULT_MARKER not in serialized
    for attribute_key in _span_attribute_keys(spans):
        assert all(
            fragment not in attribute_key.casefold()
            for fragment in ('content', 'message', 'prompt', 'argument', 'result')
        )


async def _collect(events: tuple[StreamEvent, ...]) -> list[StreamEvent]:
    async def source() -> AsyncIterator[StreamEvent]:
        for event in events:
            yield event

    return [
        event
        async for event in _hydrated_client_events(
            _telemetry_client_events(source()),
            private_data={'account_number': _RESOLVED},
        )
    ]


def _serialize_spans(spans: Iterable[ReadableSpan]) -> str:
    records = [_span_record(span) for span in spans]
    return json.dumps(records, sort_keys=True, default=str)


def _span_record(span: ReadableSpan) -> dict[str, Any]:
    scope = span.instrumentation_scope
    return {
        'name': span.name,
        'attributes': dict(span.attributes or {}),
        'events': [
            {'name': event.name, 'attributes': dict(event.attributes or {})}
            for event in span.events
        ],
        'status': {
            'code': span.status.status_code.name,
            'description': span.status.description,
        },
        'resource': dict(span.resource.attributes),
        'scope': None if scope is None else {'name': scope.name, 'version': scope.version},
    }


def _span_attribute_keys(spans: Iterable[ReadableSpan]) -> set[str]:
    keys: set[str] = set()
    for span in spans:
        keys.update(cast('Mapping[str, object]', span.attributes or {}).keys())
        for event in span.events:
            keys.update(cast('Mapping[str, object]', event.attributes or {}).keys())
    return keys
