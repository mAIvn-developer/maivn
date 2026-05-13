"""End-to-end tests for ``hook_fired`` event emission.

Covers both scope hooks (``run_scope_hooks`` in :mod:`agent.hooks`) and tool
hooks (``ToolExecutionService._run_execution_hooks``). The dispatcher-coverage
test for the new event lives in
``tests/application/test_event_forwarding_dispatcher.py``.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock

from maivn._internal.api.agent.hooks import run_scope_hooks

# MARK: Scope Hooks


def test_run_scope_hooks_emits_hook_fired_on_success() -> None:
    """A successful scope hook emits a ``completed`` event via the reporter."""
    calls: list[dict[str, Any]] = []

    def hook(payload: dict[str, Any]) -> None:
        _ = payload

    hook.__name__ = "my_before_hook"

    reporter = MagicMock()
    reporter.report_hook_fired.side_effect = lambda **kwargs: calls.append(kwargs)

    run_scope_hooks(
        [(hook, "agent", "agent-123", "MyAgent")],
        payload={"stage": "before"},
        stage="before",
        reporter=reporter,
    )

    assert len(calls) == 1
    event = calls[0]
    assert event["name"] == "my_before_hook"
    assert event["stage"] == "before"
    assert event["status"] == "completed"
    assert event["target_type"] == "agent"
    assert event["target_id"] == "agent-123"
    assert event["target_name"] == "MyAgent"
    assert event["error"] is None
    assert isinstance(event["elapsed_ms"], int)


def test_run_scope_hooks_emits_hook_fired_on_failure() -> None:
    """A raising scope hook emits a ``failed`` event with the error message."""
    calls: list[dict[str, Any]] = []

    def hook(payload: dict[str, Any]) -> None:
        _ = payload
        raise RuntimeError("hook went sideways")

    hook.__name__ = "buggy_hook"

    reporter = MagicMock()
    reporter.report_hook_fired.side_effect = lambda **kwargs: calls.append(kwargs)

    run_scope_hooks(
        [(hook, "swarm", "swarm-x", "MySwarm")],
        payload={"stage": "after"},
        stage="after",
        reporter=reporter,
    )

    assert len(calls) == 1
    event = calls[0]
    assert event["name"] == "buggy_hook"
    assert event["status"] == "failed"
    assert event["target_type"] == "swarm"
    assert event["error"] == "hook went sideways"


def test_run_scope_hooks_skips_emission_when_reporter_is_none() -> None:
    """Local hook execution still works when no reporter is wired."""
    ran = False

    def hook(payload: dict[str, Any]) -> None:
        nonlocal ran
        ran = True

    run_scope_hooks(
        [(hook, "agent", None, None)],
        payload={"stage": "before"},
        stage="before",
        reporter=None,
    )

    assert ran is True


def test_run_scope_hooks_swallows_reporter_exceptions() -> None:
    """A misbehaving reporter must never crash the hook execution path."""
    ran = False

    def hook(payload: dict[str, Any]) -> None:
        nonlocal ran
        ran = True

    reporter = MagicMock()
    reporter.report_hook_fired.side_effect = RuntimeError("reporter exploded")

    run_scope_hooks(
        [(hook, "agent", "id", "name")],
        payload={"stage": "before"},
        stage="before",
        reporter=reporter,
    )

    assert ran is True  # Hook still ran despite reporter exception


# MARK: Tool Hooks


def test_tool_execution_service_emits_hook_fired_for_tool_hook(
    tmp_path: Any,
) -> None:
    """A tool's ``before_execute`` callback emits ``hook_fired`` with tool target."""
    _ = tmp_path
    from maivn._internal.core.application_services.tool_execution.tool_execution_service import (
        ToolExecutionService,
    )
    from maivn._internal.core.entities.execution_context import ExecutionContext

    calls: list[dict[str, Any]] = []
    reporter = MagicMock()
    reporter.report_hook_fired.side_effect = lambda **kwargs: calls.append(kwargs)

    service = ToolExecutionService(reporter_supplier=lambda: reporter)

    def before_hook(payload: dict[str, Any]) -> None:
        _ = payload

    before_hook.__name__ = "my_tool_before"

    def after_hook(payload: dict[str, Any]) -> None:
        _ = payload

    after_hook.__name__ = "my_tool_after"

    class _FakeTool:
        tool_id = "tool-abc"
        name = "MyTool"
        tool_type = "func"
        before_execute = staticmethod(before_hook)
        after_execute = staticmethod(after_hook)
        dependencies = None

    fake_tool = _FakeTool()

    # Patch the service's lookups so we don't need a full strategy chain
    service.resolve_tool = lambda tool_id: fake_tool  # type: ignore[method-assign]
    service._validate_arguments = lambda *_a, **_k: {}  # type: ignore[method-assign]
    service._resolve_dependencies = lambda *_a, **_k: {}  # type: ignore[method-assign]
    service._filter_arguments = lambda *_a, **_k: {}  # type: ignore[method-assign]
    service._strategy_registry = MagicMock()  # type: ignore[assignment]
    service._strategy_registry.execute.return_value = {"ok": True}

    service.execute_tool_call(
        "tool-abc",
        {},
        context=ExecutionContext(scope=None),
        tool_event_id="event-xyz",
    )

    # before + after = 2 firings
    assert len(calls) == 2
    before_call = next(c for c in calls if c["stage"] == "before")
    after_call = next(c for c in calls if c["stage"] == "after")

    assert before_call["name"] == "my_tool_before"
    assert before_call["target_type"] == "tool"
    assert before_call["target_id"] == "event-xyz"
    assert before_call["target_name"] == "MyTool"
    assert before_call["status"] == "completed"

    assert after_call["name"] == "my_tool_after"
    assert after_call["target_type"] == "tool"
    assert after_call["target_id"] == "event-xyz"


def test_tool_hook_falls_back_to_tool_id_when_event_id_missing() -> None:
    """``target_id`` falls back to ``tool_id`` for nested / context-free calls."""
    from maivn._internal.core.application_services.tool_execution.tool_execution_service import (
        ToolExecutionService,
    )
    from maivn._internal.core.entities.execution_context import ExecutionContext

    calls: list[dict[str, Any]] = []
    reporter = MagicMock()
    reporter.report_hook_fired.side_effect = lambda **kwargs: calls.append(kwargs)

    service = ToolExecutionService(reporter_supplier=lambda: reporter)

    def hook(payload: dict[str, Any]) -> None:
        _ = payload

    hook.__name__ = "anonymous"

    class _FakeTool:
        tool_id = "tool-static"
        name = "MyTool"
        tool_type = "func"
        before_execute = staticmethod(hook)
        after_execute = None
        dependencies = None

    service.resolve_tool = lambda tool_id: _FakeTool()  # type: ignore[method-assign]
    service._validate_arguments = lambda *_a, **_k: {}  # type: ignore[method-assign]
    service._resolve_dependencies = lambda *_a, **_k: {}  # type: ignore[method-assign]
    service._filter_arguments = lambda *_a, **_k: {}  # type: ignore[method-assign]
    service._strategy_registry = MagicMock()  # type: ignore[assignment]
    service._strategy_registry.execute.return_value = None

    service.execute_tool_call("tool-static", {}, context=ExecutionContext(scope=None))

    before_call = next(c for c in calls if c["stage"] == "before")
    assert before_call["target_id"] == "tool-static"  # falls back


# MARK: Forwarder


def test_forwarder_routes_hook_fired_to_reporter_method() -> None:
    """``forward_to_reporter`` dispatches ``hook_fired`` to ``reporter.report_hook_fired``."""
    from maivn import AppEvent, NormalizedEventForwardingState
    from maivn.events._forwarding.reporter import forward_to_reporter

    reporter = MagicMock()
    state = NormalizedEventForwardingState()
    event = AppEvent.model_validate(
        {
            "contract_version": "v1",
            "event_name": "hook_fired",
            "event_kind": "hook",
            "name": "my_hook",
            "stage": "before",
            "status": "completed",
            "target_type": "tool",
            "target_id": "evt-1",
            "target_name": "MyTool",
            "error": None,
            "elapsed_ms": 3,
            "hook": {
                "name": "my_hook",
                "stage": "before",
                "status": "completed",
                "target_type": "tool",
                "target_id": "evt-1",
                "target_name": "MyTool",
            },
        }
    )

    forward_to_reporter(
        event,
        payload=event.model_dump(),
        reporter=reporter,
        state=state,
    )

    reporter.report_hook_fired.assert_called_once()
    kwargs = reporter.report_hook_fired.call_args.kwargs
    assert kwargs["name"] == "my_hook"
    assert kwargs["stage"] == "before"
    assert kwargs["target_type"] == "tool"
    assert kwargs["target_id"] == "evt-1"
