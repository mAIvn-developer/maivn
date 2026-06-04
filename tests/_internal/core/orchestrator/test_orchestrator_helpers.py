# pyright: strict
from __future__ import annotations

from collections.abc import Sequence
from typing import cast

import pytest

from maivn._internal.core.entities import BaseTool
from maivn._internal.core.orchestrator.helpers import (
    OrchestratorConfig,
    coerce_tool_list,
    extract_latest_response,
    sanitize_user_facing_error_message,
)

_REDACTED = "An internal error occurred. Please try again."

# MARK: OrchestratorConfig


def test_orchestrator_config_stores_values() -> None:
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


def test_orchestrator_config_custom_defaults() -> None:
    cfg = OrchestratorConfig(
        http_timeout=10.0,
        execution_timeout=60.0,
        pending_event_timeout_s=2.0,
        max_retries=5,
        enable_background_execution=False,
    )
    assert cfg.max_retries == 5
    assert cfg.enable_background_execution is False


def test_orchestrator_config_is_frozen() -> None:
    cfg = OrchestratorConfig(
        http_timeout=10.0,
        execution_timeout=60.0,
        pending_event_timeout_s=2.0,
    )
    # Frozen dataclass blocks attribute assignment at runtime; use the builtin
    # `setattr` to route through the dataclass-installed `__setattr__` (which
    # raises AttributeError on frozen instances) without triggering pyright's
    # static read-only check on a direct attribute write.
    with pytest.raises(AttributeError):
        timeout_attr = "http_timeout"
        setattr(cfg, timeout_attr, 99.0)


# MARK: extract_latest_response


def test_extract_latest_response_returns_last_non_empty_string() -> None:
    assert extract_latest_response(["first", "second", "third"]) == "third"


def test_extract_latest_response_skips_empty_strings() -> None:
    assert extract_latest_response(["hello", "", "   "]) == "hello"


def test_extract_latest_response_strips_whitespace() -> None:
    assert extract_latest_response(["  padded  "]) == "padded"


def test_extract_latest_response_returns_none_for_non_list() -> None:
    assert extract_latest_response("not a list") is None
    assert extract_latest_response(42) is None
    assert extract_latest_response(None) is None
    assert extract_latest_response({"key": "value"}) is None


def test_extract_latest_response_returns_none_for_empty_list() -> None:
    assert extract_latest_response([]) is None


def test_extract_latest_response_returns_none_for_all_empty() -> None:
    assert extract_latest_response(["", "   ", ""]) is None


def test_extract_latest_response_skips_non_string_items() -> None:
    assert extract_latest_response([123, None, "valid"]) == "valid"


def test_extract_latest_response_returns_none_for_only_non_strings() -> None:
    assert extract_latest_response([123, None, 45.6]) is None


# MARK: coerce_tool_list


def test_coerce_tool_list_passes_through() -> None:
    tools = ["a", "b", "c"]
    # coerce_tool_list is a pass-through cast helper; test the runtime behavior
    # by casting the input to satisfy the typed signature.
    result = coerce_tool_list(cast(Sequence[BaseTool], tools))
    assert result is tools


def test_coerce_tool_list_empty() -> None:
    result = coerce_tool_list([])
    assert result == []


# MARK: sanitize_user_facing_error_message


def test_sanitize_preserves_private_data_message() -> None:
    msg = "LLM payload contains private data values that must be resolved"
    assert sanitize_user_facing_error_message(msg) == msg


def test_sanitize_preserves_private_data_message_case_insensitive() -> None:
    msg = "LLM Payload Contains Private Data Values in request"
    assert sanitize_user_facing_error_message(msg) == msg


def test_sanitize_strips_agent_execution_failed_prefix() -> None:
    msg = "Agent execution failed: Something went wrong"
    result = sanitize_user_facing_error_message(msg)
    assert result == "Something went wrong"


def test_sanitize_agent_execution_failed_no_detail() -> None:
    msg = "Agent execution failed:"
    result = sanitize_user_facing_error_message(msg)
    # Empty detail after colon, so prefix is not stripped
    assert result == msg


def test_sanitize_redacts_forward_slash_paths() -> None:
    msg = "Error in /home/user/project/file.py"
    assert sanitize_user_facing_error_message(msg) == _REDACTED


def test_sanitize_redacts_backslash_paths() -> None:
    msg = "Error in C:\\Users\\project\\file.py"
    assert sanitize_user_facing_error_message(msg) == _REDACTED


def test_sanitize_redacts_dot_md_references() -> None:
    msg = "See README.md for details"
    assert sanitize_user_facing_error_message(msg) == _REDACTED


def test_sanitize_redacts_maivn_underscore() -> None:
    msg = "Error in maivn_shared module"
    assert sanitize_user_facing_error_message(msg) == _REDACTED


def test_sanitize_redacts_importlib() -> None:
    msg = "importlib failed to load module"
    assert sanitize_user_facing_error_message(msg) == _REDACTED


def test_sanitize_redacts_langgraph() -> None:
    msg = "langgraph raised an exception"
    assert sanitize_user_facing_error_message(msg) == _REDACTED


def test_sanitize_redacts_traceback() -> None:
    msg = "Traceback (most recent call last):"
    assert sanitize_user_facing_error_message(msg) == _REDACTED


def test_sanitize_redacts_file_reference() -> None:
    msg = 'File "orchestrator.py", line 42'
    assert sanitize_user_facing_error_message(msg) == _REDACTED


def test_sanitize_redacts_double_backslash() -> None:
    msg = "Path is C:\\\\Users\\\\test"
    assert sanitize_user_facing_error_message(msg) == _REDACTED


def test_sanitize_redacts_windows_drive_letter() -> None:
    msg = "Located at D:\\Projects\\app"
    assert sanitize_user_facing_error_message(msg) == _REDACTED


def test_sanitize_passes_clean_message() -> None:
    msg = "Invalid input: expected a number"
    assert sanitize_user_facing_error_message(msg) == "Invalid input: expected a number"


def test_sanitize_passes_simple_error() -> None:
    msg = "Request timed out"
    assert sanitize_user_facing_error_message(msg) == "Request timed out"


def test_sanitize_agent_execution_failed_with_suspicious_detail() -> None:
    msg = "Agent execution failed: Error in /app/server.py"
    result = sanitize_user_facing_error_message(msg)
    assert result == _REDACTED


def test_sanitize_empty_string() -> None:
    assert sanitize_user_facing_error_message("") == ""
