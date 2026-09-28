"""Event-category configuration and legacy enrichment forwarding helpers."""

from __future__ import annotations

from typing import TYPE_CHECKING, Protocol

if TYPE_CHECKING:
    from collections.abc import Iterable


EVENT_CATEGORY_ALL = frozenset(
    {
        'enrichment',
        'response',
        'func',
        'model',
        'mcp',
        'agent',
        'system',
        'assignment',
        'lifecycle',
    }
)
"""All canonical event categories recognized by the event router."""

TOOL_EVENT_CATEGORIES = frozenset({'func', 'model', 'mcp', 'agent', 'system'})
"""Canonical categories emitted by tool callbacks."""

EVENT_TOKEN_ALIASES: dict[str, set[str]] = {
    'all': set(EVENT_CATEGORY_ALL),
    'lifecycle': {'lifecycle'},
    'enrichment': {'enrichment'},
    'response': {'response'},
    'assistant': {'response'},
    'stream': {'response'},
    'tool': set(TOOL_EVENT_CATEGORIES),
    'tools': set(TOOL_EVENT_CATEGORIES),
    'func': {'func'},
    'function': {'func'},
    'func_tool': {'func'},
    'model': {'model'},
    'model_tool': {'model'},
    'mcp': {'mcp'},
    'mcp_tool': {'mcp'},
    'agent': {'agent'},
    'agent_tool': {'agent'},
    'system': {'system'},
    'system_tool': {'system'},
    'assignment': {'assignment'},
    'agent_assignment': {'assignment'},
}
"""Accepted user-facing category tokens mapped to canonical categories."""


class EnrichmentReporter(Protocol):
    """The compatible subset needed to forward enrichment events."""

    def report_enrichment(self, **kwargs: object) -> None:
        """Report an enrichment event using the reporter's supported signature."""


def normalize_event_categories(values: Iterable[object] | str | None) -> set[str] | None:
    """Expand user-provided category tokens into canonical event categories."""
    if values is None:
        return None

    raw_values = [values] if isinstance(values, str) else list(values)
    categories: set[str] = set()
    for raw in raw_values:
        if not isinstance(raw, str):
            message = 'event category values must be strings'
            raise TypeError(message)
        token = raw.strip().lower()
        if not token:
            continue
        resolved = EVENT_TOKEN_ALIASES.get(token)
        if resolved is None:
            valid = ', '.join(sorted(EVENT_TOKEN_ALIASES))
            message = f"Unknown event category '{raw}'. Valid values: {valid}"
            raise ValueError(message)
        categories.update(resolved)
    return categories


def resolve_tool_category(tool_type: str | None) -> str:
    """Resolve a tool type into its canonical event category."""
    normalized = str(tool_type or '').strip().lower()
    return normalized if normalized in TOOL_EVENT_CATEGORIES else 'func'


def resolve_tool_category_from_event_id(event_id: str, category_map: dict[str, str]) -> str:
    """Resolve a tool category from the tracked event id or its legacy prefix."""
    if event_id:
        mapped = category_map.get(event_id)
        if mapped:
            return mapped
        if event_id.startswith('system-tool:'):
            return 'system'
        if event_id.startswith('model-tool:'):
            return 'model'
    return 'func'


def category_for_print_event(event_type: str) -> str:
    """Determine the router category for a legacy ``print_event`` event type."""
    normalized = str(event_type or '').strip().lower()
    if normalized in TOOL_EVENT_CATEGORIES:
        return normalized
    if normalized == 'enrichment':
        return 'enrichment'
    if normalized in {'stream', 'raw', 'assistant', 'response'}:
        return 'response'
    return 'lifecycle'


_ENRICHMENT_KWARG_TOKENS = (
    'scope_id',
    'scope_name',
    'scope_type',
    'memory',
    'redaction',
    'source',
    'trigger_tool',
    'target_tool',
    'reevaluate_count',
    'collected_count',
)
_REEVALUATE_KWARGS = (
    'source',
    'trigger_tool',
    'target_tool',
    'reevaluate_count',
    'collected_count',
)


def _is_enrichment_kwarg_error(error: TypeError) -> bool:
    """Return whether an error rejects a known enrichment keyword."""
    return _is_enrichment_kwarg_message(str(error))


def _is_enrichment_kwarg_message(message: str) -> bool:
    """Return whether an error message rejects a known enrichment keyword."""
    return 'unexpected keyword argument' in message and any(
        token in message for token in _ENRICHMENT_KWARG_TOKENS
    )


def forward_enrichment_with_fallback(  # noqa: PLR0913 - one parameter per enrichment field.
    reporter: EnrichmentReporter,
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
    """Forward enrichment while supporting reporters with older callback signatures."""
    full_kwargs: dict[str, object] = {
        'phase': phase,
        'message': message,
        'scope_id': scope_id,
        'scope_name': scope_name,
        'scope_type': scope_type,
        'memory': memory,
        'redaction': redaction,
        'source': source,
        'trigger_tool': trigger_tool,
        'target_tool': target_tool,
        'reevaluate_count': reevaluate_count,
        'collected_count': collected_count,
    }
    error_message = _try_forward_enrichment(reporter, full_kwargs)
    if error_message is None:
        return

    base_kwargs: dict[str, object] = {
        'phase': phase,
        'message': message,
        'scope_id': scope_id,
        'scope_name': scope_name,
        'scope_type': scope_type,
        'memory': memory,
        'redaction': redaction,
    }
    if any(token in error_message for token in _REEVALUATE_KWARGS):
        error_message = _try_forward_enrichment(reporter, base_kwargs)
        if error_message is None:
            return

    if 'redaction' in error_message:
        base_kwargs.pop('redaction')
        error_message = _try_forward_enrichment(reporter, base_kwargs)
        if error_message is None:
            return

    if 'memory' in error_message:
        base_kwargs.pop('memory')
        error_message = _try_forward_enrichment(reporter, base_kwargs)
        if error_message is None:
            return

    reporter.report_enrichment(phase=phase, message=message)


def _try_forward_enrichment(
    reporter: EnrichmentReporter,
    kwargs: dict[str, object],
) -> str | None:
    """Forward one enrichment signature and return a recognized keyword-error message."""
    try:
        reporter.report_enrichment(**kwargs)
    except TypeError as error:
        if not _is_enrichment_kwarg_error(error):
            raise
        return str(error)
    return None


__all__ = [
    'EVENT_CATEGORY_ALL',
    'EVENT_TOKEN_ALIASES',
    'TOOL_EVENT_CATEGORIES',
    'category_for_print_event',
    'forward_enrichment_with_fallback',
    'normalize_event_categories',
    'resolve_tool_category',
    'resolve_tool_category_from_event_id',
]
