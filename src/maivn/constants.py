"""Constants used by the maivn SDK.
Centralizes SDK configuration values and HTTP/client defaults.
Re-exports shared contract constants from ``maivn_shared`` when applicable.
"""

from __future__ import annotations

from maivn_shared import ServerEndpoints

# MARK: - SDK Configuration


class SDKConfig:
    """SDK configuration constants."""

    DEFAULT_TIMEOUT_SECONDS = 600.0
    MAX_RETRY_ATTEMPTS = 3
    RETRY_DELAY_SECONDS = 1.0


# MARK: - Client Constants


class ClientConstants:
    """Client-specific constants."""

    USER_AGENT_HEADER = "maivn-sdk"
    CONTENT_TYPE_HEADER = "application/json"


# MARK: - Exports

__all__ = [
    # Shared from maivn-core
    "ServerEndpoints",
    # SDK Internal
    "SDKConfig",
    "ClientConstants",
]
