from __future__ import annotations

import pytest

from maivn._internal.core.orchestrator.helpers import (
    OrchestratorConfig,
    coerce_tool_list,
    extract_latest_response,
    sanitize_user_facing_error_message,
)

_REDACTED = "An internal error occurred. Please try again."

# MARK: OrchestratorConfig


def test_orchestrator_config_stores_values():
    cfg = OrchestratorConfig(
        http_timeout=30.0,
        execution_timeout=120.0,
        pending_event_timeout_s=5.0,
    )
    assert cfg.http_timeout == 30.0
    assert cfg.execution_timeout == 120.0
    assert cfg.pending_event_timeout_s == 5.0
    assert cfg.max_retries == 3
    assert cfg.enable_background_execution is True


def test_orchestrator_config_custom_defaults():
    cfg = OrchestratorConfig(
        http_timeout=10.0,
        execution_timeout=60.0,
        pending_event_timeout_s=2.0,
        max_retries=5,
        enable_background_execution=False,
    )
    assert cfg.max_retries == 5
    assert cfg.enable_background_execution is False


def test_orchestrator_config_is_frozen():
    cfg = OrchestratorConfig(
        http_timeout=10.0,
        execution_timeout=60.0,
        pending_event_timeout_s=2.0,
    )
    with pytest.raises(AttributeError):
        cfg.http_timeout = 99.0  # type: ignore[misc]


# MARK: extract_latest_response


def test_extract_latest_response_returns_last_non_empty_string():
    assert extract_latest_response(["first", "second", "third"]) == "third"


def test_extract_latest_response_skips_empty_strings():
    assert extract_latest_response(["hello", "", "   "]) == "hello"


def test_extract_latest_response_strips_whitespace():
    assert extract_latest_response(["  padded  "]) == "padded"


def test_extract_latest_response_returns_none_for_non_list():
    assert extract_latest_response("not a list") is None
    assert extract_latest_response(42) is None
    assert extract_latest_response(None) is None
    assert extract_latest_response({"key": "value"}) is None


def test_extract_latest_response_returns_none_for_empty_list():
    assert extract_latest_response([]) is None


def test_extract_latest_response_returns_none_for_all_empty():
    assert extract_latest_response(["", "   ", ""]) is None


def test_extract_latest_response_skips_non_string_items():
    assert extract_latest_response([123, None, "valid"]) == "valid"


def test_extract_latest_response_returns_none_for_only_non_strings():
    assert extract_latest_response([123, None, 45.6]) is None


# MARK: coerce_tool_list


def test_coerce_tool_list_passes_through():
    tools = ["a", "b", "c"]
    result = coerce_tool_list(tools)
    assert result is tools


def test_coerce_tool_list_empty():
    result = coerce_tool_list([])
    assert result == []


# MARK: sanitize_user_facing_error_message


def test_sanitize_preserves_private_data_message():
    msg = "LLM payload contains private data values that must be resolved"
    assert sanitize_user_facing_error_message(msg) == msg


def test_sanitize_preserves_private_data_message_case_insensitive():
    msg = "LLM Payload Contains Private Data Values in request"
    assert sanitize_user_facing_error_message(msg) == msg


def test_sanitize_strips_agent_execution_failed_prefix():
    msg = "Agent execution failed: Something went wrong"
    result = sanitize_user_facing_error_message(msg)
    assert result == "Something went wrong"


def test_sanitize_agent_execution_failed_no_detail():
    msg = "Agent execution failed:"
    result = sanitize_user_facing_error_message(msg)
    # Empty detail after colon, so prefix is not stripped
    assert result == msg


def test_sanitize_redacts_forward_slash_paths():
    msg = "Error in /home/user/project/file.py"
    assert sanitize_user_facing_error_message(msg) == _REDACTED


def test_sanitize_redacts_backslash_paths():
    msg = "Error in C:\\Users\\project\\file.py"
    assert sanitize_user_facing_error_message(msg) == _REDACTED


def test_sanitize_redacts_dot_md_references():
    msg = "See README.md for details"
    assert sanitize_user_facing_error_message(msg) == _REDACTED


def test_sanitize_redacts_maivn_underscore():
    msg = "Error in maivn_shared module"
    assert sanitize_user_facing_error_message(msg) == _REDACTED


def test_sanitize_redacts_importlib():
    msg = "importlib failed to load module"
    assert sanitize_user_facing_error_message(msg) == _REDACTED


def test_sanitize_redacts_langgraph():
    msg = "langgraph raised an exception"
    assert sanitize_user_facing_error_message(msg) == _REDACTED


def test_sanitize_redacts_traceback():
    msg = "Traceback (most recent call last):"
    assert sanitize_user_facing_error_message(msg) == _REDACTED


def test_sanitize_redacts_file_reference():
    msg = 'File "orchestrator.py", line 42'
    assert sanitize_user_facing_error_message(msg) == _REDACTED


def test_sanitize_redacts_double_backslash():
    msg = "Path is C:\\\\Users\\\\test"
    assert sanitize_user_facing_error_message(msg) == _REDACTED


def test_sanitize_redacts_windows_drive_letter():
    msg = "Located at D:\\Projects\\app"
    assert sanitize_user_facing_error_message(msg) == _REDACTED


def test_sanitize_passes_clean_message():
    msg = "Invalid input: expected a number"
    assert sanitize_user_facing_error_message(msg) == "Invalid input: expected a number"


def test_sanitize_passes_simple_error():
    msg = "Request timed out"
    assert sanitize_user_facing_error_message(msg) == "Request timed out"


def test_sanitize_agent_execution_failed_with_suspicious_detail():
    msg = "Agent execution failed: Error in /app/server.py"
    result = sanitize_user_facing_error_message(msg)
    assert result == _REDACTED


def test_sanitize_empty_string():
    assert sanitize_user_facing_error_message("") == ""
