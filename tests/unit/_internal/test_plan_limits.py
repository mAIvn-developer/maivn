# pyright: strict
"""A plan limit is refused once and never retried.

Asserts what the change REMOVED: the same 429, retried five times before this
landed, now raises on the first attempt. A test that only checked "the typed
error exists" would pass while the retry loop still burned the whole backoff
budget on an answer that cannot change.
"""

from __future__ import annotations

import asyncio
import json
from typing import TYPE_CHECKING

import httpx
import pytest

# The two retry predicates are private, and reaching for them is the point:
# whether a plan limit is retried is decided inside them, and a test that only
# exercised a public wrapper would keep passing after the check moved out.
from maivn._internal.claiming import (
    _is_transient as claim_is_transient,  # pyright: ignore[reportPrivateUsage]
)
from maivn._internal.errors import MaivnHTTPError, MaivnSDKError
from maivn._internal.plan_limits import (
    PLAN_LIMIT_ERROR_CODE,
    PlanLimitExceededError,
    is_plan_limit_error,
)
from maivn._internal.serving import (
    _is_transient as serving_is_transient,  # pyright: ignore[reportPrivateUsage]
)
from maivn._internal.transport.http import HttpJsonClient

if TYPE_CHECKING:
    from collections.abc import Callable

_REFUSAL_DETAIL = {
    'meter': 'fires',
    'plan': 'free',
    'limit': '100',
    'used': '100',
    'remaining': '0',
    'period_resets_at': '2026-10-01T00:00:00+00:00',
    'upgrade': 'starter',
}
_TOO_MANY_REQUESTS = 429
_SERVICE_UNAVAILABLE = 503


def _refusal_response() -> httpx.Response:
    return httpx.Response(
        _TOO_MANY_REQUESTS,
        content=json.dumps(
            {
                'detail': 'Plan limit reached.',
                'code': PLAN_LIMIT_ERROR_CODE,
                'context': _REFUSAL_DETAIL,
            },
        ).encode(),
        headers={'content-type': 'application/json'},
    )


class _CountingTransport(httpx.AsyncBaseTransport):
    """Counts attempts so a retry can be proved to have happened, or not."""

    def __init__(self, respond: Callable[[], httpx.Response]) -> None:
        self.attempts = 0
        self._respond = respond

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        """Answer every request identically and count the attempt."""
        del request
        self.attempts += 1
        return self._respond()


def test_a_plan_limit_response_raises_the_typed_error() -> None:
    """Classify the platform's plan-limit 429 as its own exception type."""

    async def scenario() -> None:
        transport = _CountingTransport(_refusal_response)
        client = HttpJsonClient(
            base_url='http://127.0.0.1:8100',
            api_key='key',
            timeout_seconds=5.0,
            transport=transport,
        )
        with pytest.raises(PlanLimitExceededError) as caught:
            await client.post('/v1/invoke', {})
        error = caught.value
        assert error.status_code == _TOO_MANY_REQUESTS
        assert error.code == PLAN_LIMIT_ERROR_CODE
        assert error.meter == 'fires'
        assert error.plan == 'free'
        assert error.limit == '100'
        assert error.remaining == '0'
        assert error.upgrade == 'starter'
        assert error.period_resets_at == '2026-10-01T00:00:00+00:00'
        assert transport.attempts == 1, 'the refusal was sent more than once'

    asyncio.run(scenario())


def test_the_typed_error_is_still_an_http_error() -> None:
    """Keep every existing `MaivnHTTPError` handler working."""
    error = PlanLimitExceededError(
        status_code=_TOO_MANY_REQUESTS,
        reason='plan limit reached',
        code=PLAN_LIMIT_ERROR_CODE,
        detail=_REFUSAL_DETAIL,
    )
    assert isinstance(error, MaivnHTTPError)
    assert isinstance(error, MaivnSDKError)


@pytest.mark.parametrize('is_transient', [claim_is_transient, serving_is_transient])
def test_neither_retry_loop_treats_a_plan_limit_as_transient(
    is_transient: Callable[[BaseException], bool],
) -> None:
    """Stop retrying the one 429 that will answer the same way every time."""
    refusal = PlanLimitExceededError(
        status_code=_TOO_MANY_REQUESTS,
        reason='plan limit reached',
        code=PLAN_LIMIT_ERROR_CODE,
        detail=_REFUSAL_DETAIL,
    )
    assert is_transient(refusal) is False


@pytest.mark.parametrize('is_transient', [claim_is_transient, serving_is_transient])
def test_an_ordinary_429_is_still_retried(
    is_transient: Callable[[BaseException], bool],
) -> None:
    """Keep retrying real congestion: the clamp removed one code, not the status."""
    congestion = MaivnHTTPError(
        status_code=_TOO_MANY_REQUESTS,
        reason='slow down',
        code='rate_limited',
    )
    assert is_transient(congestion) is True
    assert (
        is_transient(
            MaivnHTTPError(status_code=_SERVICE_UNAVAILABLE, reason='unavailable'),
        )
        is True
    )


@pytest.mark.parametrize('is_transient', [claim_is_transient, serving_is_transient])
def test_a_plan_limit_arriving_as_a_plain_http_error_is_still_not_retried(
    is_transient: Callable[[BaseException], bool],
) -> None:
    """Classify by code as well as by type, so an older raise site is still honoured."""
    untyped = MaivnHTTPError(
        status_code=_TOO_MANY_REQUESTS,
        reason='plan limit reached',
        code=PLAN_LIMIT_ERROR_CODE,
    )
    assert is_plan_limit_error(untyped) is True
    assert is_transient(untyped) is False


def test_describe_reads_as_one_plain_sentence() -> None:
    """Give a terminal or UI a sentence it can print without rewording it."""
    error = PlanLimitExceededError(
        status_code=_TOO_MANY_REQUESTS,
        reason='plan limit reached',
        code=PLAN_LIMIT_ERROR_CODE,
        detail=_REFUSAL_DETAIL,
    )
    sentence = error.describe()
    assert 'free' in sentence
    assert '100' in sentence
    assert 'starter' in sentence
    assert '2026-10-01T00:00:00+00:00' in sentence


def test_describe_names_the_gap_when_no_allowance_is_configured() -> None:
    """Say a plan has no configured allowance rather than printing a bare refusal."""
    error = PlanLimitExceededError(
        status_code=_TOO_MANY_REQUESTS,
        reason='plan limit reached',
        code=PLAN_LIMIT_ERROR_CODE,
        detail={
            'meter': 'tokens',
            'plan': 'enterprise',
            'limit': None,
            'used': '0',
            'remaining': '0',
            'period_resets_at': '2026-10-01T00:00:00+00:00',
            'upgrade': None,
        },
    )
    sentence = error.describe()
    assert 'enterprise' in sentence
    assert 'no configured' in sentence


def test_an_unrelated_failure_is_not_a_plan_limit() -> None:
    """Keep the classifier from swallowing every other error."""
    assert is_plan_limit_error(MaivnSDKError('boom')) is False
    assert is_plan_limit_error(httpx.ConnectError('down')) is False
