"""The one refusal the SDK must never retry.

A plan limit is not congestion. HTTP 429 usually means "we are busy, come back
in a moment", and the SDK is right to retry it -- but a tenant who has used
every run their plan sells will get the same answer in a moment, and in an
hour, and on every one of the five attempts the retry loop makes. Retrying it
turns one honest refusal into a burst of identical refusals, delays the message
the developer actually needs by the whole backoff budget, and hides the fact
that the platform said no for a reason they can act on.

So this module carries two things and nothing else: the code the platform sends
for that refusal, and a typed exception that classifies it. Every retry
predicate in the SDK asks `is_plan_limit_error` before it asks about the
status, which is why the answer cannot drift between the claim loop and the
serving loop.

3.10-compatible on purpose (SDK floor): no `StrEnum`, no `match`, and every
`X | Y` annotation lives under `from __future__ import annotations`.
"""

from __future__ import annotations

from typing import Any, cast

from maivn._internal.errors import MaivnHTTPError

__all__ = [
    'PLAN_LIMIT_ERROR_CODE',
    'PlanLimitExceededError',
    'is_plan_limit_error',
]

PLAN_LIMIT_ERROR_CODE = 'plan_limit_exceeded'
"""The platform's single error code for every plan-limit refusal.

One code with the meter in the detail, rather than one code per meter: a client
that only needs to stop retrying reads the code, and a client that wants to say
"you are out of runs" reads `detail['meter']`. Three codes would have made the
first job depend on a list that drifts.
"""

_METER_SENTENCES = {
    'tokens': 'monthly processing allowance',
    'fires': 'monthly runs',
    'spend': 'spend guardrail',
}


class PlanLimitExceededError(MaivnHTTPError):
    """Raised when the platform refused a run because a plan limit is reached.

    Terminal by nature, and a subclass of `MaivnHTTPError` so every existing
    handler keeps working while a surface that wants to say something better
    than the raw code can classify by TYPE instead of matching message text --
    the same shape `DurableBoundaryRefusedError` already uses.

    The typed fields are read off the platform's detail rather than parsed out
    of the message, so a caller can render the reset date and the upgrade path
    without knowing how the sentence was worded.
    """

    def __init__(  # noqa: PLR0913 - one keyword per envelope member.
        self,
        *,
        status_code: int,
        reason: str,
        code: str | None = None,
        detail: Any | None = None,
        fields: list[dict[str, str]] | None = None,
        retry_after: int | None = None,
    ) -> None:
        """Create the refusal, pulling the meter, limit and reset out of the detail."""
        refusal: dict[str, object] = (
            cast('dict[str, object]', detail) if isinstance(detail, dict) else {}
        )
        self.meter = _text(refusal.get('meter'))
        self.plan = _text(refusal.get('plan'))
        self.limit = _text(refusal.get('limit'))
        self.used = _text(refusal.get('used'))
        self.remaining = _text(refusal.get('remaining'))
        self.period_resets_at = _text(refusal.get('period_resets_at'))
        self.upgrade = _text(refusal.get('upgrade'))
        super().__init__(
            status_code=status_code,
            reason=reason,
            code=code,
            detail=cast('Any', detail),
            fields=fields,
            retry_after=retry_after,
        )

    def describe(self) -> str:
        """Return one plain sentence a terminal or UI can show without rewording it."""
        plan = self.plan or 'current'
        meter = _METER_SENTENCES.get(self.meter or '', 'plan limit')
        if self.limit is None:
            return (
                f'Your {plan} plan has no configured {meter}, so runs are refused. '
                f'Contact support to set one.'
            )
        sentence = f"Your {plan} plan's {self.limit} {meter} for this period are used up."
        if self.period_resets_at:
            sentence += f' It resets on {self.period_resets_at}.'
        if self.upgrade:
            sentence += f' Upgrade to {self.upgrade} to keep running.'
        return sentence


def is_plan_limit_error(exc: BaseException) -> bool:
    """Report whether a failure is a plan-limit refusal, by type or by code.

    The type check is the fast path. The code check is what keeps an older
    client honest: a `MaivnHTTPError` raised before this class existed, or
    reconstructed by a caller, still carries the code, and a refusal that is
    retried because it arrived as the wrong Python type is exactly the bug this
    function exists to prevent.
    """
    if isinstance(exc, PlanLimitExceededError):
        return True
    return isinstance(exc, MaivnHTTPError) and exc.code == PLAN_LIMIT_ERROR_CODE


def _text(value: object) -> str | None:
    if value is None:
        return None
    if isinstance(value, str):
        return value or None
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return str(value)
    return None
