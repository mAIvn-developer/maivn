from __future__ import annotations

from maivn._internal.core.application_services.events.event_handlers import (
    extract_tool_call_payload,
)


def test_extract_tool_call_payload_maps_user_data_injected_to_private_data_injected() -> None:
    payload = extract_tool_call_payload(
        {
            "tool_call": {"tool_id": "tool-1", "args": {"x": 1}},
            "user_data_injected": ["email", "ssn"],
            "interrupt_data_injected": {"prompt": "confirm"},
        }
    )

    assert payload["tool_id"] == "tool-1"
    assert payload["args"] == {"x": 1}
    assert payload["private_data_injected"] == ["email", "ssn"]
    assert payload["interrupt_data_injected"] == {"prompt": "confirm"}


def test_extract_tool_call_payload_prefers_private_data_injected_when_both_present() -> None:
    payload = extract_tool_call_payload(
        {
            "tool_call": {"tool_id": "tool-1"},
            "private_data_injected": ["a"],
            "user_data_injected": ["b"],
        }
    )

    assert payload["private_data_injected"] == ["a"]


def test_extract_tool_call_payload_uses_user_data_injected_when_private_data_is_none() -> None:
    payload = extract_tool_call_payload(
        {
            "tool_call": {"tool_id": "tool-1"},
            "private_data_injected": None,
            "user_data_injected": ["legacy"],
        }
    )

    assert payload["private_data_injected"] is None
