"""MCP server registration and client helpers for the maivn SDK."""

from __future__ import annotations

import time
import warnings
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, PrivateAttr, field_validator

from .auto import MCPAutoSetup
from .clients import _McpClientBase, _McpHttpClient, _McpStdioClient
from .retry import MCPSoftErrorHandling, _find_soft_error_message, _RateLimiter
from .tools import (
    _DEFAULT_CLIENT_NAME,
    _DEFAULT_CLIENT_TITLE,
    _DEFAULT_CLIENT_VERSION,
    _DEFAULT_PROTOCOL_VERSION,
    MCPToolDefinition,
    sanitize_identifier,
)

# MARK: MCPServer


class MCPServer(BaseModel):
    """Pydantic model for MCP server registration.

    This class represents a connection to an MCP server, supporting both
    HTTP and stdio transports. It handles tool discovery, rate limiting,
    and tool invocation.
    """

    model_config = ConfigDict(
        arbitrary_types_allowed=True,
        validate_assignment=True,
        populate_by_name=True,
    )

    name: str = Field(..., description="Logical name of the MCP server")
    transport: Literal["http", "stdio"] = Field(
        default="stdio",
        description="Transport protocol for MCP server communication",
    )
    url: str | None = Field(default=None, description="HTTP endpoint for MCP server")
    command: str | None = Field(default=None, description="Command to launch stdio MCP server")
    args: list[str] = Field(default_factory=list, description="Arguments for stdio MCP server")
    env: dict[str, str] | None = Field(default=None, description="Environment variables")
    inherit_env: bool = Field(
        default=True,
        description=(
            "Whether a stdio MCP server inherits the parent process environment. "
            "Disable or combine with inherit_env_allowlist for tighter control."
        ),
    )
    inherit_env_allowlist: list[str] | None = Field(
        default=None,
        description=(
            "Optional parent environment variable names to inherit. When set, only "
            "these variables plus a minimal runtime baseline are inherited."
        ),
    )
    working_dir: str | None = Field(default=None, description="Working directory for stdio server")
    headers: dict[str, str] | None = Field(default=None, description="HTTP headers")
    protocol_version: str = Field(default=_DEFAULT_PROTOCOL_VERSION)
    client_name: str = Field(default=_DEFAULT_CLIENT_NAME)
    client_title: str = Field(default=_DEFAULT_CLIENT_TITLE)
    client_version: str = Field(default=_DEFAULT_CLIENT_VERSION)
    tool_name_prefix: str | None = Field(
        default=None, description="Optional prefix for MCP tool names"
    )
    tool_name_separator: str = Field(default="__", description="Separator for MCP tool names")
    default_tool_args: dict[str, Any] | None = Field(
        default=None,
        description="Default arguments applied to all MCP tools",
    )
    tool_defaults: dict[str, dict[str, Any]] | None = Field(
        default=None,
        description="Per-tool default arguments keyed by MCP tool name",
    )
    max_calls_per_minute: int | None = Field(
        default=None,
        description="Optional rate limit (calls per minute)",
    )
    max_calls_per_day: int | None = Field(
        default=None,
        description="Optional rate limit (calls per day)",
    )
    request_timeout_seconds: float | None = Field(
        default=None, description="HTTP request timeout override"
    )
    stdio_response_timeout_seconds: float | None = Field(
        default=None,
        description="Timeout for stdio MCP responses (None = no timeout)",
    )
    raise_on_tool_error: bool = Field(
        default=False,
        description="Raise when MCP tools return isError=True",
    )
    auto_setup: MCPAutoSetup | None = Field(
        default=None,
        description="Optional auto-setup instructions for stdio MCP servers",
    )
    soft_error_handling: MCPSoftErrorHandling | None = Field(
        default=None,
        description=(
            "Optional detection + retry/backoff for providers that return errors "
            "inside JSON payloads (e.g., rate-limit notes with HTTP 200)."
        ),
    )

    _client: _McpClientBase | None = PrivateAttr(default=None)
    _rate_limiters: list[_RateLimiter] = PrivateAttr(default_factory=list)

    # MARK: - Validators

    @field_validator("name")
    @classmethod
    def _validate_name(cls, value: str) -> str:
        if not value or not isinstance(value, str):
            raise ValueError("MCPServer name must be a non-empty string")
        return value

    @field_validator("tool_name_separator")
    @classmethod
    def _validate_separator(cls, value: str) -> str:
        if not value:
            raise ValueError("tool_name_separator must be a non-empty string")
        return value

    @field_validator("transport")
    @classmethod
    def _normalize_transport(cls, value: str) -> str:
        normalized = value.strip().lower()
        if normalized not in {"http", "stdio"}:
            raise ValueError("transport must be 'http' or 'stdio'")
        return normalized

    @field_validator("max_calls_per_minute", "max_calls_per_day")
    @classmethod
    def _validate_rate_limits(cls, value: int | None) -> int | None:
        if value is None:
            return value
        if value <= 0:
            raise ValueError("Rate limits must be positive integers")
        return value

    @field_validator("inherit_env_allowlist")
    @classmethod
    def _normalize_inherit_env_allowlist(cls, value: list[str] | None) -> list[str] | None:
        if value is None:
            return value

        normalized: list[str] = []
        seen: set[str] = set()
        for item in value:
            if not isinstance(item, str) or not item.strip():
                raise ValueError("inherit_env_allowlist entries must be non-empty strings")
            candidate = item.strip()
            if candidate not in seen:
                normalized.append(candidate)
                seen.add(candidate)
        return normalized

    # MARK: - Initialization

    def model_post_init(self, __context: Any) -> None:
        if self.transport == "http" and not self.url:
            raise ValueError("HTTP MCPServer requires a url")
        if self.auto_setup and self.transport != "stdio":
            raise ValueError("auto_setup is only supported for stdio MCP servers")
        if self.transport == "stdio":
            if self.auto_setup:
                self._apply_auto_setup()
            if not self.command:
                raise ValueError("STDIO MCPServer requires a command or auto_setup")
            if self.stdio_response_timeout_seconds is None:
                warnings.warn(
                    (
                        "MCPServer transport='stdio' has no "
                        "stdio_response_timeout_seconds configured; this can wait "
                        "indefinitely on stalled responses."
                    ),
                    RuntimeWarning,
                    stacklevel=2,
                )

        self._rate_limiters = []
        if self.max_calls_per_minute:
            self._rate_limiters.append(_RateLimiter(self.max_calls_per_minute, 60.0))
        if self.max_calls_per_day:
            self._rate_limiters.append(_RateLimiter(self.max_calls_per_day, 86400.0))
        super().model_post_init(__context)

    def _apply_auto_setup(self) -> None:
        if not self.auto_setup:
            return

        needs_command = not self.command
        needs_args = not self.args
        if needs_command or needs_args:
            command, args = self.auto_setup.resolve_command()
            if needs_command:
                self.command = command
            if needs_args:
                self.args = list(args)

        if self.auto_setup.env:
            merged_env = dict(self.auto_setup.env)
            if self.env:
                merged_env.update(self.env)
            self.env = merged_env

        if self.working_dir is None and self.auto_setup.working_dir:
            self.working_dir = self.auto_setup.working_dir

    # MARK: - Tool Name Utilities

    def build_tool_name(self, mcp_tool_name: str) -> str:
        """Build a full tool name with optional prefix."""
        prefix = self.tool_name_prefix if self.tool_name_prefix is not None else self.name
        if prefix:
            raw = f"{prefix}{self.tool_name_separator}{mcp_tool_name}"
        else:
            raw = mcp_tool_name
        return sanitize_identifier(raw)

    def resolve_tool_defaults(self, mcp_tool_name: str) -> dict[str, Any]:
        """Resolve default arguments for a specific tool."""
        defaults: dict[str, Any] = {}
        if isinstance(self.default_tool_args, dict):
            defaults.update(self.default_tool_args)
        if isinstance(self.tool_defaults, dict):
            tool_defaults = self.tool_defaults.get(mcp_tool_name)
            if isinstance(tool_defaults, dict):
                defaults.update(tool_defaults)
        return defaults

    # MARK: - Public Methods

    def list_tools(self) -> list[MCPToolDefinition]:
        """List all tools available from this MCP server."""
        client = self._get_client()
        return client.list_tools()

    def call_tool(self, tool_name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        """Call a tool on this MCP server."""
        client = self._get_client()

        attempts = 0
        max_attempts = 1
        soft_error_cfg = self.soft_error_handling
        if soft_error_cfg is not None and soft_error_cfg.enabled:
            max_attempts = 1 + soft_error_cfg.max_retries

        backoff_seconds = soft_error_cfg.initial_backoff_seconds if soft_error_cfg else 0.0
        soft_error_keys = set(soft_error_cfg.keys) if soft_error_cfg else set()

        while True:
            self._apply_rate_limits()
            result = client.call_tool(tool_name, arguments)
            normalized = self._normalize_tool_result(result)

            structured = normalized.get("structured_content")
            soft_error_message = (
                _find_soft_error_message(structured, soft_error_keys)
                if soft_error_cfg is not None and soft_error_cfg.enabled
                else None
            )

            if soft_error_message:
                normalized["is_error"] = True
                normalized["soft_error"] = {
                    "message": soft_error_message,
                    "keys": list(soft_error_keys),
                }

                attempts += 1
                if attempts < max_attempts:
                    limiter_wait = 0.0
                    if self._rate_limiters:
                        limiter_wait = max(
                            limiter.peek_wait_seconds() for limiter in self._rate_limiters
                        )

                    wait_for = max(limiter_wait, backoff_seconds)
                    time.sleep(wait_for)
                    if soft_error_cfg is not None:
                        backoff_seconds = min(
                            backoff_seconds * 2.0,
                            soft_error_cfg.max_backoff_seconds,
                        )
                    continue

            if normalized.get("is_error") and self.raise_on_tool_error:
                raise ValueError(f"MCP tool '{tool_name}' returned an error")
            return normalized

    def close(self) -> None:
        """Close the MCP client connection."""
        if self._client is None:
            return
        self._client.close()
        self._client = None

    # MARK: - Private Methods

    def _apply_rate_limits(self) -> None:
        for limiter in self._rate_limiters:
            limiter.acquire()

    def _get_client(self) -> _McpClientBase:
        if self._client is not None:
            return self._client
        if self.transport == "http":
            self._client = _McpHttpClient(self)
        else:
            self._client = _McpStdioClient(self)
        return self._client

    @staticmethod
    def _normalize_tool_result(result: dict[str, Any]) -> dict[str, Any]:
        is_error = bool(result.get("isError") or result.get("is_error"))
        content = result.get("content")
        structured = result.get("structuredContent") or result.get("structured_content")

        payload: dict[str, Any] = {"is_error": is_error}
        if content is not None:
            payload["content"] = content
        if structured is not None:
            payload["structured_content"] = structured
        return payload


__all__ = ["MCPAutoSetup", "MCPServer", "MCPSoftErrorHandling", "MCPToolDefinition"]
