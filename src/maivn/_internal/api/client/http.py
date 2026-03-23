"""HTTP request handling for the maivn SDK Client."""

from __future__ import annotations

from typing import Any, TypeVar
from urllib.parse import urlencode

import httpx
from pydantic import BaseModel

from maivn._internal.core.exceptions import ServerAuthenticationError

ModelT = TypeVar("ModelT", bound=BaseModel)


# MARK: HTTP Mixin


class ClientHttpMixin:
    """HTTP request methods shared by the Client class."""

    _api_key: str | None
    _base_url: str
    _timeout: float | int | None
    _http_client: httpx.Client | None
    _owns_http_client: bool

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

    def _get_http_client(self) -> httpx.Client:
        """Get or create reusable HTTP client with connection pooling."""
        if self._http_client is None:
            self._http_client = httpx.Client(timeout=self._timeout)
            self._owns_http_client = True
        return self._http_client

    def _request(self, method: str, path: str, *, json: dict[str, Any] | None = None) -> Any:
        """Perform a JSON HTTP request against maivn-server."""
        url = f"{self._base_url.rstrip('/')}/{path.lstrip('/')}"
        client = self._get_http_client()
        response = client.request(method, url, headers=self.headers(), json=json)
        try:
            response.raise_for_status()
        except httpx.HTTPStatusError as exc:
            if exc.response.status_code in {401, 403}:
                self._raise_auth_error(exc, url)
            raise
        return response.json() if response.content else {}

    def _raise_auth_error(self, exc: httpx.HTTPStatusError, url: str) -> None:
        """Extract error details and raise ServerAuthenticationError."""
        server_error: str | None = None
        server_message: str | None = None
        try:
            payload = exc.response.json()
            detail = payload.get("detail") if isinstance(payload, dict) else None
            if isinstance(detail, dict):
                server_error = str(detail.get("error") or "").strip() or None
                server_message = str(detail.get("message") or "").strip() or None
            elif isinstance(detail, str):
                server_message = detail.strip() or None
        except Exception:
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
    def _with_query(path: str, params: dict[str, Any]) -> str:
        filtered = {
            key: value
            for key, value in params.items()
            if value is not None and value != [] and value != ""
        }
        if not filtered:
            return path
        return f"{path}?{urlencode(filtered, doseq=True)}"

    @staticmethod
    def _extract_items(payload: Any) -> list[dict[str, Any]]:
        if isinstance(payload, dict):
            items = payload.get("items")
            if isinstance(items, list):
                return [item for item in items if isinstance(item, dict)]
            return []
        if isinstance(payload, list):
            return [item for item in payload if isinstance(item, dict)]
        return []

    @staticmethod
    def _validate_model(model_type: type[ModelT], payload: Any) -> ModelT:
        return model_type.model_validate(payload)

    def _get_json(self, path: str) -> Any:
        return self._request("GET", path, json=None)

    def _post_json(self, path: str, payload: dict[str, Any] | None = None) -> Any:
        return self._request("POST", path, json=payload or {})

    def _patch_json(self, path: str, payload: dict[str, Any]) -> Any:
        return self._request("PATCH", path, json=payload)

    def _delete_json(self, path: str) -> Any:
        return self._request("DELETE", path, json=None)


__all__ = ["ClientHttpMixin"]
