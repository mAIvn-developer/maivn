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

    def __init__(self) -> None:
        """Initialize call counters."""
        self.stream_calls = 0

    def invoke(self, _messages: object, **_kwargs: object) -> InvokeResponse:
        """Return a direct response without opening an event stream."""
        return _response()

    def stream(self, _messages: object, **_kwargs: object) -> Iterator[StreamEvent]:
        """Return the terminal event required by verbose reporting."""
        self.stream_calls += 1
        yield _final_event()


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


@pytest.mark.parametrize('scope_type', [scope.Agent, scope.Swarm])
def test_core_scope_can_recompile_after_hook_mode_changes(
    scope_type: type[scope.Agent | scope.Swarm],
) -> None:
    """Slotted scope classes support the hook cache used by every invocation."""
    instance = scope_type(name='core-scope', client=cast('Client', _FakeClient()))
    assert instance.compile_tools() == []
    instance.hook_execution_mode = 'scope'
    assert instance.compile_tools() == []
