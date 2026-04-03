from __future__ import annotations

from maivn.events._models import NormalizedStreamState
from maivn.events._normalize.context import NormalizationOptions
from maivn.events._normalize.tool_events import handle_tool_event


def test_handle_tool_event_prefers_explicit_tool_id_over_matching_name() -> None:
    payload = {
        "id": "evt-1",
        "value": {
            "tool_call": {
                "tool_id": "repl",
                "name": "repl",
                "args": {"code": "1+1"},
            }
        },
    }

    normalized = handle_tool_event(payload, NormalizedStreamState(), NormalizationOptions())

    assert normalized[0]["tool_id"] == "repl"
    assert normalized[0]["tool"]["id"] == "repl"


def test_handle_tool_event_builds_composite_id_for_name_only_tools() -> None:
    payload = {
        "id": "evt-1",
        "value": {
            "tool_call": {
                "name": "repl",
                "args": {"code": "1+1"},
            }
        },
    }

    normalized = handle_tool_event(payload, NormalizedStreamState(), NormalizationOptions())

    assert normalized[0]["tool_id"] == "evt-1:0:repl"
    assert normalized[0]["tool"]["id"] == "evt-1:0:repl"
