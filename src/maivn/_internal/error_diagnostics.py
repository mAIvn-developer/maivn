"""Project existing value-free error facts without changing failure semantics."""

from __future__ import annotations

import re
from collections.abc import Mapping
from contextlib import suppress
from typing import cast

from maivn._internal.sanitize import sanitize_user_facing_error_message

# Match maivn_observability.attributes.MAX_EXCEPTION_MESSAGE_CHARS without
# importing the server observability package into the dependency-light SDK.
_MAX_MESSAGE_CHARS = 512
_TOKEN = re.compile(r'[A-Za-z0-9_.:-]{1,160}\Z')
_IDENTITIES = (
    'session_id',
    'root_event_id',
    'run_id',
    'model_call_id',
    'call_id',
    'attempt_id',
    'tool_call_id',
    'final_output_id',
    'event_id',
)


def diagnostic_facts(*sources: Mapping[str, object]) -> dict[str, str | int]:
    """Keep only bounded structural facts; never copy free-text error details."""
    facts: dict[str, str | int] = {}
    for source in sources:
        for key in ('error_code', *_IDENTITIES):
            value = source.get(key)
            if key not in facts and isinstance(value, str) and _TOKEN.fullmatch(value):
                facts[key] = value
        index = source.get('attempt_index')
        if (
            'attempt_index' not in facts
            and isinstance(index, int)
            and not isinstance(index, bool)
            and index >= 0
        ):
            facts['attempt_index'] = index
    return facts


def attach_error_diagnostics(error: BaseException, *sources: Mapping[str, object]) -> None:
    """Attach optional identity without letting diagnostics replace the original failure."""
    # Third-party exceptions may expose read-only attributes or custom accessors.
    with suppress(Exception):
        existing = {
            key: getattr(error, key, None) for key in ('error_code', 'session_id', 'root_event_id')
        }
        facts = diagnostic_facts(existing, *sources)
        for key in ('error_code', 'session_id', 'root_event_id'):
            if getattr(error, key, None) is None and key in facts:
                setattr(error, key, facts[key])
        prior_correlation = getattr(error, 'correlation', None)
        if prior_correlation is not None and not isinstance(prior_correlation, Mapping):
            return
        correlation = diagnostic_facts(cast('Mapping[str, object]', prior_correlation or {}), facts)
        correlation.pop('error_code', None)
        if correlation:
            setattr(error, 'correlation', correlation)  # noqa: B010 - additive third-party exception metadata.


def error_action_hint(code: str | None) -> str:
    """Suggest inspection or correction without authorizing replay of executed work."""
    if code in {'durable_event_refused', 'private_placeholder_unresolvable'}:
        return 'Check private-data declarations and the refused field before submitting more work.'
    if code in {
        'contract_validation_failed',
        'validation_failed',
        'invalid_arguments',
        'structured_output_invalid_json',
    }:
        return 'Check arguments and the expected output schema.'
    if code in {'permission_denied', 'unauthorized', 'forbidden', 'configuration_error'}:
        return 'Check configuration and permissions.'
    if code in {'usage_pending', 'accounting_incomplete', 'settlement_pending'}:
        return 'Wait for settlement and query session usage again.'
    if code in {'cancelled', 'session_cancelled'}:
        return 'Inspect the session for work completed before cancellation.'
    return 'Inspect the failed session before submitting more work.'


def safe_error_message(message: str) -> str:
    """Apply the existing consumer sanitizer and shared observability message bound."""
    safe = ' '.join(sanitize_user_facing_error_message(message).split())
    if len(safe) > _MAX_MESSAGE_CHARS:
        return f'{safe[: _MAX_MESSAGE_CHARS - 3]}...'
    return safe


__all__ = [
    'attach_error_diagnostics',
    'diagnostic_facts',
    'error_action_hint',
    'safe_error_message',
]
