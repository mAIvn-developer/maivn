"""Forwarder for ``hook_fired`` events.

Translates a normalized hook-fired AppEvent into a ``report_hook_fired``
call on the supplied reporter. Reporters that don't implement
``report_hook_fired`` simply drop the event (it has a no-op default on
:class:`maivn._internal.utils.reporting.terminal_reporter.BaseReporter`,
so this only matters for fully-custom reporter implementations).
"""

from __future__ import annotations

from typing import Any

from ..._models import AppEvent
from ..payload import mapping_value, normalized_text
from ..state import NormalizedEventForwardingState


def forward_hook_fired(
    event: AppEvent,
    *,
    payload: dict[str, Any],
    reporter: Any,
    state: NormalizedEventForwardingState,
) -> None:
    _ = state
    report_hook_fired = getattr(reporter, "report_hook_fired", None)
    if not callable(report_hook_fired):
        return

    name = normalized_text(payload.get("name")) or normalized_text(
        mapping_value(payload.get("hook"), "name")
    )
    stage = normalized_text(payload.get("stage")) or normalized_text(
        mapping_value(payload.get("hook"), "stage")
    )
    status = normalized_text(payload.get("status")) or normalized_text(
        mapping_value(payload.get("hook"), "status")
    )
    target_type = normalized_text(payload.get("target_type")) or normalized_text(
        mapping_value(payload.get("hook"), "target_type")
    )
    target_id = normalized_text(payload.get("target_id")) or normalized_text(
        mapping_value(payload.get("hook"), "target_id")
    )
    target_name = normalized_text(payload.get("target_name")) or normalized_text(
        mapping_value(payload.get("hook"), "target_name")
    )
    error = normalized_text(payload.get("error")) or normalized_text(
        mapping_value(payload.get("hook"), "error")
    )
    elapsed_ms_raw = payload.get("elapsed_ms")
    if elapsed_ms_raw is None:
        elapsed_ms_raw = mapping_value(payload.get("hook"), "elapsed_ms")
    elapsed_ms = elapsed_ms_raw if isinstance(elapsed_ms_raw, int) else None

    if not name or not stage or not status or not target_type:
        return

    report_hook_fired(
        name=name,
        stage=stage,
        status=status,
        target_type=target_type,
        target_id=target_id,
        target_name=target_name,
        error=error,
        elapsed_ms=elapsed_ms,
    )


__all__ = ["forward_hook_fired"]
