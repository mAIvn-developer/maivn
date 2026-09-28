"""Hook event normalization handler."""

from __future__ import annotations

from typing import TYPE_CHECKING

from maivn._internal.reporting.app_event_payloads import build_hook_fired_payload

from .helpers import clean_text, coerce_mapping

if TYPE_CHECKING:
    from maivn.events._models import JsonObject, NormalizedStreamState

    from .context import NormalizationOptions

# MARK: Hook Firings


def handle_hook_fired_event(
    payload: JsonObject,
    _state: NormalizedStreamState,
    _options: NormalizationOptions,
) -> list[JsonObject]:
    """Normalize one developer hook callback firing.

    A firing carries names and an outcome, never the payload the callback
    received, so there is nothing here to redact. Events that fail to name the
    hook or the card it attaches to are dropped rather than normalized into a
    marker no consumer could place.
    """
    hook = coerce_mapping(payload.get('hook'))
    name = clean_text(payload.get('name')) or clean_text(hook.get('name'))
    stage = clean_text(payload.get('stage')) or clean_text(hook.get('stage'))
    target_type = clean_text(payload.get('target_type')) or clean_text(hook.get('target_type'))
    if name is None or stage is None or target_type is None:
        return []
    error = clean_text(payload.get('error')) or clean_text(hook.get('error'))
    status = (
        clean_text(payload.get('status'))
        or clean_text(hook.get('status'))
        or ('failed' if error is not None else 'completed')
    )
    return [
        build_hook_fired_payload(
            name=name,
            stage=stage,
            status=status,
            target_type=target_type,
            target_id=clean_text(payload.get('target_id')) or clean_text(hook.get('target_id')),
            target_name=(
                clean_text(payload.get('target_name')) or clean_text(hook.get('target_name'))
            ),
            source=clean_text(payload.get('source')) or clean_text(hook.get('source')),
            error=error,
            elapsed_ms=_elapsed_ms(payload, hook),
        )
    ]


def _elapsed_ms(payload: JsonObject, hook: JsonObject) -> int | None:
    """Return a non-negative hook duration from either wire shape."""
    for value in (payload.get('elapsed_ms'), hook.get('elapsed_ms')):
        if isinstance(value, int) and not isinstance(value, bool) and value >= 0:
            return value
    return None


__all__ = ['handle_hook_fired_event']
