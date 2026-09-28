"""Retained dependency results stay scoped and never override fresh execution."""

from __future__ import annotations

import asyncio
from typing import Any

import pytest
from maivn_contracts.tools import ErrorToolOutcome, OkToolOutcome

from maivn import depends_on_tool
from maivn._internal.models import StreamEvent, ToolMetadata
from maivn._internal.tool_runtime import LocalToolRuntime


def _event(name: str, retained: dict[str, Any] | None = None) -> StreamEvent:
    return StreamEvent(
        position=1,
        event_type='system_tool_start',
        data={
            'payload': {
                'tool_call': {
                    'call_id': f'new-{name}',
                    'spec_ref': {'tool_id': name, 'namespace': 'sdk', 'version': 'v1'},
                    'arguments': {},
                    'lineage': {'session_id': 'new-session', 'invocation_id': 'new-invocation'},
                    'retained_dependencies': retained,
                }
            }
        },
    )


def _binding(value: object = 'Taylor') -> dict[str, Any]:
    return {
        'name': {
            'thread_id': 'thread-A',
            'source_session_id': 'prior-session',
            'source_call_id': 'prior-call',
            'producer': {'namespace': 'sdk', 'tool_id': 'collect_name', 'version': 'v1'},
            'result': value,
        }
    }


@pytest.mark.parametrize('fresh', [False, True])
def test_retained_dependency_fills_only_an_unexecuted_producer(*, fresh: bool) -> None:
    """A fresh None result is authoritative too; no truthiness fallback to old data."""

    def collect_name() -> None:
        return None

    @depends_on_tool('collect_name', 'name', result_scope='conversation')
    def summary(name: object) -> object:
        return name

    runtime = LocalToolRuntime(
        [
            ToolMetadata(name='collect_name', target=collect_name),
            ToolMetadata(name='summary', target=summary),
        ],
        private_data=None,
        thread_id='thread-A',
    )
    if fresh:
        assert isinstance(
            asyncio.run(runtime.outcome_for_event(_event('collect_name'))), OkToolOutcome
        )
    result = asyncio.run(runtime.outcome_for_event(_event('summary', _binding())))
    assert isinstance(result, OkToolOutcome)
    assert result.result == (None if fresh else 'Taylor')


@pytest.mark.parametrize(
    'invalid', ['thread', 'producer', 'version', 'session', 'argument', 'scope']
)
def test_invalid_retained_dependency_cannot_execute_the_consumer(invalid: str) -> None:
    """A carried value needs the authored policy, producer identity, and owning thread."""
    called: list[object] = []

    @depends_on_tool(
        'collect_name', 'name', result_scope='invocation' if invalid == 'scope' else 'conversation'
    )
    def summary(name: object) -> object:
        called.append(name)
        return name

    retained = _binding()
    if invalid == 'thread':
        retained['name']['thread_id'] = 'thread-B'
    elif invalid == 'producer':
        retained['name']['producer']['tool_id'] = 'another_tool'
    elif invalid == 'version':
        retained['name']['producer']['version'] = 'old-version'
    elif invalid == 'session':
        retained['name']['source_session_id'] = 'new-session'
    elif invalid == 'argument':
        retained['unexpected'] = retained.pop('name')
    runtime = LocalToolRuntime(
        [ToolMetadata(name='summary', target=summary)],
        private_data=None,
        thread_id='thread-A',
    )
    result = asyncio.run(runtime.outcome_for_event(_event('summary', retained)))
    assert isinstance(result, ErrorToolOutcome)
    assert result.error.code == 'sdk_retained_dependency_invalid'
    assert called == []


def test_failed_fresh_producer_does_not_fall_back_to_stale_success() -> None:
    """An attempted refresh failure must remain a failure."""

    def collect_name() -> None:
        message = 'refresh failed'
        raise ValueError(message)

    @depends_on_tool('collect_name', 'name', result_scope='conversation')
    def summary(name: object) -> object:
        return name

    runtime = LocalToolRuntime(
        [
            ToolMetadata(name='collect_name', target=collect_name),
            ToolMetadata(name='summary', target=summary),
        ],
        private_data=None,
        thread_id='thread-A',
    )
    asyncio.run(runtime.outcome_for_event(_event('collect_name')))
    result = asyncio.run(runtime.outcome_for_event(_event('summary', _binding())))
    assert isinstance(result, ErrorToolOutcome)
    assert 'dependency failed' in result.error.message


@pytest.mark.parametrize('available', [False, True])
def test_retained_private_placeholder_requires_current_local_custody(*, available: bool) -> None:
    """The provider-side placeholder is resolved locally or refused, never used literally."""

    @depends_on_tool('collect_name', 'name', result_scope='conversation')
    def summary(name: object) -> object:
        return name

    runtime = LocalToolRuntime(
        [ToolMetadata(name='summary', target=summary)],
        private_data={'customer': 'PRIVATE-TEST'} if available else None,
        thread_id='thread-A',
    )
    result = asyncio.run(runtime.outcome_for_event(_event('summary', _binding('{_{customer}_}'))))
    if available:
        assert isinstance(result, OkToolOutcome)
        assert result.result == 'PRIVATE-TEST'
    else:
        assert isinstance(result, ErrorToolOutcome)
        assert result.error.code == 'sdk_retained_dependency_unavailable'
