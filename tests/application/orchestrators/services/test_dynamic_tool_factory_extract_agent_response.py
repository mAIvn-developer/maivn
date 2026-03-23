from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from maivn._internal.core.application_services.state_compilation import (
    dynamic_tool_factory,
)

# MARK: Tests


@dataclass
class _FakeResponse:
    result: Any = None
    responses: list[str] | None = None


def test_extract_agent_response_preserves_falsy_result_values() -> None:
    factory = dynamic_tool_factory.DynamicToolFactory()

    extracted = factory._extract_agent_response(_FakeResponse(result=0), "agent-1")
    assert extracted == {"result": 0}

    extracted = factory._extract_agent_response(_FakeResponse(result=False), "agent-1")
    assert extracted == {"result": False}

    extracted = factory._extract_agent_response(_FakeResponse(result=[]), "agent-1")
    assert extracted == {"result": []}

    extracted = factory._extract_agent_response(_FakeResponse(result={}), "agent-1")
    assert extracted == {"result": {}}

    extracted = factory._extract_agent_response(_FakeResponse(result=""), "agent-1")
    assert extracted == {"result": ""}


def test_extract_agent_response_preserves_falsy_response_text() -> None:
    factory = dynamic_tool_factory.DynamicToolFactory()

    # Empty response strings are stripped by _extract_latest_response_entry,
    # and a responses-only payload with no valid text returns None.
    extracted = factory._extract_agent_response(
        _FakeResponse(responses=[""]), "agent-1", include_response=True
    )
    assert extracted is None

    # A non-empty response is preserved
    extracted = factory._extract_agent_response(
        _FakeResponse(responses=["hello"]), "agent-1", include_response=True
    )
    assert extracted == {"result": None, "response": "hello"}


def test_extract_agent_response_includes_response_when_requested() -> None:
    factory = dynamic_tool_factory.DynamicToolFactory()

    extracted = factory._extract_agent_response(
        _FakeResponse(result={"ok": True}, responses=["final response"]),
        "agent-1",
        include_response=True,
    )
    assert extracted == {"result": {"ok": True}, "response": "final response"}


def test_extract_agent_response_skips_empty_response_when_requested() -> None:
    factory = dynamic_tool_factory.DynamicToolFactory()

    extracted = factory._extract_agent_response(
        _FakeResponse(result={"ok": True}, responses=[""]),
        "agent-1",
        include_response=True,
    )
    assert extracted == {"result": {"ok": True}}
