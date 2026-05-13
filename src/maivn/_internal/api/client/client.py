"""HTTP client helpers for the maivn SDK.
Defines the reusable ``Client`` and ``ClientBuilder`` used by agents and swarms.
"""

from __future__ import annotations

import uuid
from collections.abc import Callable
from typing import Any

try:
    from tzlocal import get_localzone_name
except Exception:  # pragma: no cover
    get_localzone_name = None  # type: ignore[assignment]

from maivn_shared import RedactionPreviewRequest, RedactionPreviewResponse

from maivn._internal.utils.configuration import (
    ConfigurationBuilder,
    MaivnConfiguration,
    get_configuration,
)
from maivn.constants import ServerEndpoints

from .http import ClientHttpMixin
from .memory import ClientMemoryMixin

# MARK: Client


class Client(ClientHttpMixin, ClientMemoryMixin):
    """Reusable SDK client for connecting multiple agents to maivn-server.

    The client acts as a lightweight connection helper that SDK consumers can create
    once and reuse across many ``Agent`` or ``Swarm`` instances. It manages
    authentication (API key), connection reuse, and thread identifiers while keeping
    server connection details internal to the SDK.
    """

    # MARK: - Initialization

    def __init__(
        self,
        api_key: str | None = None,
        *,
        client_timezone: str | None = None,
        auto_detect_timezone: bool = True,
        timeout: int | float | None = None,
        thread_id: str | None = None,
        tool_execution_timeout: float | None = None,
        dependency_wait_timeout: float | None = None,
        total_execution_timeout: float | None = None,
    ) -> None:
        """Initialize the Client.

        Args:
            api_key: API key for authentication (required for server calls)
            client_timezone: Optional IANA time zone identifier.
            auto_detect_timezone: When True and client_timezone is not provided,
                detect the local system timezone automatically.
            timeout: Request timeout in seconds (HTTP request timeout)
            thread_id: Optional thread ID to use
            tool_execution_timeout: Per-tool execution timeout in seconds.
            dependency_wait_timeout: Dependency resolution timeout in seconds.
            total_execution_timeout: Total execution timeout in seconds.
        """
        self._api_key = api_key
        self._client_timezone = client_timezone
        if self._client_timezone is None and auto_detect_timezone:
            self._client_timezone = self._detect_system_timezone()
        self._timeout = timeout
        self._thread_id: str | None = None
        self._configuration: MaivnConfiguration | None = None
        self._configuration_provider: Callable[[], MaivnConfiguration] = get_configuration
        self._http_client = None
        self._owns_http_client = True

        self._tool_execution_timeout = tool_execution_timeout
        self._dependency_wait_timeout = dependency_wait_timeout
        self._total_execution_timeout = total_execution_timeout

        self._initialize_from_configuration()

        if thread_id:
            self.set_thread_id(thread_id)

    @staticmethod
    def _detect_system_timezone() -> str | None:
        if get_localzone_name is None:
            return None
        try:
            value = get_localzone_name()
            if isinstance(value, str) and value.strip():
                return value
        except Exception:  # noqa: BLE001 - tzlocal raises a variety of OS errors
            return None
        return None

    def _initialize_from_configuration(self) -> None:
        """Initialize base URLs and timeout from configuration."""
        config = self._resolve_configuration()
        self._base_url = config.server.base_url
        self._mock_base_url = config.server.mock_base_url
        self._deployment_timezone = getattr(config.server, "deployment_timezone", "UTC")
        if self._timeout is None:
            self._timeout = config.server.timeout_seconds

    # MARK: - Factory Methods

    @classmethod
    def from_configuration(
        cls,
        *,
        api_key: str | None,
        configuration: MaivnConfiguration,
        configuration_provider: Callable[[], MaivnConfiguration] | None = None,
        http_client: Any = None,
        timeout: int | float | None = None,
        thread_id: str | None = None,
        tool_execution_timeout: float | None = None,
        dependency_wait_timeout: float | None = None,
        total_execution_timeout: float | None = None,
    ) -> Client:
        """Internal factory that allows SDK components to inject configuration."""
        instance = cls.__new__(cls)

        instance._api_key = api_key
        instance._client_timezone = cls._detect_system_timezone()
        instance._timeout = timeout
        instance._thread_id = None
        instance._configuration = configuration
        instance._configuration_provider = configuration_provider or (lambda: configuration)
        instance._http_client = http_client
        instance._owns_http_client = http_client is None

        instance._tool_execution_timeout = tool_execution_timeout
        instance._dependency_wait_timeout = dependency_wait_timeout
        instance._total_execution_timeout = total_execution_timeout

        instance._initialize_from_configuration()

        if thread_id:
            instance.set_thread_id(thread_id)

        return instance

    # MARK: - Properties

    @property
    def api_key(self) -> str | None:
        return self._api_key

    @property
    def timeout(self) -> float | None:
        if self._timeout is None:
            return None
        return float(self._timeout)

    @property
    def client_timezone(self) -> str | None:
        return self._client_timezone

    @property
    def deployment_timezone(self) -> str:
        return str(getattr(self, "_deployment_timezone", "UTC"))

    @property
    def thread_id(self) -> str | None:
        return self._thread_id

    @property
    def base_url(self) -> str:
        """Read-only server API base URL derived from configuration."""
        return self._base_url

    @property
    def mock_base_url(self) -> str:
        """Read-only mock server base URL derived from configuration."""
        return self._mock_base_url

    # MARK: - Thread Management

    def set_thread_id(self, thread_id: str) -> None:
        """Set the thread id."""
        if not isinstance(thread_id, str) or not thread_id.strip():
            raise ValueError("thread_id must be a non-empty string")
        self._thread_id = thread_id

    def new_thread_id(self) -> str:
        """Generate and set a new UUID4 thread id; return it."""
        tid = str(uuid.uuid4())
        self._thread_id = tid
        return tid

    def get_thread_id(self, create_if_missing: bool = False) -> str | None:
        """Return the current thread id."""
        if self._thread_id:
            return self._thread_id
        if create_if_missing:
            return self.new_thread_id()
        return None

    # MARK: - Timeout Resolution

    def get_tool_execution_timeout(self) -> float:
        """Get effective tool execution timeout."""
        if self._tool_execution_timeout is not None:
            return self._tool_execution_timeout
        return self._resolve_configuration().execution.tool_execution_timeout_seconds

    def get_dependency_wait_timeout(self) -> float:
        """Get effective dependency wait timeout."""
        if self._dependency_wait_timeout is not None:
            return self._dependency_wait_timeout
        return self._resolve_configuration().execution.dependency_wait_timeout_seconds

    def get_total_execution_timeout(self) -> float | None:
        """Get effective total execution timeout."""
        if self._total_execution_timeout is not None:
            return self._total_execution_timeout
        return self._resolve_configuration().execution.total_execution_timeout_seconds

    # MARK: - Session Operations

    def start_session(self, *, payload: dict) -> dict:
        """Start a session via maivn-server and return response JSON."""
        return self._request("POST", ServerEndpoints.START_SESSION, json=payload)

    def preview_redaction(
        self,
        *,
        payload: RedactionPreviewRequest | dict[str, Any],
    ) -> RedactionPreviewResponse:
        request_payload = (
            payload.model_dump(mode="json", exclude_none=True)
            if isinstance(payload, RedactionPreviewRequest)
            else dict(payload)
        )
        response_payload = self._request(
            "POST",
            ServerEndpoints.PREVIEW_REDACTION,
            json=request_payload,
        )
        return RedactionPreviewResponse.model_validate(response_payload)

    # MARK: - Resource Management

    def close(self) -> None:
        """Close the HTTP client and release connections."""
        if self._http_client is not None and self._owns_http_client:
            self._http_client.close()
            self._http_client = None

    def __enter__(self) -> Client:
        return self

    def __exit__(self, exc_type: Any, exc_val: Any, exc_tb: Any) -> None:
        self.close()

    # MARK: - Utilities

    @staticmethod
    def sanitize_pubsub(pubsub: dict | None) -> dict | None:
        """Redact sensitive fields from pubsub configuration for logging."""
        if not isinstance(pubsub, dict):
            return pubsub
        redacted = dict(pubsub)
        if "token" in redacted and isinstance(redacted["token"], str):
            redacted["token"] = "***"
        return redacted

    def _resolve_configuration(self) -> MaivnConfiguration:
        """Resolve and cache configuration."""
        if self._configuration is not None:
            return self._configuration

        resolved = self._configuration_provider()
        if not isinstance(resolved, MaivnConfiguration):
            raise TypeError(
                f"configuration_provider must return MaivnConfiguration, got {type(resolved)!r}"
            )
        self._configuration = resolved
        return resolved


# MARK: ClientBuilder


class ClientBuilder:
    """Factory helpers for creating :class:`Client` instances."""

    @staticmethod
    def from_configuration(configuration: MaivnConfiguration) -> Client:
        """Create a client from an explicit MaivnConfiguration instance."""
        return Client.from_configuration(
            api_key=configuration.security.api_key,
            configuration=configuration,
            timeout=configuration.server.timeout_seconds,
            tool_execution_timeout=configuration.execution.tool_execution_timeout_seconds,
            dependency_wait_timeout=configuration.execution.dependency_wait_timeout_seconds,
            total_execution_timeout=configuration.execution.total_execution_timeout_seconds,
        )

    @staticmethod
    def from_environment() -> Client:
        """Create a client using configuration derived from environment variables."""
        configuration = ConfigurationBuilder.from_environment()
        return ClientBuilder.from_configuration(configuration)


__all__ = [
    "Client",
    "ClientBuilder",
]
