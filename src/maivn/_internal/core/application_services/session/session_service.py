"""Session orchestration helpers."""

# pyright: strict
from __future__ import annotations

from typing import TypeAlias, cast

from maivn_shared import SessionClientProtocol, SessionRequest, SessionStartRequest
from maivn_shared.infrastructure.logging import LoggerProtocol
from pydantic import JsonValue

from maivn._internal.core import SessionEndpoints
from maivn._internal.utils.logging import get_optional_logger

# MARK: Types

JsonObject: TypeAlias = dict[str, JsonValue]
JsonArray: TypeAlias = list[JsonValue]


# MARK: - SessionService


class SessionService:
    """Handles server session lifecycle interactions."""

    def __init__(self, *, logger: LoggerProtocol | None = None) -> None:
        self._logger: LoggerProtocol = logger or get_optional_logger()

    # MARK: - Session Lifecycle

    def start_session(
        self,
        *,
        client: SessionClientProtocol,
        payload: JsonObject,
    ) -> SessionEndpoints:
        """Start a session via the provided client and return endpoints."""
        # Tools are nested inside payload.state.tools
        nested_state = cast(JsonObject, payload.get("state", {}))
        tools = cast(JsonArray, nested_state.get("tools", []))
        tool_count = len(tools)
        self._logger.debug(
            "[TIMING] http.start start-session tools=%d",
            tool_count,
        )
        response = client.start_session(payload=payload)
        endpoints = self._parse_session_response(response)
        self._logger.info("Started session %s", endpoints.session_id)
        return endpoints

    def build_payload(
        self,
        *,
        state: SessionRequest,
        client_id: str,
        thread_id: str | None,
    ) -> JsonObject:
        """Construct the payload for session start requests."""
        start_request = SessionStartRequest(
            state=state,
            client_id=client_id,
            thread_id=thread_id,
        )
        return cast(JsonObject, start_request.model_dump(mode="json", exclude_none=True))

    # MARK: - Private Helpers

    def _parse_session_response(self, response: JsonObject) -> SessionEndpoints:
        """Parse and validate session response from server."""
        session_id = str(response.get("session_id", ""))
        assistant_id = response.get("assistant_id")
        assistant_id_value = str(assistant_id) if assistant_id else None
        events_url = str(response.get("events_url", ""))
        resume_url = str(response.get("resume_url", ""))
        if not (session_id and events_url and resume_url):
            raise RuntimeError("Server response missing session endpoints")
        return SessionEndpoints(
            session_id=session_id,
            assistant_id=assistant_id_value,
            events_url=events_url,
            resume_url=resume_url,
        )
