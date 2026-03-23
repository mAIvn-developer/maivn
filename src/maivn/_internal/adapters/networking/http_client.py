"""HTTP client wrapper using maivn-core shared implementation.

This module provides the HTTP client implementation using the shared
maivn-core HttpClient for consistent behavior across services.
"""

from __future__ import annotations

from maivn_shared import HttpClient as CoreHttpClient
from maivn_shared import HttpError

from maivn._internal.utils.configuration import get_configuration

# MARK: - HttpClient


class HttpClient(CoreHttpClient):
    """HTTP client implementation using maivn-core shared client.

    This class extends the core HttpClient with SDK-specific configuration.
    """

    # MARK: - Initialization

    def __init__(self, *, timeout: float | None = None) -> None:
        """Initialize the HTTP client with SDK configuration.

        Args:
            timeout: Default timeout for requests
        """
        config = get_configuration()
        sdk_timeout = timeout if timeout is not None else config.server.timeout_seconds
        super().__init__(timeout=sdk_timeout)


# MARK: - Exports

__all__ = [
    "HttpClient",
    "HttpError",
]
