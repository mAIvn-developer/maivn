"""Interrupt handling helpers for AgentOrchestrator services."""

# pyright: strict
from __future__ import annotations

from collections.abc import Callable
from typing import Protocol, cast, runtime_checkable

from maivn_shared import SessionClientProtocol
from maivn_shared.infrastructure.logging import LoggerProtocol
from pydantic import JsonValue

from maivn._internal.core import SessionEndpoints
from maivn._internal.core.exceptions import ServerAuthenticationError
from maivn._internal.core.services.interrupt_service import InterruptService
from maivn._internal.utils.logging import get_optional_logger
from maivn._internal.utils.reporting.terminal_reporter import BaseReporter

# MARK: Types

JsonObject = dict[str, JsonValue]


class CheckpointResumeResponse(Protocol):
    """Response surface returned by the checkpoint-resume HTTP client."""

    def raise_for_status(self) -> None:
        """Raise when the checkpoint-resume request failed."""
        ...

    def json(self) -> object:
        """Return the decoded response body."""
        ...


class CheckpointHttpClient(Protocol):
    """HTTP client surface required by checkpoint interrupt resume."""

    def post(
        self,
        url: str,
        *,
        json: JsonObject,
        headers: dict[str, str] | None,
    ) -> CheckpointResumeResponse:
        """POST a checkpoint resume payload."""
        ...


@runtime_checkable
class PrivateDataAgent(Protocol):
    """Agent surface that stores private data."""

    private_data: dict[str, object]


# MARK: Interrupt Manager


class InterruptManager:
    """Handle user-input interrupts and checkpoint resumes."""

    def __init__(self) -> None:
        self.resumed_session_id: str | None = None
        self._collected_interrupt_keys: set[str] = set()

    def store_resumed_session(self, session_id: str | None) -> None:
        """Record resumed session id for chaining."""
        self.resumed_session_id = session_id

    def mark_interrupt_collected(self, data_key: str) -> None:
        """Mark an interrupt data_key as collected to prevent duplicate prompts."""
        self._collected_interrupt_keys.add(data_key)

    def is_interrupt_collected(self, data_key: str) -> bool:
        """Check if an interrupt data_key has already been collected."""
        return data_key in self._collected_interrupt_keys

    def clear_collected_interrupts(self) -> None:
        """Clear collected interrupt keys (e.g., when starting a new session)."""
        self._collected_interrupt_keys.clear()

    def should_chain(self, result: JsonObject) -> bool:
        """Determine whether to chain to a resumed session."""
        return bool(self.resumed_session_id and result.get("status") == "interrupted")

    def build_resumed_endpoints(self, base_url: str, session_id: str) -> SessionEndpoints:
        """Construct endpoints for new chained session."""
        return SessionEndpoints(
            session_id=session_id,
            events_url=f"{base_url}/sessions/{session_id}/events",
            resume_url=f"{base_url}/sessions/{session_id}/resume",
        )

    def update_reporter_progress(
        self,
        reporter: BaseReporter | None,
        progress_task: object | None,
        message: str,
    ) -> None:
        """Helper for updating progress safely."""
        if reporter and progress_task:
            reporter.update_progress(progress_task, message)


# MARK: Interrupt Handler


class InterruptHandler:
    """Coordinate interrupt collection and checkpoint resume behavior."""

    def __init__(
        self,
        *,
        agent: object,
        client: SessionClientProtocol,
        interrupt_service: InterruptService,
        interrupt_manager: InterruptManager,
        resume_callback: Callable[[str, JsonObject], None],
        reporter_supplier: Callable[[], BaseReporter | None],
        progress_task_supplier: Callable[[], object | None],
        logger: LoggerProtocol | None = None,
    ) -> None:
        self._agent: object = agent
        self._client: SessionClientProtocol = client
        self._interrupt_service: InterruptService = interrupt_service
        self._interrupt_manager: InterruptManager = interrupt_manager
        self._resume_callback: Callable[[str, JsonObject], None] = resume_callback
        self._get_reporter: Callable[[], BaseReporter | None] = reporter_supplier
        self._get_progress_task: Callable[[], object | None] = progress_task_supplier
        self._logger: LoggerProtocol = logger or get_optional_logger()

    # MARK: - Input Collection

    def _collect_user_input(
        self,
        prompt: str,
        *,
        input_type: str = "text",
        choices: list[str] | None = None,
        data_key: str | None = None,
        arg_name: str | None = None,
    ) -> str:
        """Collect user input via reporter or interrupt service."""
        reporter = self._get_reporter()
        if reporter:
            return _call_reporter_get_input(
                reporter,
                f"\n{prompt}",
                input_type=input_type,
                choices=choices,
                data_key=data_key,
                arg_name=arg_name,
            )

        return self._interrupt_service.get_user_input(
            f"\n{prompt}",
            input_type=input_type,
            choices=choices,
            data_key=data_key,
            arg_name=arg_name,
        )

    def _get_client_base_url(self) -> str:
        """Extract base URL from client."""
        return str(getattr(self._client, "base_url", getattr(self._client, "_base_url", "")))

    def _get_http_client(self) -> CheckpointHttpClient:
        """Extract HTTP client from session client."""
        http_client = cast(
            CheckpointHttpClient | None,
            getattr(self._client, "http_client", getattr(self._client, "_http_client", None)),
        )
        if not http_client:
            raise RuntimeError("Client does not have HTTP client available")
        return http_client

    def _get_client_headers(self) -> dict[str, str] | None:
        headers_fn = cast(object, getattr(self._client, "headers", None))
        if not callable(headers_fn):
            return None
        try:
            headers_obj = cast(Callable[[], object], headers_fn)()
        except ServerAuthenticationError:
            raise
        except Exception as exc:  # noqa: BLE001 - invalid optional auth headers are non-fatal
            self._logger.warning("[INTERRUPT] Failed to read client headers: %s", exc)
            return None
        if isinstance(headers_obj, dict):
            headers_dict = cast(dict[object, object], headers_obj)
            if all(isinstance(k, str) and isinstance(v, str) for k, v in headers_dict.items()):
                return cast(dict[str, str], headers_dict)
        if headers_obj is not None:
            headers_type_name = type(cast(object, headers_obj)).__name__
            self._logger.warning(
                "[INTERRUPT] Client headers() returned invalid type: %s",
                headers_type_name,
            )
        return None

    # MARK: - Legacy Interrupt Request Handling

    def handle_user_input_request(
        self,
        tool_event_id: str,
        value: JsonObject,
        resume_url: str,
    ) -> None:
        """Collect user input and send it back to the server."""
        arg_name = _string_value(value.get("arg_name"), "")
        prompt = _string_value(value.get("prompt"), "Please enter input: ")
        reporter = self._get_reporter()
        progress_task = self._get_progress_task()

        try:
            self._interrupt_manager.update_reporter_progress(
                reporter, progress_task, "Awaiting user input..."
            )
            user_input = self._collect_user_input(prompt, arg_name=arg_name)
        except (RuntimeError, EOFError) as exc:
            self._logger.warning("[USER_INPUT] Input interrupted: %s", exc)
            user_input = ""
        finally:
            self._interrupt_manager.update_reporter_progress(
                reporter, progress_task, "Processing events..."
            )

        response_payload: JsonObject = {tool_event_id: {"arg_name": arg_name, "value": user_input}}
        self._logger.info("[USER_INPUT] Collected input for arg=%s", arg_name)
        self._resume_callback(resume_url, response_payload)

    # MARK: - Checkpoint Interrupt Handling

    def handle_interrupt_required(
        self,
        interrupt_data: JsonObject,
        resume_url: str,
    ) -> None:
        """Handle checkpoint-based interrupt events."""
        _ = resume_url
        checkpoint_id = _optional_string_value(interrupt_data.get("checkpoint_id"))
        data_key = _string_value(interrupt_data.get("data_key"), "")
        prompt = _string_value(interrupt_data.get("prompt"), "Please enter input: ")
        arg_name = _optional_string_value(interrupt_data.get("arg_name")) or data_key
        tool_name = _string_value(interrupt_data.get("tool_name"), "")
        input_type = _string_value(interrupt_data.get("input_type"), "text")
        choices_value = interrupt_data.get("choices")
        choices = (
            [str(choice) for choice in choices_value] if isinstance(choices_value, list) else None
        )
        reporter = self._get_reporter()
        progress_task = self._get_progress_task()

        self._logger.info(
            "[INTERRUPT] Checkpoint-based interrupt for tool=%s data_key=%s",
            tool_name,
            data_key,
        )

        # Skip if this data_key has already been collected (prevents duplicate prompts)
        if self._interrupt_manager.is_interrupt_collected(data_key):
            self._logger.info(
                "[INTERRUPT] Skipping duplicate prompt for data_key=%s (already collected)",
                data_key,
            )
            return

        user_input = self._collect_interrupt_input(
            prompt,
            data_key,
            arg_name=arg_name,
            input_type=input_type,
            choices=choices,
            reporter=reporter,
            progress_task=progress_task,
        )

        # Mark this data_key as collected to prevent future duplicate prompts
        self._interrupt_manager.mark_interrupt_collected(data_key)

        self._resume_checkpoint(checkpoint_id, data_key, user_input)

    def _collect_interrupt_input(
        self,
        prompt: str,
        data_key: str,
        *,
        arg_name: str | None,
        input_type: str,
        choices: list[str] | None,
        reporter: BaseReporter | None,
        progress_task: object | None,
    ) -> str:
        """Collect user input for checkpoint interrupt."""
        try:
            self._interrupt_manager.update_reporter_progress(
                reporter, progress_task, "Awaiting user input..."
            )
            self._logger.info(
                "[INTERRUPT] Waiting for user input for data_key=%s (reporter=%s)",
                data_key,
                "yes" if reporter else "no",
            )

            user_input = self._collect_user_input(
                prompt,
                input_type=input_type,
                choices=choices,
                data_key=data_key,
                arg_name=arg_name,
            )

            self._logger.info(
                "[INTERRUPT] Received user input for data_key=%s: '%s'",
                data_key,
                user_input[:50] if user_input else "(empty)",
            )
            return user_input
        except (RuntimeError, EOFError) as exc:
            self._logger.warning("[INTERRUPT] Input interrupted: %s", exc)
            return ""
        finally:
            self._interrupt_manager.update_reporter_progress(
                reporter, progress_task, "Resuming from checkpoint..."
            )

    def _resume_checkpoint(
        self,
        checkpoint_id: str | None,
        data_key: str,
        user_input: str,
    ) -> None:
        """Resume execution from checkpoint with collected input."""
        base_url = self._get_client_base_url()
        checkpoint_resume_url = f"{base_url}/api/v1/checkpoints/{checkpoint_id}/resume"
        resume_payload: JsonObject = {"interrupt_data": {data_key: user_input}}

        self._logger.info(
            "[INTERRUPT] Resuming checkpoint %s via %s",
            checkpoint_id,
            checkpoint_resume_url,
        )

        try:
            http_client = self._get_http_client()
            headers = self._get_client_headers()

            response = http_client.post(
                checkpoint_resume_url,
                json=resume_payload,
                headers=headers,
            )
            response.raise_for_status()

            resumed_session_data = response.json()
            if not isinstance(resumed_session_data, dict):
                raise RuntimeError("Checkpoint resume response was not a JSON object")
            self._process_resumed_session(cast(JsonObject, resumed_session_data))
        except ServerAuthenticationError:
            raise
        except Exception as exc:  # noqa: BLE001 - checkpoint resume failures are logged only
            self._logger.error(
                "[INTERRUPT] Failed to resume checkpoint %s: %s",
                checkpoint_id,
                exc,
            )

    def _process_resumed_session(self, resumed_session_data: JsonObject) -> None:
        """Process resumed session data and update agent state."""
        new_session_id = _optional_string_value(resumed_session_data.get("session_id"))

        self._logger.info(
            "[INTERRUPT] Checkpoint resumed successfully: new_session=%s",
            new_session_id,
        )

        self._interrupt_manager.store_resumed_session(new_session_id)

        resumed_private_data = resumed_session_data.get("private_data")
        if isinstance(resumed_private_data, dict) and resumed_private_data:
            # Merge the owner's own private values returned by the resume response
            # into agent state so subsequent client-side rehydration can resolve
            # placeholder tokens. These values are never sent to an LLM or
            # persisted server-side; they originate from this caller's session.
            agent = self._agent
            if isinstance(agent, PrivateDataAgent):
                merged: dict[str, object] = dict(agent.private_data)
                merged.update(cast(dict[str, object], resumed_private_data))
                agent.private_data = merged

        self._logger.info(
            "[INTERRUPT] Will chain to resumed session %s",
            new_session_id,
        )


# MARK: Helpers


def _optional_string_value(value: JsonValue) -> str | None:
    return value if isinstance(value, str) else None


def _string_value(value: JsonValue, fallback: str) -> str:
    return value if isinstance(value, str) else fallback


def _call_reporter_get_input(
    reporter: BaseReporter,
    prompt: str,
    *,
    input_type: str,
    choices: list[str] | None,
    data_key: str | None,
    arg_name: str | None,
) -> str:
    try:
        return reporter.get_input(
            prompt,
            input_type=input_type,
            choices=choices or [],
            data_key=data_key,
            arg_name=arg_name,
        )
    except TypeError as exc:
        message = str(exc)
        if "unexpected keyword" not in message and "unexpected keyword argument" not in message:
            raise
        return reporter.get_input(prompt)


__all__ = ["InterruptHandler", "InterruptManager"]
