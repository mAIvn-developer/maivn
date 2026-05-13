"""Forwarders for agent assignment and enrichment events."""

from __future__ import annotations

import inspect
from typing import Any

from ..._models import AppEvent
from ..payload import coerce_mapping, normalized_text
from ..state import NormalizedEventForwardingState


def forward_agent_assignment(
    event: AppEvent,
    *,
    payload: dict[str, Any],
    reporter: Any,
    state: NormalizedEventForwardingState,
) -> None:
    _ = state
    report_agent_assignment = getattr(reporter, "report_agent_assignment", None)
    if not callable(report_agent_assignment):
        return

    assignment_id = normalized_text(payload.get("assignment_id")) or normalized_text(
        getattr(event.assignment, "id", None)
    )
    agent_name = normalized_text(payload.get("agent_name")) or normalized_text(
        getattr(event.assignment, "agent_name", None)
    )
    status = normalized_text(payload.get("status")) or normalized_text(
        getattr(event.assignment, "status", None)
    )
    swarm_name = normalized_text(payload.get("swarm_name")) or normalized_text(
        getattr(event.assignment, "swarm_name", None)
    )
    error = normalized_text(payload.get("error")) or normalized_text(
        getattr(event.assignment, "error", None)
    )
    result = payload.get("result", getattr(event.assignment, "result", None))

    if not agent_name or not status:
        return

    report_agent_assignment(
        agent_name=agent_name,
        status=status,
        assignment_id=assignment_id or f"agent:{agent_name}",
        swarm_name=swarm_name,
        error=error,
        result=result,
    )


def forward_enrichment(
    event: AppEvent,
    *,
    payload: dict[str, Any],
    reporter: Any,
    state: NormalizedEventForwardingState,
) -> None:
    phase = normalized_text(payload.get("phase")) or normalized_text(
        getattr(event.enrichment, "phase", None)
    )
    message = normalized_text(payload.get("message")) or normalized_text(
        getattr(event.enrichment, "message", None)
    )
    if not phase:
        return

    report_enrichment = getattr(reporter, "report_enrichment", None)
    if not callable(report_enrichment):
        report_phase_change = getattr(reporter, "report_phase_change", None)
        if callable(report_phase_change):
            report_phase_change(phase)
        return

    scope_id = normalized_text(payload.get("scope_id")) or normalized_text(
        getattr(event.scope, "id", None)
    )
    scope_name = normalized_text(payload.get("scope_name")) or normalized_text(
        getattr(event.scope, "name", None)
    )
    scope_type = normalized_text(payload.get("scope_type")) or normalized_text(
        getattr(event.scope, "type", None)
    )
    memory = coerce_mapping(payload.get("memory")) or coerce_mapping(
        getattr(event.enrichment, "memory", None)
    )
    redaction = coerce_mapping(payload.get("redaction")) or coerce_mapping(
        getattr(event.enrichment, "redaction", None)
    )
    supports_scope, supports_memory, supports_redaction = _enrichment_support(
        reporter,
        state=state,
    )
    kwargs: dict[str, Any] = {
        "phase": phase,
        "message": message or phase,
    }
    if supports_scope:
        kwargs["scope_id"] = scope_id
        kwargs["scope_name"] = scope_name
        kwargs["scope_type"] = scope_type
    if supports_memory and memory is not None:
        kwargs["memory"] = memory
    if supports_redaction and redaction is not None:
        kwargs["redaction"] = redaction
    report_enrichment(**kwargs)


def _enrichment_support(
    reporter: Any,
    *,
    state: NormalizedEventForwardingState,
) -> tuple[bool, bool, bool]:
    """Cache and return a reporter's enrichment-kwarg support flags.

    Old reporters that don't accept ``scope_id``/``memory``/``redaction`` keep
    working because we only pass the kwargs they declare.
    """
    reporter_type = type(reporter)
    cached = state.enrichment_support_by_reporter_type.get(reporter_type)
    if cached is not None:
        return cached

    try:
        params = inspect.signature(reporter.report_enrichment).parameters
    except (AttributeError, TypeError, ValueError):
        cached = (False, False, False)
    else:
        accepts_var_kwargs = any(
            parameter.kind == inspect.Parameter.VAR_KEYWORD for parameter in params.values()
        )
        cached = (
            accepts_var_kwargs or "scope_id" in params,
            accepts_var_kwargs or "memory" in params,
            accepts_var_kwargs or "redaction" in params,
        )

    state.enrichment_support_by_reporter_type[reporter_type] = cached
    return cached


__all__ = [
    "forward_agent_assignment",
    "forward_enrichment",
]
