"""Sub-agent attribution for delegated child runs.

A swarm member or delegated agent runs as a child session, and its events reach
the parent's stream stamped with ``parent_session_id``. A child's start and end
become an ``agent_assignment`` keyed by the child session, and the child's
streamed text is re-keyed to that assignment, so a UI files it under the
sub-agent instead of the run's answer and parallel members never overwrite
each other.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from maivn._internal.reporting.app_event_payloads import build_agent_assignment_payload
from maivn.events._vocabulary import (
    ASSIGNMENT_COMPLETED_EVENT_NAME,
    ASSIGNMENT_RECEIVED_EVENT_NAME,
)

from .helpers import clean_text, coerce_mapping

if TYPE_CHECKING:
    from maivn.events._models import JsonObject, NormalizedStreamState

# MARK: Configuration

_ASSIGNMENT_EVENT_NAMES = frozenset(
    {ASSIGNMENT_RECEIVED_EVENT_NAME, ASSIGNMENT_COMPLETED_EVENT_NAME}
)
_FAILED_STATUSES = frozenset({'failure', 'failed', 'error'})
_FALLBACK_AGENT_NAME = 'Sub-agent'


# MARK: Identity


def child_session_id(event: object, payload: JsonObject) -> str | None:
    """Return the child session that emitted ``event``, or None for the run's own."""
    envelope = coerce_mapping(getattr(event, 'data', None))
    parent = clean_text(envelope.get('parent_session_id')) or clean_text(
        payload.get('parent_session_id')
    )
    if parent is None:
        return None
    return clean_text(envelope.get('session_id'))


def subagent_display_name(agent_id: str) -> str:
    """``case_law_analyst`` reads as "Case law analyst"; authored names stay as written."""
    if ' ' in agent_id or not any(separator in agent_id for separator in '_-'):
        return agent_id
    words = ' '.join(agent_id.replace('-', ' ').replace('_', ' ').split())
    return (words[:1].upper() + words[1:]) or agent_id


def _identity(
    child: str,
    payload: JsonObject,
    state: NormalizedStreamState,
) -> tuple[str, str | None]:
    known = state.subagent_identities.get(child)
    if known is not None:
        return known
    run_config = coerce_mapping(payload.get('run_config'))
    agent_id = clean_text(run_config.get('agent_id'))
    identity = (
        subagent_display_name(agent_id) if agent_id else _FALLBACK_AGENT_NAME,
        clean_text(run_config.get('swarm_id')),
    )
    state.subagent_identities[child] = identity
    return identity


# MARK: Attribution


def attribute_to_subagent(
    name: str,
    payload: JsonObject,
    child: str,
    normalized_payloads: list[JsonObject],
    state: NormalizedStreamState,
) -> list[JsonObject]:
    """Re-key a child's assistant text to its sub-agent and project its lifecycle."""
    attributed = [
        _rekey_assistant_chunk(item, child) if item.get('event_name') == 'assistant_chunk' else item
        for item in normalized_payloads
    ]
    if name not in _ASSIGNMENT_EVENT_NAMES:
        return attributed

    agent_name, swarm_name = _identity(child, payload, state)
    if name == ASSIGNMENT_RECEIVED_EVENT_NAME:
        status = 'in_progress'
    elif clean_text(payload.get('status')) in _FAILED_STATUSES or payload.get('error'):
        status = 'failed'
    else:
        status = 'completed'
    attributed.append(
        build_agent_assignment_payload(
            agent_name=agent_name,
            status=status,
            assignment_id=child,
            is_subagent=True,
            swarm_name=swarm_name,
        )
    )
    return attributed


def _rekey_assistant_chunk(payload: JsonObject, child: str) -> JsonObject:
    assistant = coerce_mapping(payload.get('assistant'))
    return {**payload, 'assistant_id': child, 'assistant': {**assistant, 'id': child}}
