"""Privileged labels are enforced from the original callable on every attempt."""

# pyright: reportPrivateUsage=false
# Reuse the invocation suite's event and wire builders.
import asyncio

import pytest

from maivn._internal.models import ToolMetadata
from maivn._internal.tool_runtime import LocalToolRuntime
from maivn._internal.wire import _function_tool_spec
from tests.unit._internal.test_runtime_tool_runtime_characterization import _start


@pytest.mark.parametrize(
    'metadata',
    [
        {'destructive': True},
        {'permissions': ['delete']},
        {'permissions': ['admin']},
        {'permissions': ['impersonate']},
        {'permissions': ['import']},
    ],
)
def test_privileged_calls_never_reach_target_or_hooks(metadata: dict[str, object]) -> None:
    """Verify privileged calls never reach target or hooks."""
    calls: list[str] = []

    def before(_payload: object) -> None:
        calls.append('hook')

    tool = ToolMetadata(
        name='danger',
        target=lambda: calls.append('target'),
        metadata=metadata,
        before_execute=before,
    )
    runtime = LocalToolRuntime([tool], private_data=None)
    for attempt in range(2):
        outcome = asyncio.run(
            runtime.outcome_for_event(_start('danger', call_id=f'call-{attempt}'))
        )
        assert outcome is not None
        assert outcome.status == 'error'
        assert outcome.error.code == 'tool_authorization_required'
    assert calls == []


@pytest.mark.parametrize(
    'metadata',
    [{}, {'permissions': ['read']}, {'permissions': ['write']}, {'permissions': ['export']}],
)
def test_explicitly_registered_ordinary_tools_remain_authorized(
    metadata: dict[str, object],
) -> None:
    """Verify explicitly registered ordinary tools remain authorized."""
    calls: list[str] = []
    tool = ToolMetadata(name='ordinary', target=lambda: calls.append('target'), metadata=metadata)
    outcome = asyncio.run(
        LocalToolRuntime([tool], private_data=None).outcome_for_event(_start('ordinary'))
    )
    assert outcome is not None
    assert outcome.status == 'ok'
    assert calls == ['target']


def test_original_labels_cross_the_wire() -> None:
    """Verify original labels cross the wire."""
    tool = ToolMetadata(
        name='danger',
        target=lambda: None,
        metadata={'permissions': ['delete'], 'destructive': True},
    )
    payload = _function_tool_spec(tool)
    assert payload['permissions'] == ['delete']
    assert payload['destructive'] is True


@pytest.mark.parametrize(
    'metadata', [{'permissions': ['billing']}, {'destructive': 'false'}, {'permissions': 'read'}]
)
def test_historical_freeform_labels_become_denied_tools_without_crashing_run(
    metadata: dict[str, object],
) -> None:
    """Unknown legacy labels deny the tool consistently at wire and local execution boundaries."""
    calls: list[str] = []
    tool = ToolMetadata(name='legacy', target=lambda: calls.append('target'), metadata=metadata)
    payload = _function_tool_spec(tool)
    assert payload['destructive'] is True
    result = asyncio.run(
        LocalToolRuntime([tool], private_data=None).outcome_for_event(_start('legacy'))
    )
    assert result is not None
    assert result.status == 'error'
    assert result.error.code == 'tool_authorization_required'
    assert calls == []
