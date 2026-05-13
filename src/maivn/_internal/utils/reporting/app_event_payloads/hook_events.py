"""Payload builder for ``hook_fired`` events.

Emitted each time a developer-registered scope or tool hook callback runs.
The payload carries enough information for a frontend to attach the firing
to the correct on-screen card (tool card, agent card, or swarm card) and
render its name + status as a persistent header (before) or footer (after).
"""

from __future__ import annotations

from typing import Any

from .common import attach_common_fields


def build_hook_fired_payload(
    *,
    name: str,
    stage: str,
    status: str,
    target_type: str,
    target_id: str | None = None,
    target_name: str | None = None,
    error: str | None = None,
    elapsed_ms: int | None = None,
) -> dict[str, Any]:
    """Build the payload for a single hook-callback firing.

    Args:
        name: Display name of the hook (typically ``hook_callable.__name__``).
        stage: ``"before"`` or ``"after"``.
        status: ``"completed"`` or ``"failed"``.
        target_type: ``"tool"`` / ``"agent"`` / ``"swarm"``.
        target_id: Per-invocation event ID for tool targets; agent ID or
            swarm name for scope targets.
        target_name: Display name of the target (tool name, agent name,
            swarm name) — used as a fallback when ``target_id`` is missing
            or for UI labels.
        error: Error message when ``status == "failed"``.
        elapsed_ms: How long the hook callable ran, in milliseconds.
    """
    payload = {
        "name": name,
        "stage": stage,
        "status": status,
        "target_type": target_type,
        "target_id": target_id,
        "target_name": target_name,
        "error": error,
        "elapsed_ms": elapsed_ms,
        "hook": {
            "name": name,
            "stage": stage,
            "status": status,
            "target_type": target_type,
            "target_id": target_id,
            "target_name": target_name,
            "error": error,
            "elapsed_ms": elapsed_ms,
        },
    }
    return attach_common_fields(
        payload,
        event_name="hook_fired",
        event_kind="hook",
        scope=None,
        participant=None,
    )


__all__ = ["build_hook_fired_payload"]
