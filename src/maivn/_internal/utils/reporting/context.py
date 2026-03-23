"""Context variables for reporter propagation.

This module provides context variables that allow nested agent invocations
to inherit the parent session's reporter, ensuring system tool events from
nested sessions are displayed in the terminal.
"""

from __future__ import annotations

from contextvars import ContextVar
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from maivn._internal.utils.reporting.terminal_reporter import BaseReporter

# MARK: - Context Variables

current_reporter: ContextVar[BaseReporter | None] = ContextVar(
    "current_reporter",
    default=None,
)
"""Context variable holding the current terminal reporter.

When set, nested agent invocations will use this reporter to display
system tool events, ensuring visibility into nested session activity.
"""

inside_orchestrator: ContextVar[bool] = ContextVar(
    "inside_orchestrator",
    default=False,
)
"""Context variable indicating whether execution is inside an AgentOrchestrator.

Used to distinguish between a reporter set by external code (e.g. Studio)
vs one inherited from a parent orchestrator.  Only when this is True does
a new orchestrator treat itself as nested.
"""

allow_nested_response_stream: ContextVar[bool] = ContextVar(
    "allow_nested_response_stream",
    default=False,
)
"""Allow a nested orchestrator to forward assistant streaming chunks.

Used by swarm dynamic agent tools when the target agent is marked
``use_as_final_output=True`` so its synthesized response can stream to the
shared reporter like a normal assistant response.
"""

current_sdk_delivery_mode: ContextVar[str] = ContextVar(
    "current_sdk_delivery_mode",
    default="invoke",
)
"""Track whether the active SDK execution is ``invoke`` or ``stream``.

Nested agent invocations inherit this value so internal live streaming can be
suppressed for ``invoke`` while remaining enabled for ``stream``.
"""


# MARK: - Public API


def get_current_reporter() -> BaseReporter | None:
    """Get the current reporter from context.

    Returns:
        The current reporter if set, None otherwise.
    """
    return current_reporter.get()


def set_current_reporter(reporter: BaseReporter | None) -> None:
    """Set the current reporter in context.

    Args:
        reporter: The reporter to set, or None to clear.
    """
    current_reporter.set(reporter)


__all__ = [
    "allow_nested_response_stream",
    "current_sdk_delivery_mode",
    "current_reporter",
    "get_current_reporter",
    "inside_orchestrator",
    "set_current_reporter",
]
