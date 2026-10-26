"""Which platform failures a long-running SDK loop retries and which end it.

The serving heartbeat and the agent-work claim loop face exactly the same network, so
they share one classification.
"""

from __future__ import annotations

from http import HTTPStatus
from typing import Final

import httpx

from maivn._internal.errors import MaivnHTTPError
from maivn._internal.plan_limits import is_plan_limit_error

# Statuses below 500 that still mean "try again shortly" rather than "you are
# wrong". Everything at or above 500 is transient by definition.
_RETRYABLE_STATUSES: Final[frozenset[int]] = frozenset(
    {HTTPStatus.REQUEST_TIMEOUT, HTTPStatus.TOO_MANY_REQUESTS},
)
# The platform said no, and saying it again will not help.
_TERMINAL_STATUSES: Final[frozenset[int]] = frozenset(
    {HTTPStatus.UNAUTHORIZED, HTTPStatus.FORBIDDEN},
)


def is_transient(exc: BaseException) -> bool:
    """Report whether retrying after a short backoff can succeed."""
    if is_plan_limit_error(exc):
        # A plan limit is not congestion. The same 429 will come back on every
        # attempt, so retrying only delays the message the developer can act on
        # by the whole backoff budget.
        return False
    if isinstance(exc, MaivnHTTPError):
        return (
            exc.status_code >= HTTPStatus.INTERNAL_SERVER_ERROR
            or exc.status_code in _RETRYABLE_STATUSES
        )
    # Connection refused, DNS failure, read timeout: the transport never got an
    # answer, which is exactly the blipped-Wi-Fi case.
    return isinstance(exc, httpx.TransportError)


def is_terminal(exc: MaivnHTTPError) -> bool:
    """Report whether the platform refused the credentials, so the loop must stop."""
    return exc.status_code in _TERMINAL_STATUSES


__all__ = ['is_terminal', 'is_transient']
