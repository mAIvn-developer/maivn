"""Scope-level execution hooks for Agent invocations."""

from __future__ import annotations

import logging
from collections.abc import Callable, Iterator
from typing import TYPE_CHECKING, Any

from maivn_shared import FINAL_EVENT_NAME, SessionResponse

from maivn._internal.core.entities.execution_context import ExecutionContext
from maivn._internal.core.entities.sse_event import SSEEvent

if TYPE_CHECKING:
    from .agent import _InvocationState

logger = logging.getLogger(__name__)


# MARK: Hook State


def scope_hooks_enabled(invocation_state: _InvocationState) -> bool:
    """Check if scope-level hooks should be fired."""
    return invocation_state.agent_mode == "scope" or invocation_state.swarm_mode == "scope"


def build_scope_hook_payload(
    agent: Any,
    invocation_state: _InvocationState,
) -> dict[str, Any]:
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


def get_before_scope_hooks(
    agent: Any,
    invocation_state: _InvocationState,
) -> list[Callable[..., Any] | None]:
    """Collect before-execute hooks from swarm and agent."""
    return [
        getattr(invocation_state.swarm, "before_execute", None)
        if invocation_state.swarm_mode == "scope"
        else None,
        getattr(agent, "before_execute", None) if invocation_state.agent_mode == "scope" else None,
    ]


def get_after_scope_hooks(
    agent: Any,
    invocation_state: _InvocationState,
) -> list[Callable[..., Any] | None]:
    """Collect after-execute hooks from agent and swarm."""
    return [
        getattr(agent, "after_execute", None) if invocation_state.agent_mode == "scope" else None,
        getattr(invocation_state.swarm, "after_execute", None)
        if invocation_state.swarm_mode == "scope"
        else None,
    ]


# MARK: Hook Execution


def run_scope_hooks(
    hooks: list[Callable[..., Any] | None],
    payload: dict[str, Any],
    *,
    stage: str,
) -> None:
    """Run a list of hooks, logging failures without re-raising."""
    for hook in hooks:
        if hook is None:
            continue
        try:
            hook(payload)
        except Exception:  # noqa: BLE001
            logger.exception(f"[AGENT] Scope {stage}_execute hook failed")


# MARK: Stream Wrapper


def wrap_stream_with_hooks(
    stream_iter: Iterator[SSEEvent],
    agent: Any,
    invocation_state: _InvocationState,
    payload: dict[str, Any],
) -> Iterator[SSEEvent]:
    """Wrap a stream iterator with before/after scope hooks."""
    run_scope_hooks(
        get_before_scope_hooks(agent, invocation_state),
        payload,
        stage="before",
    )

    def _stream_with_scope_hooks() -> Iterator[SSEEvent]:
        final_response: SessionResponse | None = None
        stream_error: Exception | None = None
        try:
            for event in stream_iter:
                if event.name == FINAL_EVENT_NAME and isinstance(event.payload, dict):
                    try:
                        final_response = SessionResponse.model_validate(event.payload)
                    except Exception:  # noqa: BLE001
                        final_response = None
                yield event
        except Exception as exc:  # noqa: BLE001
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
            )

    return _stream_with_scope_hooks()


__all__ = [
    "build_scope_hook_payload",
    "get_after_scope_hooks",
    "get_before_scope_hooks",
    "run_scope_hooks",
    "scope_hooks_enabled",
    "wrap_stream_with_hooks",
]
