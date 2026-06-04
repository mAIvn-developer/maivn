"""Scope-level execution hooks for Agent invocations."""

# pyright: strict
from __future__ import annotations

import logging
import time
from collections.abc import Callable, Iterator
from typing import TYPE_CHECKING, Protocol, TypeAlias, cast

from maivn_shared import FINAL_EVENT_NAME, SessionResponse

from maivn._internal.core.entities.execution_context import ExecutionContext
from maivn._internal.core.entities.sse_event import SSEEvent

if TYPE_CHECKING:
    from maivn._internal.utils.reporting.terminal_reporter import BaseReporter

    from .invocation_state import InvocationState

logger = logging.getLogger(__name__)


# MARK: Hook State

HookPayload: TypeAlias = dict[str, object]
ScopeHook: TypeAlias = Callable[[HookPayload], object]


class _ScopeHookTarget(Protocol):
    id: str
    name: str | None
    before_execute: ScopeHook | None
    after_execute: ScopeHook | None


def scope_hooks_enabled(invocation_state: InvocationState) -> bool:
    """Check if scope-level hooks should be fired."""
    return invocation_state.agent_mode == "scope" or invocation_state.swarm_mode == "scope"


def build_scope_hook_payload(
    agent: object,
    invocation_state: InvocationState,
) -> HookPayload:
    """Build the initial payload dict for scope hooks."""
    context = ExecutionContext(
        scope=agent,
        messages=invocation_state.prepared_messages,
        metadata=invocation_state.merged_metadata or None,
        memory_config=invocation_state.resolved_memory_config,
        system_tools_config=invocation_state.resolved_system_tools_config,
        orchestration_config=invocation_state.resolved_orchestration_config,
        memory_assets_config=invocation_state.resolved_memory_assets_config,
        swarm_config=invocation_state.resolved_swarm_config,
    )
    return {
        "stage": "before",
        "tool_id": None,
        "tool": None,
        "args": None,
        "context": context,
        "result": None,
        "error": None,
    }


# MARK: Hook Resolution

# Each entry is ``(callable, target_type, target_id, target_name)`` so the
# emitted ``hook_fired`` event can be routed to the right scope card.
ScopeHookEntry: TypeAlias = tuple[ScopeHook | None, str, str | None, str | None]


def _scope_target(swarm: object) -> tuple[str | None, str | None]:
    """Resolve ``(target_id, target_name)`` for a swarm scope hook."""
    swarm_name = getattr(swarm, "name", None) or (
        swarm.__class__.__name__ if swarm is not None else None
    )
    swarm_id = getattr(swarm, "id", None)
    return (
        swarm_id if isinstance(swarm_id, str) else None,
        swarm_name if isinstance(swarm_name, str) else None,
    )


def _agent_target(agent: object) -> tuple[str | None, str | None]:
    """Resolve ``(target_id, target_name)`` for an agent scope hook."""
    agent_id = getattr(agent, "id", None)
    agent_name = getattr(agent, "name", None)
    return (
        agent_id if isinstance(agent_id, str) else None,
        agent_name if isinstance(agent_name, str) else None,
    )


def get_before_scope_hooks(
    agent: object,
    invocation_state: InvocationState,
) -> list[ScopeHookEntry]:
    """Collect before-execute hooks from swarm and agent.

    Returns a list of ``(callable, target_type, target_id, target_name)``
    tuples — the extra metadata lets :func:`run_scope_hooks` emit a
    ``hook_fired`` event routed to the correct scope card.
    """
    entries: list[ScopeHookEntry] = []
    swarm = invocation_state.swarm
    if invocation_state.swarm_mode == "scope" and swarm is not None:
        swarm_id, swarm_name = _scope_target(swarm)
        swarm_target = cast(_ScopeHookTarget, swarm)
        entries.append((swarm_target.before_execute, "swarm", swarm_id, swarm_name))
    if invocation_state.agent_mode == "scope":
        agent_id, agent_name = _agent_target(agent)
        agent_target = cast(_ScopeHookTarget, agent)
        entries.append((agent_target.before_execute, "agent", agent_id, agent_name))
    return entries


def get_after_scope_hooks(
    agent: object,
    invocation_state: InvocationState,
) -> list[ScopeHookEntry]:
    """Collect after-execute hooks from agent and swarm."""
    entries: list[ScopeHookEntry] = []
    if invocation_state.agent_mode == "scope":
        agent_id, agent_name = _agent_target(agent)
        agent_target = cast(_ScopeHookTarget, agent)
        entries.append((agent_target.after_execute, "agent", agent_id, agent_name))
    swarm = invocation_state.swarm
    if invocation_state.swarm_mode == "scope" and swarm is not None:
        swarm_id, swarm_name = _scope_target(swarm)
        swarm_target = cast(_ScopeHookTarget, swarm)
        entries.append((swarm_target.after_execute, "swarm", swarm_id, swarm_name))
    return entries


# MARK: Hook Execution


def run_scope_hooks(
    hooks: list[ScopeHookEntry],
    payload: HookPayload,
    *,
    stage: str,
    reporter: BaseReporter | None = None,
) -> None:
    """Run a list of hooks, logging failures without re-raising.

    When ``reporter`` is provided each hook firing emits a ``hook_fired``
    event via :meth:`BaseReporter.report_hook_fired` so the appropriate
    scope card can render a header/footer marker.
    """
    for hook, target_type, target_id, target_name in hooks:
        if hook is None:
            continue
        hook_name = _hook_name(hook)
        started_at = time.monotonic()
        status = "completed"
        error_message: str | None = None
        try:
            _ = hook(payload)
        except Exception as exc:  # noqa: BLE001 - hook failures must never abort execution
            status = "failed"
            error_message = str(exc) or exc.__class__.__name__
            logger.exception(f"[AGENT] Scope {stage}_execute hook failed")
        finally:
            if reporter is not None:
                elapsed_ms = int((time.monotonic() - started_at) * 1000)
                # Scope-level firings define the hook at the same level that
                # owns the on-screen card, so ``source`` mirrors target_type
                # (with ``"agent"`` re-spelled ``"scope"`` to match the
                # tool-execution path's vocabulary).
                source = "swarm" if target_type == "swarm" else "scope"
                _emit_hook_fired(
                    reporter,
                    name=hook_name,
                    stage=stage,
                    status=status,
                    source=source,
                    target_type=target_type,
                    target_id=target_id,
                    target_name=target_name,
                    error=error_message,
                    elapsed_ms=elapsed_ms,
                )


def _hook_name(hook: ScopeHook) -> str:
    """Best-effort display name for a hook callable."""
    name = getattr(hook, "__name__", None)
    if isinstance(name, str) and name:
        return name
    return hook.__class__.__name__


def _emit_hook_fired(
    reporter: BaseReporter,
    *,
    name: str,
    stage: str,
    status: str,
    source: str,
    target_type: str,
    target_id: str | None,
    target_name: str | None,
    error: str | None,
    elapsed_ms: int,
) -> None:
    """Call ``reporter.report_hook_fired`` defensively (never let the SDK crash on it)."""
    try:
        reporter.report_hook_fired(
            name=name,
            stage=stage,
            status=status,
            source=source,
            target_type=target_type,
            target_id=target_id,
            target_name=target_name,
            error=error,
            elapsed_ms=elapsed_ms,
        )
    except TypeError:
        # Older reporters predate the ``source`` kwarg.
        try:
            reporter.report_hook_fired(
                name=name,
                stage=stage,
                status=status,
                target_type=target_type,
                target_id=target_id,
                target_name=target_name,
                error=error,
                elapsed_ms=elapsed_ms,
            )
        except Exception:  # noqa: BLE001 - emission must never disrupt execution
            logger.exception("[AGENT] Reporter.report_hook_fired raised")
    except Exception:  # noqa: BLE001 - emission must never disrupt execution
        logger.exception("[AGENT] Reporter.report_hook_fired raised")


# MARK: Stream Wrapper


def wrap_stream_with_hooks(
    stream_iter: Iterator[SSEEvent],
    agent: object,
    invocation_state: InvocationState,
    payload: HookPayload,
    *,
    reporter: BaseReporter | None = None,
) -> Iterator[SSEEvent]:
    """Wrap a stream iterator with before/after scope hooks."""
    run_scope_hooks(
        get_before_scope_hooks(agent, invocation_state),
        payload,
        stage="before",
        reporter=reporter,
    )

    def _stream_with_scope_hooks() -> Iterator[SSEEvent]:
        final_response: SessionResponse | None = None
        stream_error: Exception | None = None
        try:
            for event in stream_iter:
                if event.name == FINAL_EVENT_NAME and isinstance(event.payload, dict):
                    try:
                        final_response = SessionResponse.model_validate(event.payload)
                    except Exception:  # noqa: BLE001 - malformed final event falls back to None
                        final_response = None
                yield event
        except Exception as exc:  # noqa: BLE001 - stream errors must still fire after hooks
            stream_error = exc
            raise
        finally:
            payload["stage"] = "after"
            payload["error"] = stream_error
            if stream_error is None:
                payload["result"] = final_response
            run_scope_hooks(
                get_after_scope_hooks(agent, invocation_state),
                payload,
                stage="after",
                reporter=reporter,
            )

    return _stream_with_scope_hooks()


__all__ = [
    "ScopeHookEntry",
    "build_scope_hook_payload",
    "get_after_scope_hooks",
    "get_before_scope_hooks",
    "run_scope_hooks",
    "scope_hooks_enabled",
    "wrap_stream_with_hooks",
]
