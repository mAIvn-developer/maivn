"""HTTP client service for orchestrator network operations with retry logic."""

from __future__ import annotations

import time
from typing import Any

import httpx
from maivn_shared import SessionClientProtocol
from maivn_shared.infrastructure.logging import LoggerProtocol

from maivn._internal.utils.logging import get_optional_logger


class HttpClientService:
    """Handles HTTP operations for orchestrators with proper abstraction."""

    # MARK: - Initialization

    def __init__(
        self,
        *,
        timeout: float,
        max_retries: int = 3,
        logger: LoggerProtocol | None = None,
        http_client: httpx.Client | None = None,
    ) -> None:
        self._timeout = timeout
        self._max_retries = max_retries
        self._logger: LoggerProtocol = logger or get_optional_logger()
        self._client = http_client or self._create_http_client(timeout)
        self._owns_http_client = http_client is None

    def _create_http_client(self, timeout: float) -> httpx.Client:
        """Create an HTTP client with connection pooling."""
        limits = httpx.Limits(
            max_keepalive_connections=20,
            max_connections=100,
            keepalive_expiry=30.0,
        )

        try:
            return httpx.Client(
                timeout=httpx.Timeout(timeout),
                limits=limits,
                http2=True,
            )
        except ImportError:
            self._logger.debug("HTTP/2 support not available (h2 package missing), using HTTP/1.1")
            return httpx.Client(
                timeout=httpx.Timeout(timeout),
                limits=limits,
                http2=False,
            )

    # MARK: - Public Methods

    def post_resume(
        self,
        url: str,
        payload: dict[str, Any],
        client: SessionClientProtocol,
    ) -> None:
        """Post resume payload with retries."""
        headers = client.headers()
        json_payload = {"value": payload}

        last_exception = self._execute_with_retries(
            url=url,
            json_payload=json_payload,
            headers=headers,
            payload_count=len(payload),
        )

        if last_exception:
            raise last_exception

    def close(self) -> None:
        """Close the HTTP client if owned."""
        if self._owns_http_client:
            self._client.close()

    # MARK: - Context Manager

    def __enter__(self) -> HttpClientService:
        return self

    def __exit__(self, *args: Any) -> None:
        self.close()

    # MARK: - Private Methods

    def _execute_with_retries(
        self,
        *,
        url: str,
        json_payload: dict[str, Any],
        headers: dict[str, str],
        payload_count: int,
    ) -> Exception | None:
        """Execute POST request with retry logic."""
        last_exception: Exception | None = None

        for attempt in range(self._max_retries):
            try:
                self._log_attempt(url, attempt, payload_count)
                response = self._client.post(url, json=json_payload, headers=headers)
                response.raise_for_status()
                self._log_success(attempt)
                return None

            except (httpx.ConnectError, httpx.TimeoutException, httpx.RemoteProtocolError) as exc:
                last_exception = exc
                if not self._handle_retryable_error(exc, attempt):
                    break

            except httpx.HTTPStatusError as exc:
                result = self._handle_http_status_error(exc, attempt)
                if result is None:
                    # Continue retry loop
                    last_exception = exc
                    continue
                if isinstance(result, Exception):
                    raise result from exc
                last_exception = exc

            except Exception as exc:  # noqa: BLE001
                self._logger.error("Unexpected error during resume POST: %s", exc)
                raise

        return last_exception

    def _log_attempt(self, url: str, attempt: int, payload_count: int) -> None:
        """Log the current POST attempt."""
        self._logger.debug(
            "Posting resume payload to %s (attempt %d/%d) with %d tool result(s)",
            url,
            attempt + 1,
            self._max_retries,
            payload_count,
        )

    def _log_success(self, attempt: int) -> None:
        """Log successful POST completion."""
        if attempt > 0:
            self._logger.info("Resume payload posted successfully after %d retry(ies)", attempt)
        else:
            self._logger.debug("Resume payload posted successfully")

    def _handle_retryable_error(self, exc: Exception, attempt: int) -> bool:
        """Handle retryable network errors. Returns True if should continue retrying."""
        if self._is_connection_refused(exc):
            self._logger.error(
                "Resume POST failed (connection refused). Server appears down: %s",
                exc,
            )
            return False

        if attempt < self._max_retries - 1:
            delay = 2**attempt
            self._logger.warning(
                "Retryable error on attempt %d/%d: %s. Retrying in %ds...",
                attempt + 1,
                self._max_retries,
                type(exc).__name__,
                delay,
            )
            time.sleep(delay)
            return True

        self._logger.error(
            "All %d retry attempts exhausted. Last error: %s",
            self._max_retries,
            exc,
        )
        return False

    def _is_connection_refused(self, exc: Exception) -> bool:
        cause = getattr(exc, "__cause__", None)
        if isinstance(cause, OSError) and getattr(cause, "winerror", None) == 10061:
            return True
        msg = str(exc).lower()
        return "actively refused" in msg or "connection refused" in msg

    def _handle_http_status_error(
        self,
        exc: httpx.HTTPStatusError,
        attempt: int,
    ) -> Exception | None:
        """Handle HTTP status errors. Returns None to continue retrying, Exception to raise."""
        if exc.response.status_code >= 500 and attempt < self._max_retries - 1:
            delay = 2**attempt
            self._logger.warning(
                "Server error %d on attempt %d/%d. Retrying in %ds...",
                exc.response.status_code,
                attempt + 1,
                self._max_retries,
                delay,
            )
            time.sleep(delay)
            return None  # Signal to continue retry loop

        self._logger.error(
            "Resume POST failed: %s %s - %s",
            exc.response.status_code,
            exc.response.reason_phrase,
            exc.response.text if hasattr(exc.response, "text") else "No response body",
        )
        return exc  # Return exception to be raised


__all__ = [
    "HttpClientService",
]
