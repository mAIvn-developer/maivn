"""Shared configuration dictionary keys."""

# pyright: strict
from __future__ import annotations

from typing import Final

# MARK: Section Keys

SECTION_SERVER: Final = "server"
SECTION_EXECUTION: Final = "execution"
SECTION_SECURITY: Final = "security"
SECTION_LOGGING: Final = "logging"


# MARK: Server Fields

FIELD_BASE_URL: Final = "base_url"
FIELD_MOCK_BASE_URL: Final = "mock_base_url"
FIELD_TIMEOUT_SECONDS: Final = "timeout_seconds"
FIELD_MAX_RETRIES: Final = "max_retries"
FIELD_DEPLOYMENT_TIMEZONE: Final = "deployment_timezone"


# MARK: Execution Fields

FIELD_DEFAULT_TIMEOUT_SECONDS: Final = "default_timeout_seconds"
FIELD_PENDING_EVENT_TIMEOUT_SECONDS: Final = "pending_event_timeout_seconds"
FIELD_MAX_PARALLEL_TOOLS: Final = "max_parallel_tools"
FIELD_ENABLE_BACKGROUND_EXECUTION: Final = "enable_background_execution"
FIELD_TOOL_EXECUTION_TIMEOUT_SECONDS: Final = "tool_execution_timeout_seconds"
FIELD_DEPENDENCY_WAIT_TIMEOUT_SECONDS: Final = "dependency_wait_timeout_seconds"
FIELD_TOTAL_EXECUTION_TIMEOUT_SECONDS: Final = "total_execution_timeout_seconds"


# MARK: Security Fields

FIELD_API_KEY: Final = "api_key"
FIELD_REQUIRE_API_KEY: Final = "require_api_key"


# MARK: Logging Fields

FIELD_LOG_LEVEL: Final = "level"
FIELD_FORMAT_STRING: Final = "format_string"
FIELD_ENABLE_TIMING_LOGS: Final = "enable_timing_logs"
