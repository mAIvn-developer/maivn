"""Forwarders for agent assignment and enrichment events."""

# pyright: strict
from __future__ import annotations

import inspect
from typing import Protocol, cast

from ..._models import AppEvent
from ..payload import EventPayload, coerce_mapping, normalized_text
from ..state import NormalizedEventForwardingState

# MARK: Callback Protocols


class AgentAssignmentCallback(Protocol):
    def __call__(
        self,
        *,
        agent_name: str,
        status: str,
        assignment_id: str,
        swarm_name: str | None = None,
        error: str | None = None,
        result: object | None = None,
    ) -> None: ...


class EnrichmentCallback(Protocol):
    def __call__(self, **kwargs: object) -> None: ...


class PhaseChangeCallback(Protocol):
    def __call__(self, phase: str) -> None: ...


# MARK: Assignment Forwarding


def forward_agent_assignment(
    event: AppEvent,
    *,
    payload: EventPayload,
    reporter: object,
    state: NormalizedEventForwardingState,
) -> None:
    _ = state
    callback = getattr(reporter, "report_agent_assignment", None)
    if not callable(callback):
        return
    report_agent_assignment = cast(AgentAssignmentCallback, callback)

    assignment_id = normalized_text(payload.get("assignment_id")) or normalized_text(
        event.assignment.id if event.assignment is not None else None
    )
    agent_name = normalized_text(payload.get("agent_name")) or normalized_text(
        event.assignment.agent_name if event.assignment is not None else None
    )
    status = normalized_text(payload.get("status")) or normalized_text(
        event.assignment.status if event.assignment is not None else None
    )
    swarm_name = normalized_text(payload.get("swarm_name")) or normalized_text(
        event.assignment.swarm_name if event.assignment is not None else None
    )
    error = normalized_text(payload.get("error")) or normalized_text(
        event.assignment.error if event.assignment is not None else None
    )
    result = payload.get(
        "result",
        cast(object, event.assignment.result) if event.assignment is not None else None,
    )

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


# MARK: Enrichment Forwarding


def forward_enrichment(
    event: AppEvent,
    *,
    payload: EventPayload,
    reporter: object,
    state: NormalizedEventForwardingState,
) -> None:
    enrichment = event.enrichment
    scope = event.scope
    phase = normalized_text(payload.get("phase")) or normalized_text(
        enrichment.phase if enrichment is not None else None
    )
    message = normalized_text(payload.get("message")) or normalized_text(
        enrichment.message if enrichment is not None else None
    )
    if not phase:
        return

    enrichment_callback = getattr(reporter, "report_enrichment", None)
    if not callable(enrichment_callback):
        phase_change_callback = getattr(reporter, "report_phase_change", None)
        if callable(phase_change_callback):
            cast(PhaseChangeCallback, phase_change_callback)(phase)
        return
    report_enrichment = cast(EnrichmentCallback, enrichment_callback)

    scope_id = normalized_text(payload.get("scope_id")) or normalized_text(
        scope.id if scope is not None else None
    )
    scope_name = normalized_text(payload.get("scope_name")) or normalized_text(
        scope.name if scope is not None else None
    )
    scope_type = normalized_text(payload.get("scope_type")) or normalized_text(
        scope.type if scope is not None else None
    )
    memory = coerce_mapping(payload.get("memory")) or coerce_mapping(
        cast(object, enrichment.memory) if enrichment is not None else None
    )
    redaction = coerce_mapping(payload.get("redaction")) or coerce_mapping(
        cast(object, enrichment.redaction) if enrichment is not None else None
    )
    supports_scope, supports_memory, supports_redaction = _enrichment_support(
        reporter,
        state=state,
    )
    kwargs: dict[str, object] = {
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
    reporter: object,
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

    report_enrichment = getattr(reporter, "report_enrichment", None)
    if not callable(report_enrichment):
        cached = (False, False, False)
        state.enrichment_support_by_reporter_type[reporter_type] = cached
        return cached

    try:
        params = inspect.signature(report_enrichment).parameters
    except (TypeError, ValueError):
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
