"""Platform decision transport: every decision goes through the mAIvn API.

The SDK never calls a decision model directly and holds no decision-model key or
model name. Approved public requests go to ``POST /v1/decisions`` on the configured
mAIvn API origin with the caller's ordinary mAIvn API key. The platform resolves the
decision model from its signed catalog, runs its own private-value check, accounts
and bills the call to the caller's project, and returns verdicts with the receipt.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Literal, Protocol, runtime_checkable

import httpx
from pydantic import BaseModel, ConfigDict, ValidationError

from maivn._internal.decisions.models import (
    DecisionPrediction,
    DecisionRequest,
    DecisionStatus,
    DecisionUsage,
    Key,
    Probability,
)
from maivn._internal.private_placeholders import resolve_private_placeholders

if TYPE_CHECKING:
    from collections.abc import Callable, Sequence

    from maivn._internal.client import Client

DECISIONS_PATH = '/v1/decisions'
# The platform answers every decision; the served model is named in each receipt.
PLATFORM_PROVIDER = 'maivn'


class DecisionBackend(Protocol):
    """Internal single-request seam; cancellation must propagate."""

    async def predict(self, request: DecisionRequest, /) -> DecisionPrediction:
        """Predict one declared category for an explicitly public request."""
        ...


@runtime_checkable
class BatchDecisionBackend(Protocol):
    """Internal ordered batch seam, independent of the single protocol."""

    async def predict_batch(
        self, requests: Sequence[DecisionRequest], /
    ) -> list[DecisionPrediction | ProviderError]:
        """Return one outcome per original request; only the first owns shared usage."""
        ...


class ProviderError(Exception):
    """Content-free decision failure used by the runtime."""

    def __init__(
        self,
        status: DecisionStatus,
        *,
        usage: DecisionUsage | None = None,
        model: str | None = None,
        provider: str | None = PLATFORM_PROVIDER,
    ) -> None:
        self.provider = provider
        self.status = status
        self.usage = usage
        self.model = model
        super().__init__(status.value)


class _Answer(BaseModel):
    model_config = ConfigDict(strict=True, extra='ignore', hide_input_in_errors=True)
    name: Key
    status: Literal['accepted', 'invalid', 'refused']
    value: Key | None = None
    confidence: Probability | None = None


class _Response(BaseModel):
    model_config = ConfigDict(strict=True, extra='ignore', hide_input_in_errors=True)
    status: Literal['decided', 'refused', 'unavailable']
    model: Key | None = None
    answers: list[_Answer] = []
    usage: DecisionUsage | None = None


class PlatformDecisions:
    """Submit only complete public requests approved by a local callback.

    ``client`` is an ordinary :class:`maivn.Client`; without one, a default client is
    built from ``MAIVN_BASE_URL`` and ``MAIVN_API_KEY`` on first use. Local approval
    runs before any client, credential or connection is touched.
    """

    def __init__(
        self,
        *,
        authorize: Callable[[DecisionRequest], bool],
        client: Client | None = None,
    ) -> None:
        self._authorize = authorize
        self._client = client

    def _platform(self) -> Client:
        if self._client is None:
            from maivn._internal.client import Client  # noqa: PLC0415 - avoid an import cycle.

            self._client = Client()
        return self._client

    async def predict(self, request: DecisionRequest, /) -> DecisionPrediction:
        """Decide one request through the platform."""
        (outcome,) = await self.predict_batch((request,))
        if isinstance(outcome, ProviderError):
            raise outcome
        return outcome

    async def predict_batch(
        self, requests: Sequence[DecisionRequest], /
    ) -> list[DecisionPrediction | ProviderError]:
        """Approve every request first; one platform call carries one shared public state.

        Wire question names are synthetic and carry no question content. Only the first
        outcome owns the call's usage receipt.
        """
        validated = [DecisionRequest.model_validate(request) for request in requests]
        if not validated:
            return []
        if any(request.privacy != 'public' for request in validated) or any(
            (request.state, request.private_data) != (validated[0].state, validated[0].private_data)
            for request in validated
        ):
            raise ProviderError(DecisionStatus.BLOCKED)
        try:
            allowed = [self._authorize(request) is True for request in validated]
        except Exception:  # noqa: BLE001 - local authorization fails closed.
            raise ProviderError(DecisionStatus.BLOCKED) from None
        if not all(allowed):
            raise ProviderError(DecisionStatus.BLOCKED)
        wire_names = [f'q{index}' for index in range(len(validated))]
        body: dict[str, object] = {
            'state': validated[0].state,
            'questions': [
                {
                    'name': wire_name,
                    'instructions': request.instructions,
                    'choices': [
                        {'key': choice.key, 'description': choice.description}
                        for choice in request.choices
                    ],
                }
                for wire_name, request in zip(wire_names, validated, strict=True)
            ],
        }
        if validated[0].private_data:
            # Marked values travel only to the platform shield, never to a model.
            body['private_data'] = validated[0].private_values
        try:
            # The SDK's own authenticated client; the runtime owns the total deadline.
            http = self._platform()._http()  # noqa: SLF001 - reuse the authenticated SDK transport.  # pyright: ignore[reportPrivateUsage]
            payload = await http.post(DECISIONS_PATH, body)
        except httpx.TimeoutException:
            raise ProviderError(DecisionStatus.TIMEOUT) from None
        except Exception:  # noqa: BLE001 - any platform failure routes to the baseline.
            raise ProviderError(DecisionStatus.UNAVAILABLE) from None
        return _parse(payload, validated, wire_names)


def _parse(
    payload: object,
    requests: Sequence[DecisionRequest],
    wire_names: list[str],
) -> list[DecisionPrediction | ProviderError]:
    try:
        response = _Response.model_validate(payload)
    except ValidationError:
        raise ProviderError(DecisionStatus.INVALID) from None
    usage, model = response.usage, response.model
    if response.status == 'refused':
        raise ProviderError(DecisionStatus.BLOCKED)
    if response.status == 'unavailable':
        raise ProviderError(DecisionStatus.UNAVAILABLE, usage=usage, model=model)
    answers = {answer.name: answer for answer in response.answers}
    if model is None or len(answers) != len(response.answers) or set(answers) != set(wire_names):
        raise ProviderError(DecisionStatus.INVALID, usage=usage, model=model)
    outcomes: list[DecisionPrediction | ProviderError] = []
    for index, (request, wire_name) in enumerate(zip(requests, wire_names, strict=True)):
        owned = usage if index == 0 else None
        answer = answers[wire_name]
        keys = {choice.key for choice in request.choices}
        value = _resolved(answer.value, request)
        if answer.status == 'refused':
            outcomes.append(ProviderError(DecisionStatus.BLOCKED, usage=owned))
        elif answer.status != 'accepted' or value not in keys or answer.confidence is None:
            outcomes.append(ProviderError(DecisionStatus.INVALID, usage=owned, model=model))
        else:
            outcomes.append(
                DecisionPrediction(
                    value=value,
                    provider=PLATFORM_PROVIDER,
                    model=model,
                    confidence=answer.confidence,
                    usage=owned,
                )
            )
    return outcomes


def _resolved(value: str | None, request: DecisionRequest) -> str | None:
    """Resolve a placeholder verdict locally, as invoke output is resolved."""
    if value is None or not request.private_data:
        return value
    resolved = resolve_private_placeholders(value, request.private_values)
    return resolved if isinstance(resolved, str) else None
