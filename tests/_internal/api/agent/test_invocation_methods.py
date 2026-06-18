# pyright: strict
from __future__ import annotations

from collections.abc import Iterator, Sequence
from typing import Literal, cast

import pytest
from maivn_shared import (
    BaseMessage,
    HumanMessage,
    MemoryAssetsConfig,
    MemoryConfig,
    SessionOrchestrationConfig,
    SessionRequest,
    SessionResponse,
    SwarmConfig,
    SystemToolsConfig,
)
from pydantic import BaseModel, ValidationError

from maivn._internal.api.agent import Agent
from maivn._internal.api.client import Client
from maivn._internal.core.entities.sse_event import SSEEvent
from maivn._internal.utils.configuration import MaivnConfiguration, ServerConfiguration


class _StructuredPayload(BaseModel):
    """Return the structured answer."""

    answer: str


class _OtherPayload(BaseModel):
    """Return a different structured answer."""

    title: str


class _StubOrchestrator:
    def __init__(self, response: SessionResponse) -> None:
        self.response = response

    def compile_state(
        self,
        messages: Sequence[BaseMessage],
        force_final_tool: bool = False,
        targeted_tools: list[str] | None = None,
        structured_output: type[BaseModel] | None = None,
        model: Literal["auto", "fast", "balanced", "max"] | None = None,
        force_model: str | None = None,
        reasoning: Literal["minimal", "low", "medium", "high"] | None = None,
        stream_response: bool = True,
        status_messages: bool = False,
        thread_id: str | None = None,
        metadata: dict[str, object] | None = None,
        memory_config: MemoryConfig | None = None,
        system_tools_config: SystemToolsConfig | None = None,
        orchestration_config: SessionOrchestrationConfig | None = None,
        memory_assets_config: MemoryAssetsConfig | None = None,
        swarm_config: SwarmConfig | None = None,
    ) -> SessionRequest:
        _ = (
            messages,
            force_final_tool,
            targeted_tools,
            structured_output,
            model,
            force_model,
            reasoning,
            stream_response,
            status_messages,
            thread_id,
            metadata,
            memory_config,
            system_tools_config,
            orchestration_config,
            memory_assets_config,
            swarm_config,
        )
        return SessionRequest()

    def invoke(
        self,
        messages: Sequence[BaseMessage],
        force_final_tool: bool = False,
        targeted_tools: list[str] | None = None,
        structured_output: type[BaseModel] | None = None,
        model: Literal["auto", "fast", "balanced", "max"] | None = None,
        force_model: str | None = None,
        reasoning: Literal["minimal", "low", "medium", "high"] | None = None,
        stream_response: bool = True,
        thread_id: str | None = None,
        verbose: bool = False,
        metadata: dict[str, object] | None = None,
        memory_config: MemoryConfig | None = None,
        system_tools_config: SystemToolsConfig | None = None,
        orchestration_config: SessionOrchestrationConfig | None = None,
        memory_assets_config: MemoryAssetsConfig | None = None,
        swarm_config: SwarmConfig | None = None,
    ) -> SessionResponse:
        _ = (
            messages,
            force_final_tool,
            targeted_tools,
            structured_output,
            model,
            force_model,
            reasoning,
            stream_response,
            thread_id,
            verbose,
            metadata,
            memory_config,
            system_tools_config,
            orchestration_config,
            memory_assets_config,
            swarm_config,
        )
        return self.response

    def stream(
        self,
        messages: Sequence[BaseMessage],
        force_final_tool: bool = False,
        targeted_tools: list[str] | None = None,
        model: Literal["auto", "fast", "balanced", "max"] | None = None,
        force_model: str | None = None,
        reasoning: Literal["minimal", "low", "medium", "high"] | None = None,
        stream_response: bool = True,
        status_messages: bool = False,
        thread_id: str | None = None,
        verbose: bool = False,
        metadata: dict[str, object] | None = None,
        memory_config: MemoryConfig | None = None,
        system_tools_config: SystemToolsConfig | None = None,
        orchestration_config: SessionOrchestrationConfig | None = None,
        memory_assets_config: MemoryAssetsConfig | None = None,
        swarm_config: SwarmConfig | None = None,
    ) -> Iterator[SSEEvent]:
        _ = (
            messages,
            force_final_tool,
            targeted_tools,
            model,
            force_model,
            reasoning,
            stream_response,
            status_messages,
            thread_id,
            verbose,
            metadata,
            memory_config,
            system_tools_config,
            orchestration_config,
            memory_assets_config,
            swarm_config,
        )
        return iter(())

    def invoke_compiled_state(
        self,
        state: SessionRequest,
        *,
        thread_id: str | None = None,
        verbose: bool = False,
        compilation_elapsed_s: float | None = None,
    ) -> SessionResponse:
        _ = state, thread_id, verbose, compilation_elapsed_s
        return self.response

    def stream_compiled_state(
        self,
        state: SessionRequest,
        *,
        thread_id: str | None = None,
        verbose: bool = False,
        compilation_elapsed_s: float | None = None,
    ) -> Iterator[SSEEvent]:
        _ = state, thread_id, verbose, compilation_elapsed_s
        return iter(())

    def register_swarm_agent_tools(self, agent_tools: list[object]) -> None:
        _ = agent_tools

    def close(self) -> None:
        return


def _make_client() -> Client:
    config = MaivnConfiguration(
        server=ServerConfiguration(
            base_url="http://example.com",
            mock_base_url="http://example.com",
        )
    )
    return Client.from_configuration(api_key="key", configuration=config)


def _agent_with_response(response: SessionResponse) -> Agent:
    agent = Agent(name="typed-agent", client=_make_client())
    agent.__setattr__("_orchestrator", _StubOrchestrator(response))
    return agent


def test_structured_output_invocation_coerces_result_to_requested_model() -> None:
    agent = _agent_with_response(SessionResponse(result={"answer": "typed"}, responses=["done"]))

    response = agent.invoke(
        [HumanMessage(content="Return a typed result")],
        structured_output=_StructuredPayload,
    )

    assert response.responses == ["done"]
    assert response.result == _StructuredPayload(answer="typed")


def test_structured_output_invocation_raises_when_result_does_not_match_model() -> None:
    agent = _agent_with_response(SessionResponse(result={"title": "wrong"}))

    with pytest.raises(ValidationError):
        agent.invoke(
            [HumanMessage(content="Return a typed result")],
            structured_output=_StructuredPayload,
        )


def test_structured_output_invocation_validation_error_has_nested_result_hint() -> None:
    agent = _agent_with_response(SessionResponse(result={"payload": {"answer": "typed"}}))

    with pytest.raises(ValidationError) as exc_info:
        agent.invoke(
            [HumanMessage(content="Return a typed result")],
            structured_output=_StructuredPayload,
        )

    notes = cast(list[str], getattr(exc_info.value, "__notes__", []))
    assert any(
        "Structured output validation failed for _StructuredPayload" in note for note in notes
    )
    assert any("payload" in note and "answer" in note and "top-level" in note for note in notes)


def test_force_final_tool_coerces_result_to_registered_final_model_best_effort() -> None:
    agent = _agent_with_response(SessionResponse(result={"answer": "typed"}))
    _ = agent.add_tool(_StructuredPayload, final_tool=True)

    response = agent.invoke(
        [HumanMessage(content="Return the final tool")],
        force_final_tool=True,
    )

    assert response.result == _StructuredPayload(answer="typed")


def test_force_final_tool_keeps_raw_result_when_registered_model_does_not_validate() -> None:
    raw_result = {"answer": "not the final model"}
    agent = _agent_with_response(SessionResponse(result=raw_result))
    _ = agent.add_tool(_OtherPayload, final_tool=True)

    response = agent.invoke(
        [HumanMessage(content="Return the final tool")],
        force_final_tool=True,
    )

    assert cast(object, response.result) == raw_result
