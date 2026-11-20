"""Tests for verbose reporter plumbing on scope invocations."""

from __future__ import annotations

from typing import TYPE_CHECKING, cast

import pytest
from maivn_contracts.messages import Message

from maivn._internal import scope
from maivn._internal.models import InvokeResponse, StreamEvent
from maivn._internal.scope import agent as agent_module

if TYPE_CHECKING:
    from collections.abc import Iterator

    from maivn._internal.client import Client
    from maivn._internal.models import JsonObject


def _response() -> InvokeResponse:
    """Build the terminal response returned by the fake client."""
    return InvokeResponse(
        final_message=Message(
            message_id='msg-1',
            role='assistant',
            content='Done.',
            ts='2026-07-12T00:00:00Z',
        ),
        session_id='ses-1',
        root_event_id='evt-1',
        event_positions=[1],
        usage={'input_tokens': 1_000, 'output_tokens': 234},
        response='Done.',
    )


def _final_event() -> StreamEvent:
    """Build a canonical final event for verbose stream consumption."""
    return StreamEvent(
        position=1,
        event_type='final',
        data={
            'event_id': 'evt-1',
            'session_id': 'ses-1',
            'payload': {
                'message': {
                    'message_id': 'msg-1',
                    'role': 'assistant',
                    'content': 'Done.',
                    'ts': '2026-07-12T00:00:00Z',
                },
                'usage': {'input_tokens': 1_000, 'output_tokens': 234},
            },
        },
    )


class _FakeClient:
    """In-memory client that records which scope invocation path was used."""

    stream_calls: int

    def __init__(self, events: list[StreamEvent] | None = None) -> None:
        """Initialize call counters and the events to stream."""
        self.stream_calls = 0
        self._events = events if events is not None else [_final_event()]

    def invoke(self, _messages: object, **_kwargs: object) -> InvokeResponse:
        """Return a direct response without opening an event stream."""
        return _response()

    def stream(self, _messages: object, **_kwargs: object) -> Iterator[StreamEvent]:
        """Return the requested invocation events."""
        self.stream_calls += 1
        yield from self._events


def test_non_verbose_invoke_does_not_construct_a_reporter(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The default invocation hot path remains reporter-free."""
    client = _FakeClient()

    def _unexpected_reporter() -> object:
        message = 'reporter construction is forbidden for verbose=False'
        raise AssertionError(message)

    # Agent resolves create_reporter in its own module, so the patch target is
    # scope.agent rather than the scope facade.
    monkeypatch.setattr(agent_module, 'create_reporter', _unexpected_reporter)
    agent = scope.Agent(name='test', client=cast('Client', client))

    response = agent.invoke('hello')

    assert response.response == 'Done.'
    assert client.stream_calls == 0


def test_verbose_invoke_reports_terminal_token_summary(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Verbose invoke consumes events and exposes the frozen benchmark token line."""
    client = _FakeClient()
    agent = scope.Agent(name='test', client=cast('Client', client))

    response = agent.invoke('hello', verbose=True)

    assert response.response == 'Done.'
    assert client.stream_calls == 1
    assert 'Total Tokens: 1,234' in capsys.readouterr().out


@pytest.mark.parametrize('terminal_tier', ['plain', 'rich'])
def test_verbose_stream_keeps_provider_attempt_telemetry_in_raw_events(
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
    terminal_tier: str,
) -> None:
    """The terminal shows the run while the iterator retains per-attempt telemetry."""
    monkeypatch.setenv('MAIVN_TERMINAL_TIER', terminal_tier)
    warning = 'Provider spend coverage is partial; some usage has no known cost.'
    model = 'synthetic-model-id'
    attempt_id = 'attempt-weather-1'
    routing_facts: JsonObject = {'provider': 'openai', 'model': model, 'attempt_id': attempt_id}
    payloads: tuple[tuple[str, JsonObject], ...] = (
        ('model_routing', {'mode': 'auto', 'model': model}),
        ('model_routing', {**routing_facts, 'mode': 'hop_timing', 'elapsed_ms': 417}),
        ('model_routing', {**routing_facts, 'mode': 'hop_usage', 'output_tokens': 39}),
        ('system_tool_start', {'tool_name': 'get_weather', 'id': 'weather-1'}),
        ('system_tool_complete', {'tool_name': 'get_weather', 'id': 'weather-1'}),
        ('enrichment', {'phase': 'plan_warning', 'message': warning}),
    )
    events = [
        StreamEvent(position=index, event_type=name, data={'payload': payload})
        for index, (name, payload) in enumerate(payloads, start=1)
    ]
    events.append(_final_event().model_copy(update={'position': len(events) + 1}))
    client = _FakeClient(events)
    agent = scope.Agent(name='weather', client=cast('Client', client))
    monkeypatch.setattr(agent, '_registered_resource_ids', ())

    received = list(agent.stream('What is the weather?', verbose=True))

    assert received[1].data['payload'] == {
        **routing_facts,
        'mode': 'hop_timing',
        'elapsed_ms': 417,
    }
    assert received[2].data['payload'] == {
        **routing_facts,
        'mode': 'hop_usage',
        'output_tokens': 39,
    }
    output = capsys.readouterr().out
    assert f'auto -> {model}' in output
    assert 'get_weather' in output
    assert 'Done.' in output
    assert warning in output
    assert 'Provider attempt timing' not in output
    assert 'Provider usage reported' not in output
    assert attempt_id not in output


@pytest.mark.parametrize('scope_type', [scope.Agent, scope.Swarm])
def test_core_scope_can_recompile_after_hook_mode_changes(
    scope_type: type[scope.Agent | scope.Swarm],
) -> None:
    """Slotted scope classes support the hook cache used by every invocation."""
    instance = scope_type(name='core-scope', client=cast('Client', _FakeClient()))
    assert instance.compile_tools() == []
    instance.hook_execution_mode = 'scope'
    assert instance.compile_tools() == []
