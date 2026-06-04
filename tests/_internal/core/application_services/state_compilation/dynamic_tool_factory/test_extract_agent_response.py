# pyright: strict
from __future__ import annotations

from dataclasses import dataclass, field

from maivn._internal.core.application_services.state_compilation import (
    dynamic_tool_factory,
)

# MARK: Helpers


@dataclass
class _FakeResponse:
    result: object = None
    responses: list[str] | None = field(default=None)


# MARK: Tests


def test_extract_agent_response_preserves_falsy_result_values() -> None:
    factory = dynamic_tool_factory.DynamicToolFactory()
    extract = factory.extract_agent_response

    assert extract(_FakeResponse(result=0), "agent-1") == {"result": 0}
    assert extract(_FakeResponse(result=False), "agent-1") == {"result": False}
    assert extract(_FakeResponse(result=[]), "agent-1") == {"result": []}
    assert extract(_FakeResponse(result={}), "agent-1") == {"result": {}}
    assert extract(_FakeResponse(result=""), "agent-1") == {"result": ""}


def test_extract_agent_response_preserves_falsy_response_text() -> None:
    factory = dynamic_tool_factory.DynamicToolFactory()
    extract = factory.extract_agent_response

    # Empty response strings are stripped by _extract_latest_response_entry,
    # and a responses-only payload with no valid text returns None.
    assert extract(_FakeResponse(responses=[""]), "agent-1", include_response=True) is None

    # A non-empty response is preserved
    assert extract(_FakeResponse(responses=["hello"]), "agent-1", include_response=True) == {
        "result": None,
        "response": "hello",
    }


def test_extract_agent_response_includes_response_when_requested() -> None:
    factory = dynamic_tool_factory.DynamicToolFactory()
    extract = factory.extract_agent_response

    extracted = extract(
        _FakeResponse(result={"ok": True}, responses=["final response"]),
        "agent-1",
        include_response=True,
    )
    assert extracted == {"result": {"ok": True}, "response": "final response"}


def test_extract_agent_response_skips_empty_response_when_requested() -> None:
    factory = dynamic_tool_factory.DynamicToolFactory()
    extract = factory.extract_agent_response

    extracted = extract(
        _FakeResponse(result={"ok": True}, responses=[""]),
        "agent-1",
        include_response=True,
    )
    assert extracted == {"result": {"ok": True}}
