from __future__ import annotations

from typing import Any

from maivn_shared import HumanMessage, SessionResponse
from pydantic import BaseModel

from maivn._internal.api.base_scope.builders import StructuredOutputInvocationBuilder


class _StructuredPayload(BaseModel):
    answer: str


class _ScopeStub:
    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    def invoke(self, **kwargs: Any) -> SessionResponse:
        self.calls.append(dict(kwargs))
        return SessionResponse(responses=["ok"])


def test_structured_output_builder_forwards_invocation_flags_and_metadata() -> None:
    scope = _ScopeStub()

    response = StructuredOutputInvocationBuilder(scope, _StructuredPayload).invoke(
        [HumanMessage(content="hello")],
        force_final_tool=True,
        model="balanced",
        reasoning="medium",
        thread_id="thread-1",
        verbose=True,
        memory_config={"level": "glimpse"},
        system_tools_config={"allowed_tools": []},
        orchestration_config={"max_cycles": 1},
        allow_private_in_system_tools=True,
    )

    assert response.responses == ["ok"]
    assert len(scope.calls) == 1
    call = scope.calls[0]
    assert call["structured_output"] is _StructuredPayload
    assert call["force_final_tool"] is True
    assert call["metadata"] is None
    assert call["memory_config"] == {"level": "glimpse"}
    assert call["system_tools_config"] == {"allowed_tools": []}
    assert call["orchestration_config"] == {"max_cycles": 1}
    assert call["allow_private_in_system_tools"] is True
