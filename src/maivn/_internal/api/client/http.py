# pyright: strict
"""HTTP request handling for the maivn SDK Client."""

from __future__ import annotations

from typing import Protocol, TypeAlias, TypeVar, cast
from urllib.parse import urlencode

import httpx
from pydantic import BaseModel

from maivn._internal.core.exceptions import ServerAuthenticationError

# MARK: Types

ModelT = TypeVar("ModelT", bound=BaseModel)
JsonPrimitive: TypeAlias = str | int | float | bool | None
JsonValue: TypeAlias = JsonPrimitive | list["JsonValue"] | dict[str, "JsonValue"]
JsonObject: TypeAlias = dict[str, JsonValue]
QueryValue: TypeAlias = str | int | float | bool | list[str] | None


class HttpClientProtocol(Protocol):
    """Minimal synchronous HTTP client interface used by the SDK client."""

    def request(
        self,
        method: str,
        url: str,
        *,
        headers: dict[str, str],
        json: JsonObject | None,
    ) -> httpx.Response: ...

    def close(self) -> None: ...


def as_json_object(value: object) -> JsonObject | None:
    """Return ``value`` as a JSON object when it has the expected runtime shape."""
    if isinstance(value, dict):
        return cast(JsonObject, value)
    return None


def as_json_array(value: object) -> list[JsonValue] | None:
    """Return ``value`` as a JSON array when it has the expected runtime shape."""
    if isinstance(value, list):
        return cast(list[JsonValue], value)
    return None


# MARK: HTTP Mixin


class ClientHttpMixin:
    """HTTP request methods shared by the Client class."""

    _api_key: str | None = None
    _base_url: str = ""
    _timeout: float | int | None = None
    _http_client: HttpClientProtocol | None = None
    _owns_http_client: bool = True

    def headers(self) -> dict[str, str]:
        """Build authorization headers for API requests."""
        if not self._api_key or not self._api_key.strip():
            raise ServerAuthenticationError(
                status_code=401,
                url="(request not sent)",
                server_error="missing_header",
                server_message="API key is not configured",
                hint=(
                    "Set MAIVN_DEV_API_KEY or MAIVN_API_KEY to a valid Maivn API key. "
                    "For local/dev unauthenticated runs, set MAIVN_ALLOW_MOCK_USER=true "
                    "on the maivn-server process."
                ),
            )
        return {
            "Content-Type": "application/json",
            "X-API-Key": self._api_key,
        }

    def _get_http_client(self) -> HttpClientProtocol:
        """Get or create reusable HTTP client with connection pooling."""
        if self._http_client is None:
            self._http_client = httpx.Client(timeout=self._timeout)
            self._owns_http_client = True
        return self._http_client

    def _request(self, method: str, path: str, *, json: JsonObject | None = None) -> JsonValue:
        """Perform a JSON HTTP request against maivn-server."""
        url = f"{self._base_url.rstrip('/')}/{path.lstrip('/')}"
        client = self._get_http_client()
        response = client.request(method, url, headers=self.headers(), json=json)
        try:
            _ = response.raise_for_status()
        except httpx.HTTPStatusError as exc:
            if exc.response.status_code in {401, 403}:
                self._raise_auth_error(exc, url)
            # For 4xx client errors (e.g. 422 Unprocessable Entity from a
            # force_model pre-flight rejection), extract the server's ``detail``
            # field and raise a descriptive RuntimeError so callers receive the
            # human-readable rejection reason rather than a bare HTTP status line.
            if 400 <= exc.response.status_code < 500:
                self._raise_client_error(exc, url)
            raise
        if not response.content:
            return {}
        return cast(JsonValue, response.json())

    def _request_object(
        self,
        method: str,
        path: str,
        *,
        json: JsonObject | None = None,
    ) -> JsonObject:
        payload = self._request(method, path, json=json)
        if isinstance(payload, dict):
            return cast(JsonObject, payload)
        raise ValueError("Expected JSON object response")

    def _raise_auth_error(self, exc: httpx.HTTPStatusError, url: str) -> None:
        """Extract error details and raise ServerAuthenticationError."""
        server_error: str | None = None
        server_message: str | None = None
        try:
            payload = as_json_object(cast(object, exc.response.json()))
            detail = payload.get("detail") if payload is not None else None
            if isinstance(detail, dict):
                server_error = str(detail.get("error") or "").strip() or None
                server_message = str(detail.get("message") or "").strip() or None
            elif isinstance(detail, str):
                server_message = detail.strip() or None
        except (ValueError, TypeError):
            server_message = None

        has_key = bool(self._api_key and self._api_key.strip())
        if has_key:
            hint = (
                "Your API key was sent but was rejected. Confirm the key is correct, "
                "active, and has access to this project (manage keys in the Maivn "
                "Developer Portal)."
            )
        else:
            hint = (
                "Set MAIVN_DEV_API_KEY or MAIVN_API_KEY to a valid Maivn API key. "
                "For local/dev unauthenticated runs, set MAIVN_ALLOW_MOCK_USER=true "
                "on the maivn-server process."
            )

        raise ServerAuthenticationError(
            status_code=exc.response.status_code,
            url=url,
            server_error=server_error,
            server_message=server_message,
            hint=hint,
        ) from None

    @staticmethod
    def _raise_client_error(exc: httpx.HTTPStatusError, url: str) -> None:
        """Extract ``detail`` from a 4xx JSON response and raise RuntimeError.

        FastAPI wraps domain-level rejection messages in
        ``{"detail": "<message>"}`` JSON bodies.  Surfacing that message
        directly gives callers actionable feedback (e.g. "Model 'x' is not
        available. Available models: ...") instead of a bare HTTP status line.
        """
        detail: str | None = None
        try:
            payload = as_json_object(cast(object, exc.response.json()))
            raw = payload.get("detail") if payload is not None else None
            if isinstance(raw, str) and raw.strip():
                detail = raw.strip()
        except (ValueError, TypeError):
            pass

        status_code = exc.response.status_code
        if detail:
            raise RuntimeError(
                f"the mAIvn service rejected the request ({status_code}): {detail}"
            ) from exc
        raise exc

    @staticmethod
    def _with_query(path: str, params: dict[str, QueryValue]) -> str:
        filtered = {
            key: value
            for key, value in params.items()
            if value is not None and value != [] and value != ""
        }
        if not filtered:
            return path
        return f"{path}?{urlencode(filtered, doseq=True)}"

    @staticmethod
    def _extract_items(payload: JsonValue) -> list[JsonObject]:
        payload_object = as_json_object(payload)
        if payload_object is not None:
            items = as_json_array(payload_object.get("items"))
            if items is not None:
                return [item for item in items if isinstance(item, dict)]
            return []
        payload_array = as_json_array(payload)
        if payload_array is not None:
            return [item for item in payload_array if isinstance(item, dict)]
        return []

    @staticmethod
    def _validate_model(model_type: type[ModelT], payload: object) -> ModelT:
        return model_type.model_validate(payload)

    def _get_json(self, path: str) -> JsonValue:
        return self._request("GET", path, json=None)

    def _post_json(self, path: str, payload: JsonObject | None = None) -> JsonValue:
        return self._request("POST", path, json=payload or {})

    def _patch_json(self, path: str, payload: JsonObject) -> JsonValue:
        return self._request("PATCH", path, json=payload)

    def _delete_json(self, path: str) -> JsonValue:
        return self._request("DELETE", path, json=None)


__all__ = ["ClientHttpMixin"]
