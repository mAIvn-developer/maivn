"""Offline mAIvn platform for decision tests: answers ``POST /v1/decisions`` in-process.

Tests build an ordinary :class:`maivn.Client` over an ``httpx.MockTransport``; nothing
leaves the process and no decision-model key or model name exists on the SDK side.
"""

from __future__ import annotations

import inspect
import json
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, TypeAlias

import httpx

from maivn import Client

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable

PLATFORM_MODEL = 'catalog-decision-model'
BASE_URL = 'https://api.example.test'
API_KEY = 'offline-maivn-key'

# (question, state) -> (value, confidence), 'invalid' or 'refused'.
Chooser: TypeAlias = 'Callable[[dict[str, Any], str], tuple[str, float] | str]'


def decided(
    body: dict[str, Any],
    choose: Chooser,
    *,
    model: str = PLATFORM_MODEL,
    usage: dict[str, int | None] | None = None,
) -> dict[str, Any]:
    """Build the platform's ``decided`` reply for one request body."""
    answers: list[dict[str, Any]] = []
    for question in body['questions']:
        verdict = choose(question, body['state'])
        if verdict in ('invalid', 'refused'):
            answers.append({'name': question['name'], 'status': verdict})
        else:
            value, confidence = verdict
            answers.append(
                {
                    'name': question['name'],
                    'status': 'accepted',
                    'value': value,
                    'confidence': confidence,
                }
            )
    reply: dict[str, Any] = {'status': 'decided', 'model': model, 'answers': answers}
    if usage is not None:
        reply['usage'] = usage
    return reply


@dataclass
class OfflinePlatform:
    """Record every decision call and answer through ``respond``."""

    respond: Callable[[dict[str, Any]], httpx.Response | Awaitable[httpx.Response]]
    calls: list[dict[str, Any]] = field(default_factory=list)
    requests: list[httpx.Request] = field(default_factory=list)

    async def _handle(self, request: httpx.Request) -> httpx.Response:
        assert request.url.path == '/v1/decisions', request.url.path
        assert request.method == 'POST'
        body: dict[str, Any] = json.loads(request.content)
        self.requests.append(request)
        self.calls.append(body)
        reply = self.respond(body)
        if inspect.isawaitable(reply):
            reply = await reply
        return reply

    def client(self) -> Client:
        """Return an ordinary SDK client whose only route is this offline platform."""
        return Client(
            api_key=API_KEY,
            base_url=BASE_URL,
            transport=httpx.MockTransport(self._handle),
            private_data_store=None,
            auto_detect_timezone=False,
        )


def choosing(
    choose: Chooser,
    *,
    model: str = PLATFORM_MODEL,
    usage: dict[str, int | None] | None = None,
) -> OfflinePlatform:
    """Platform that answers every call with ``decided`` from ``choose``."""
    return OfflinePlatform(
        lambda body: httpx.Response(200, json=decided(body, choose, model=model, usage=usage))
    )


__all__ = [
    'API_KEY',
    'BASE_URL',
    'PLATFORM_MODEL',
    'Chooser',
    'OfflinePlatform',
    'choosing',
    'decided',
]
