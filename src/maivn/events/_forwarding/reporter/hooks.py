"""Forwarder for ``hook_fired`` events.

Translates a normalized hook-fired AppEvent into a ``report_hook_fired``
call on the supplied reporter. Reporters that don't implement
``report_hook_fired`` simply drop the event (it has a no-op default on
:class:`maivn._internal.utils.reporting.terminal_reporter.BaseReporter`,
so this only matters for fully-custom reporter implementations).
"""

# pyright: strict
from __future__ import annotations

import inspect
from typing import Protocol, cast

from ..._models import AppEvent
from ..payload import EventPayload, mapping_value, normalized_text
from ..state import NormalizedEventForwardingState

# MARK: Callback Protocols


class HookFiredCallback(Protocol):
    def __call__(
        self,
        *,
        name: str,
        stage: str,
        status: str,
        target_type: str,
        target_id: str | None = None,
        target_name: str | None = None,
        source: str | None = None,
        error: str | None = None,
        elapsed_ms: int | None = None,
    ) -> None: ...


# MARK: Hook Forwarding


def forward_hook_fired(
    event: AppEvent,
    *,
    payload: EventPayload,
    reporter: object,
    state: NormalizedEventForwardingState,
) -> None:
    _ = (event, state)
    callback = getattr(reporter, "report_hook_fired", None)
    if not callable(callback):
        return
    report_hook_fired = cast(HookFiredCallback, callback)

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
    source = normalized_text(payload.get("source")) or normalized_text(
        mapping_value(payload.get("hook"), "source")
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

    if source is not None and _callback_accepts_keyword(report_hook_fired, "source"):
        report_hook_fired(
            name=name,
            stage=stage,
            status=status,
            target_type=target_type,
            target_id=target_id,
            target_name=target_name,
            source=source,
            error=error,
            elapsed_ms=elapsed_ms,
        )
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


def _callback_accepts_keyword(callback: HookFiredCallback, keyword: str) -> bool:
    try:
        params = inspect.signature(callback).parameters
    except (TypeError, ValueError):
        return False
    accepts_var_kwargs = any(
        parameter.kind == inspect.Parameter.VAR_KEYWORD for parameter in params.values()
    )
    return accepts_var_kwargs or keyword in params


__all__ = ["forward_hook_fired"]
