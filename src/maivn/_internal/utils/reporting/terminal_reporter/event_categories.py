# pyright: strict
"""Event category configuration and normalization for EventRouterReporter."""

from __future__ import annotations

from collections.abc import Iterable
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .base import BaseReporter


# MARK: Category Constants

EVENT_CATEGORY_ALL = frozenset(
    {
        "enrichment",
        "response",
        "func",
        "model",
        "mcp",
        "agent",
        "system",
        "assignment",
        "lifecycle",
    }
)

TOOL_EVENT_CATEGORIES = frozenset({"func", "model", "mcp", "agent", "system"})

EVENT_TOKEN_ALIASES: dict[str, set[str]] = {
    "all": set(EVENT_CATEGORY_ALL),
    "lifecycle": {"lifecycle"},
    "enrichment": {"enrichment"},
    "response": {"response"},
    "assistant": {"response"},
    "stream": {"response"},
    "tool": set(TOOL_EVENT_CATEGORIES),
    "tools": set(TOOL_EVENT_CATEGORIES),
    "func": {"func"},
    "function": {"func"},
    "func_tool": {"func"},
    "model": {"model"},
    "model_tool": {"model"},
    "mcp": {"mcp"},
    "mcp_tool": {"mcp"},
    "agent": {"agent"},
    "agent_tool": {"agent"},
    "system": {"system"},
    "system_tool": {"system"},
    "assignment": {"assignment"},
    "agent_assignment": {"assignment"},
}


# MARK: Normalization


def normalize_event_categories(
    values: Iterable[object] | str | None,
) -> set[str] | None:
    """Expand user-provided event category tokens into canonical categories."""
    if values is None:
        return None

    raw_values = [values] if isinstance(values, str) else list(values)
    categories: set[str] = set()
    for raw in raw_values:
        if not isinstance(raw, str):
            raise TypeError("event category values must be strings")
        token = raw.strip().lower()
        if not token:
            continue
        resolved = EVENT_TOKEN_ALIASES.get(token)
        if resolved is None:
            valid = ", ".join(sorted(EVENT_TOKEN_ALIASES.keys()))
            raise ValueError(f"Unknown event category '{raw}'. Valid values: {valid}")
        categories.update(resolved)
    return categories


# MARK: Category Resolution


def resolve_tool_category(tool_type: str | None) -> str:
    """Resolve a tool type string to its event category."""
    normalized = str(tool_type or "").strip().lower()
    if normalized in TOOL_EVENT_CATEGORIES:
        return normalized
    return "func"


def resolve_tool_category_from_event_id(
    event_id: str,
    category_map: dict[str, str],
) -> str:
    """Resolve category from an event ID using the tracking map."""
    if event_id:
        mapped = category_map.get(event_id)
        if mapped:
            return mapped
        if event_id.startswith("system-tool:"):
            return "system"
        if event_id.startswith("model-tool:"):
            return "model"
    return "func"


def category_for_print_event(event_type: str) -> str:
    """Determine event category from a print_event event_type."""
    normalized = str(event_type or "").strip().lower()
    if normalized in TOOL_EVENT_CATEGORIES:
        return normalized
    if normalized in {"enrichment"}:
        return "enrichment"
    if normalized in {"stream", "raw", "assistant", "response"}:
        return "response"
    return "lifecycle"


# MARK: Enrichment Backward Compatibility

_ENRICHMENT_KWARG_TOKENS = (
    "scope_id",
    "scope_name",
    "scope_type",
    "memory",
    "redaction",
    "source",
    "trigger_tool",
    "target_tool",
    "reevaluate_count",
    "collected_count",
)

_REEVALUATE_KWARGS = (
    "source",
    "trigger_tool",
    "target_tool",
    "reevaluate_count",
    "collected_count",
)


def _is_enrichment_kwarg_error(error: TypeError) -> bool:
    """Check if a TypeError is from an unexpected enrichment keyword arg."""
    message = str(error)
    return "unexpected keyword argument" in message and any(
        token in message for token in _ENRICHMENT_KWARG_TOKENS
    )


def forward_enrichment_with_fallback(
    reporter: BaseReporter,
    *,
    phase: str,
    message: str,
    scope_id: str | None,
    scope_name: str | None,
    scope_type: str | None,
    memory: dict[str, object] | None,
    redaction: dict[str, object] | None,
    source: str | None = None,
    trigger_tool: str | None = None,
    target_tool: str | None = None,
    reevaluate_count: int | None = None,
    collected_count: int | None = None,
) -> None:
    """Forward enrichment event with backward-compatible fallback.

    Tries the full signature first, then progressively drops newer kwargs
    if the underlying reporter does not accept them. The reevaluate group is
    dropped first (it's only meaningful for ``reevaluate_accrued`` phases),
    then redaction, then memory.
    """
    try:
        reporter.report_enrichment(
            phase=phase,
            message=message,
            scope_id=scope_id,
            scope_name=scope_name,
            scope_type=scope_type,
            memory=memory,
            redaction=redaction,
            source=source,
            trigger_tool=trigger_tool,
            target_tool=target_tool,
            reevaluate_count=reevaluate_count,
            collected_count=collected_count,
        )
        return
    except TypeError as exc:
        if not _is_enrichment_kwarg_error(exc):
            raise
        error_message = str(exc)

    # Drop the reevaluate group if the reporter doesn't accept it
    if any(token in error_message for token in _REEVALUATE_KWARGS):
        try:
            reporter.report_enrichment(
                phase=phase,
                message=message,
                scope_id=scope_id,
                scope_name=scope_name,
                scope_type=scope_type,
                memory=memory,
                redaction=redaction,
            )
            return
        except TypeError as legacy_exc:
            if not _is_enrichment_kwarg_error(legacy_exc):
                raise
            error_message = str(legacy_exc)

    # Try without redaction
    if "redaction" in error_message:
        try:
            reporter.report_enrichment(
                phase=phase,
                message=message,
                scope_id=scope_id,
                scope_name=scope_name,
                scope_type=scope_type,
                memory=memory,
            )
            return
        except TypeError as legacy_exc:
            if not _is_enrichment_kwarg_error(legacy_exc):
                raise
            error_message = str(legacy_exc)

    # Try without memory
    if "memory" in error_message:
        try:
            reporter.report_enrichment(
                phase=phase,
                message=message,
                scope_id=scope_id,
                scope_name=scope_name,
                scope_type=scope_type,
            )
            return
        except TypeError as legacy_exc:
            if not _is_enrichment_kwarg_error(legacy_exc):
                raise

    # Minimal fallback
    reporter.report_enrichment(phase=phase, message=message)
