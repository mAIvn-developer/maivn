# pyright: strict
from __future__ import annotations

from typing import cast

from pydantic import JsonValue

from maivn.events._models import NormalizedStreamState
from maivn.events._normalize.context import NormalizationOptions
from maivn.events._normalize.tool_events import handle_tool_event

# JsonObject mirrors the alias used by the SUT in src/maivn/events/_normalize.
JsonObject = dict[str, JsonValue]


def _tool(normalized: list[JsonObject]) -> JsonObject:
    """Narrow ``normalized[0]['tool']`` to a ``JsonObject`` for typed indexing."""
    return cast(JsonObject, normalized[0]["tool"])


def test_handle_tool_event_prefers_explicit_tool_id_over_matching_name() -> None:
    payload: JsonObject = {
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
    assert _tool(normalized)["id"] == "repl"


def test_handle_tool_event_builds_composite_id_for_name_only_tools() -> None:
    payload: JsonObject = {
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
    assert _tool(normalized)["id"] == "evt-1:0:repl"
