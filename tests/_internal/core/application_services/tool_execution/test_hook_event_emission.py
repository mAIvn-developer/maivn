# pyright: strict
"""End-to-end tests for ``hook_fired`` event emission.

Covers both scope hooks (``run_scope_hooks`` in :mod:`agent.hooks`) and tool
hooks (``ToolExecutionService._run_execution_hooks``). The dispatcher-coverage
test for the new event lives in
``tests/application/test_event_forwarding_dispatcher.py``.

Also exercises the S10b NEW-IN-1 compat-guard path: `_FakeTool` is intentionally
missing the `metadata` attribute, so the executor's `getattr(tool, "metadata", None)`
guard must keep the call path alive.
"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import cast
from unittest.mock import MagicMock

from pydantic import JsonValue

from maivn._internal.api.agent.hooks import ScopeHook, ScopeHookEntry, run_scope_hooks
from maivn._internal.core.application_services.tool_execution.execution_strategy import (
    StrategyRegistry,
)
from maivn._internal.core.application_services.tool_execution.tool_execution_service import (
    ToolExecutionService,
)
from maivn._internal.core.entities.execution_context import ExecutionContext
from maivn._internal.utils.reporting.terminal_reporter import BaseReporter

JsonObject = dict[str, JsonValue]
HookPayload = dict[str, object]

# MARK: Scope Hooks


def _make_recording_reporter(
    calls: list[dict[str, object]],
) -> BaseReporter:
    """Build a `BaseReporter`-typed mock that records `report_hook_fired` kwargs."""
    reporter = MagicMock(spec=BaseReporter)

    def _record(**kwargs: object) -> None:
        calls.append(dict(kwargs))

    report_mock = cast(MagicMock, reporter.report_hook_fired)
    report_mock.side_effect = _record
    return cast(BaseReporter, reporter)


def test_run_scope_hooks_emits_hook_fired_on_success() -> None:
    """A successful scope hook emits a ``completed`` event via the reporter."""
    calls: list[dict[str, object]] = []

    def hook(payload: HookPayload) -> None:
        _ = payload

    hook.__name__ = "my_before_hook"

    reporter = _make_recording_reporter(calls)
    entries: list[ScopeHookEntry] = [(cast(ScopeHook, hook), "agent", "agent-123", "MyAgent")]

    run_scope_hooks(
        entries,
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
    calls: list[dict[str, object]] = []

    def hook(payload: HookPayload) -> None:
        _ = payload
        raise RuntimeError("hook went sideways")

    hook.__name__ = "buggy_hook"

    reporter = _make_recording_reporter(calls)
    entries: list[ScopeHookEntry] = [(cast(ScopeHook, hook), "swarm", "swarm-x", "MySwarm")]

    run_scope_hooks(
        entries,
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

    def hook(payload: HookPayload) -> None:
        nonlocal ran
        _ = payload
        ran = True

    entries: list[ScopeHookEntry] = [(cast(ScopeHook, hook), "agent", None, None)]

    run_scope_hooks(
        entries,
        payload={"stage": "before"},
        stage="before",
        reporter=None,
    )

    assert ran is True


def test_run_scope_hooks_swallows_reporter_exceptions() -> None:
    """A misbehaving reporter must never crash the hook execution path."""
    ran = False

    def hook(payload: HookPayload) -> None:
        nonlocal ran
        _ = payload
        ran = True

    reporter_mock = MagicMock(spec=BaseReporter)
    report_mock = cast(MagicMock, reporter_mock.report_hook_fired)
    report_mock.side_effect = RuntimeError("reporter exploded")
    reporter = cast(BaseReporter, reporter_mock)

    entries: list[ScopeHookEntry] = [(cast(ScopeHook, hook), "agent", "id", "name")]

    run_scope_hooks(
        entries,
        payload={"stage": "before"},
        stage="before",
        reporter=reporter,
    )

    assert ran is True  # Hook still ran despite reporter exception


# MARK: Tool Hooks


class _FakeTool:
    """Minimal tool stand-in intentionally missing `metadata` (S10b NEW-IN-1 guard)."""

    tool_id: str = "tool-abc"
    name: str = "MyTool"
    tool_type: str = "func"
    before_execute: staticmethod[[HookPayload], None]
    after_execute: staticmethod[[HookPayload], None] | None
    dependencies: object = None

    def __init__(
        self,
        before: Callable[[HookPayload], None],
        after: Callable[[HookPayload], None] | None,
    ) -> None:
        self.before_execute = staticmethod(before)
        self.after_execute = staticmethod(after) if after is not None else None


def test_tool_execution_service_emits_hook_fired_for_tool_hook(
    tmp_path: Path,
) -> None:
    """A tool's ``before_execute`` callback emits ``hook_fired`` with tool target."""
    _ = tmp_path

    calls: list[dict[str, object]] = []
    reporter = _make_recording_reporter(calls)

    service = ToolExecutionService(reporter_supplier=lambda: reporter)

    def before_hook(payload: HookPayload) -> None:
        _ = payload

    before_hook.__name__ = "my_tool_before"

    def after_hook(payload: HookPayload) -> None:
        _ = payload

    after_hook.__name__ = "my_tool_after"

    fake_tool = _FakeTool(before=before_hook, after=after_hook)

    # Patch the service's lookups so we don't need a full strategy chain. The
    # `cast` boundary lets us swap in test stubs without invoking type errors;
    # `setattr` is the policy-approved alternative to direct method assignment.
    def _resolve(_tool_id: str) -> _FakeTool:
        return fake_tool

    def _empty(*_a: object, **_k: object) -> JsonObject:
        return {}

    # `setattr` is the policy-approved alternative for attaching test stubs
    # to attributes the strict checker treats as readonly / class-private.
    setattr(service, "resolve_tool", _resolve)  # noqa: B010
    setattr(service, "_validate_arguments", _empty)  # noqa: B010
    setattr(service, "_resolve_dependencies", _empty)  # noqa: B010
    setattr(service, "_filter_arguments", _empty)  # noqa: B010
    strategy_registry = MagicMock(spec=StrategyRegistry)
    execute_mock = cast(MagicMock, strategy_registry.execute)
    execute_mock.return_value = {"ok": True}
    setattr(service, "_strategy_registry", strategy_registry)  # noqa: B010

    _ = service.execute_tool_call(
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

    calls: list[dict[str, object]] = []
    reporter = _make_recording_reporter(calls)

    service = ToolExecutionService(reporter_supplier=lambda: reporter)

    def hook(payload: HookPayload) -> None:
        _ = payload

    hook.__name__ = "anonymous"

    class _StaticFakeTool:
        tool_id: str = "tool-static"
        name: str = "MyTool"
        tool_type: str = "func"
        before_execute: staticmethod[[HookPayload], None] = staticmethod(hook)
        after_execute: object = None
        dependencies: object = None

    def _resolve(_tool_id: str) -> _StaticFakeTool:
        return _StaticFakeTool()

    def _empty(*_a: object, **_k: object) -> JsonObject:
        return {}

    setattr(service, "resolve_tool", _resolve)  # noqa: B010
    setattr(service, "_validate_arguments", _empty)  # noqa: B010
    setattr(service, "_resolve_dependencies", _empty)  # noqa: B010
    setattr(service, "_filter_arguments", _empty)  # noqa: B010
    strategy_registry = MagicMock(spec=StrategyRegistry)
    execute_mock = cast(MagicMock, strategy_registry.execute)
    execute_mock.return_value = None
    setattr(service, "_strategy_registry", strategy_registry)  # noqa: B010

    _ = service.execute_tool_call("tool-static", {}, context=ExecutionContext(scope=None))

    before_call = next(c for c in calls if c["stage"] == "before")
    assert before_call["target_id"] == "tool-static"  # falls back


# MARK: Forwarder


def test_forwarder_routes_hook_fired_to_reporter_method() -> None:
    """``forward_to_reporter`` dispatches ``hook_fired`` to ``reporter.report_hook_fired``."""
    from maivn import AppEvent, NormalizedEventForwardingState
    from maivn.events._forwarding.reporter import forward_to_reporter

    reporter = MagicMock(spec=BaseReporter)
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

    report_hook_fired_mock = cast(MagicMock, reporter.report_hook_fired)
    report_hook_fired_mock.assert_called_once()
    call_kwargs = cast(dict[str, object], dict(report_hook_fired_mock.call_args.kwargs))
    assert call_kwargs["name"] == "my_hook"
    assert call_kwargs["stage"] == "before"
    assert call_kwargs["target_type"] == "tool"
    assert call_kwargs["target_id"] == "evt-1"
