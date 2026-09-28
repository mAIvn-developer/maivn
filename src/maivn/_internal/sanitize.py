"""Sanitize error text before it is shown to an end user."""

from __future__ import annotations

import re
from typing import Final

_WINDOWS_PATH = re.compile(r'[a-zA-Z]:\\')
_INTERNAL_ERROR = 'An internal error occurred. Please try again.'
_PRIVATE_DATA_BREACH = 'llm payload contains private data values'
_AGENT_FAILURE_PREFIX = 'agent execution failed'
_EXPECTED_SPLIT_PARTS = 2

_LEAK_MARKERS: Final[tuple[str, ...]] = (
    '/',
    '\\\\',
    '.md',
    'maivn_',
    'importlib',
    'langgraph',
    'traceback',
    'file "',
)


def sanitize_user_facing_error_message(message: str) -> str:
    """Return an error message safe to surface, replacing any that leaks internals.

    An exception's text is written for whoever is debugging it, not for whoever triggered
    it: it carries file paths, module names, and stack frames that describe how the system
    is built. Surfacing that to an end user leaks the implementation and tells an attacker
    where to push. Anything bearing a leak marker is replaced wholesale rather than
    scrubbed, because a partially redacted trace is still a trace.

    The private-data breach message is the one exception - it is deliberately explicit,
    since a developer must be told exactly why their payload was refused.
    """
    lowered = message.lower()
    if _PRIVATE_DATA_BREACH in lowered:
        return message

    if lowered.startswith(_AGENT_FAILURE_PREFIX):
        parts = message.split(':', 1)
        if len(parts) == _EXPECTED_SPLIT_PARTS and parts[1].strip():
            message = parts[1].strip()
            lowered = message.lower()

    if any(marker in lowered for marker in _LEAK_MARKERS):
        return _INTERNAL_ERROR
    if _WINDOWS_PATH.search(message) is not None:
        return _INTERNAL_ERROR
    return message


__all__ = ['sanitize_user_facing_error_message']
