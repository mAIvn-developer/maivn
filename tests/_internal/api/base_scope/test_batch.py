# pyright: strict
from __future__ import annotations

import asyncio
import time
from collections.abc import Sequence
from threading import Lock
from typing import cast

import pytest
from maivn_shared import BaseMessage, HumanMessage, SessionRequest, SessionResponse
from pydantic import PrivateAttr

from maivn._internal.api.agent import Agent
from maivn._internal.api.base_scope import BaseScope
from maivn._internal.api.client import Client
from maivn._internal.api.swarm import Swarm
from maivn._internal.core.interfaces.orchestrator_protocol import (
    AgentOrchestratorInterface,
)
from maivn._internal.utils.configuration import MaivnConfiguration, ServerConfiguration


class _BatchScope(BaseScope):
    delay_seconds: float = 0.0

    _active: int = PrivateAttr(default=0)
    _calls: list[dict[str, object]] = PrivateAttr(default_factory=list)
    _lock: Lock = PrivateAttr(default_factory=Lock)
    _max_active: int = PrivateAttr(default=0)

    @property
    def calls(self) -> list[dict[str, object]]:
        return self._calls

    @property
    def max_active(self) -> int:
        return self._max_active

    def invoke(
        self,
        messages: str,
        *,
        prefix: str = "",
        metadata: dict[str, object] | None = None,
    ) -> SessionResponse:
        with self._lock:
            self._active += 1
            self._max_active = max(self._max_active, self._active)
            self._calls.append({"messages": messages, "metadata": metadata})

        try:
            time.sleep(self.delay_seconds)
            return SessionResponse(responses=[f"{prefix}{messages}"])
        finally:
            with self._lock:
                self._active -= 1


def _make_client() -> Client:
    config = MaivnConfiguration(
        server=ServerConfiguration(
            base_url="http://example.com",
            mock_base_url="http://example.com",
        )
    )
    return Client.from_configuration(api_key="key", configuration=config)


def _first_response_text(response: SessionResponse) -> str:
    """Return the first response string for ordered assertions."""
    return response.responses[0]


def _message_content_text(message: BaseMessage) -> str:
    """Stringify ``BaseMessage.content`` (union-typed by the upstream stub)."""
    content = cast(object, message.content)
    return content if isinstance(content, str) else str(content)


def test_scope_batch_preserves_order_forwards_kwargs_and_limits_concurrency() -> None:
    scope = _BatchScope(delay_seconds=0.05)

    responses = scope.batch(
        ["a", "b", "c", "d"],
        max_concurrency=2,
        prefix="item-",
        metadata={"source": "test"},
    )

    assert [_first_response_text(response) for response in responses] == [
        "item-a",
        "item-b",
        "item-c",
        "item-d",
    ]
    assert scope.max_active == 2
    assert [call["messages"] for call in scope.calls] == ["a", "b", "c", "d"]
    assert all(call["metadata"] == {"source": "test"} for call in scope.calls)


def test_scope_abatch_preserves_order_and_runs_concurrently() -> None:
    scope = _BatchScope(delay_seconds=0.05)

    async def _run() -> list[SessionResponse]:
        return await scope.abatch(
            ["a", "b", "c"],
            max_concurrency=3,
            prefix="async-",
        )

    responses = asyncio.run(_run())

    assert [_first_response_text(response) for response in responses] == [
        "async-a",
        "async-b",
        "async-c",
    ]
    assert scope.max_active == 3


def test_scope_batch_rejects_invalid_max_concurrency() -> None:
    scope = _BatchScope()

    with pytest.raises(ValueError, match="max_concurrency"):
        _ = scope.batch(["a"], max_concurrency=0)

    with pytest.raises(ValueError, match="max_concurrency"):
        _ = asyncio.run(scope.abatch(["a"], max_concurrency=0))


def test_scope_batch_returns_empty_list_for_empty_inputs() -> None:
    scope = _BatchScope()

    assert scope.batch([]) == []
    assert asyncio.run(scope.abatch([])) == []


def test_agent_batch_uses_fresh_orchestrator_per_input(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    created: list[_AgentBatchOrchestrator] = []

    def _build_orchestrator(self: Agent) -> _AgentBatchOrchestrator:
        _ = self
        orchestrator = _AgentBatchOrchestrator()
        created.append(orchestrator)
        return orchestrator

    monkeypatch.setattr(Agent, "_build_orchestrator", _build_orchestrator)

    agent = Agent(api_key="test")
    # ``_orchestrator`` is a Pydantic PrivateAttr typed as the orchestrator protocol.
    # The cached stub is only required to *error* if reused; cast-through-object lands a
    # stub past the protocol surface (Pattern 2). ``setattr`` keeps the assignment from
    # tripping ``reportPrivateUsage`` on the test-side attribute write.
    orchestrator_attr = "_orchestrator"
    setattr(
        agent,
        orchestrator_attr,
        cast(AgentOrchestratorInterface, cast(object, _CachedOrchestrator())),
    )

    responses = agent.batch(
        [
            [HumanMessage(content="one")],
            [HumanMessage(content="two")],
        ],
        max_concurrency=2,
    )

    assert [_first_response_text(response) for response in responses] == ["one", "two"]
    assert len(created) == 2
    assert all(orchestrator.closed for orchestrator in created)


def test_swarm_batch_uses_inherited_batch_invocation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    lock = Lock()
    active = 0
    max_active = 0

    class _SwarmBatchOrchestrator:
        def compile_state(self, messages: list[HumanMessage], **kwargs: object) -> SessionRequest:
            _ = kwargs
            return SessionRequest(metadata={"content": _message_content_text(messages[-1])})

        def register_swarm_agent_tools(self, agent_tools: list[object]) -> None:
            _ = agent_tools

        def invoke_compiled_state(
            self,
            state: SessionRequest,
            *,
            thread_id: str | None = None,
            verbose: bool = False,
        ) -> SessionResponse:
            nonlocal active, max_active
            _ = (thread_id, verbose)
            with lock:
                active += 1
                max_active = max(max_active, active)

            try:
                time.sleep(0.05)
                assert state.metadata is not None
                return SessionResponse(responses=[str(state.metadata["content"])])
            finally:
                with lock:
                    active -= 1

    def _build_orchestrator(
        self: Swarm,
        agent: Agent,
    ) -> _SwarmBatchOrchestrator:
        _ = (self, agent)
        return _SwarmBatchOrchestrator()

    monkeypatch.setattr(Swarm, "_build_orchestrator", _build_orchestrator)

    agent = Agent(name="agent", client=_make_client())
    swarm = Swarm(name="swarm", agents=[agent])

    responses = swarm.batch(
        [
            [HumanMessage(content="one")],
            [HumanMessage(content="two")],
            [HumanMessage(content="three")],
        ],
        max_concurrency=2,
    )

    assert [_first_response_text(response) for response in responses] == [
        "one",
        "two",
        "three",
    ]
    assert max_active == 2


class _AgentBatchOrchestrator:
    closed: bool

    def __init__(self) -> None:
        self.closed = False

    def invoke(
        self,
        messages: Sequence[BaseMessage],
        **kwargs: object,
    ) -> SessionResponse:
        _ = kwargs
        time.sleep(0.01)
        return SessionResponse(responses=[_message_content_text(messages[-1])])

    def close(self) -> None:
        self.closed = True


class _CachedOrchestrator:
    def invoke(self, *args: object, **kwargs: object) -> SessionResponse:
        _ = (args, kwargs)
        raise AssertionError("batch should not share the cached Agent orchestrator")
