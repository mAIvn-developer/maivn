"""SSE event consumption helpers for orchestrators."""

from __future__ import annotations

import logging
from collections.abc import Callable, Iterator
from typing import Any

from maivn_shared import loads

from maivn._internal.core import SessionEndpoints, SSEEvent
from maivn._internal.core.exceptions import ServerAuthenticationError
from maivn._internal.core.services import EventStreamHandlers
from maivn._internal.utils.reporting.terminal_reporter import BaseReporter

from .reporter_hooks import OrchestratorReporterHooks

LOGGER = logging.getLogger(__name__)


class EventConsumptionCoordinator:
    """Coordinate SSE event consumption for orchestrators."""

    # MARK: - Initialization

    def __init__(
        self,
        *,
        client: Any,
        event_processor: Any,
        interrupt_manager: Any,
        interrupt_service: Any,
        tool_event_dispatcher: Any,
        interrupt_handler: Any,
        sse_client: Any,
        reporter_hooks: OrchestratorReporterHooks,
        set_reporter_context: Callable[[BaseReporter | None, Any | None], None],
    ) -> None:
        self._client = client
        self._event_processor = event_processor
        self._interrupt_manager = interrupt_manager
        self._interrupt_service = interrupt_service
        self._tool_event_dispatcher = tool_event_dispatcher
        self._interrupt_handler = interrupt_handler
        self._sse_client = sse_client
        self._reporter_hooks = reporter_hooks
        self._set_reporter_context = set_reporter_context

    # MARK: - Public API

    def consume_events(
        self,
        endpoints: SessionEndpoints,
        timeout: float,
        reporter: BaseReporter | None,
        progress_task: Any | None = None,
        on_event: Callable[[SSEEvent], None] | None = None,
    ) -> dict[str, Any]:
        """Consume SSE events from the server."""
        self._setup_event_consumption(reporter, progress_task)
        handlers = self._build_event_handlers(reporter)
        events = self._iter_events(endpoints.events_url)

        try:
            result = self._event_processor.consume(
                events=events,
                resume_url=endpoints.resume_url,
                handlers=handlers,
                on_event=on_event,
            )
        except RuntimeError as exc:
            return self._handle_consumption_error(
                exc,
                endpoints,
                timeout,
                reporter,
                progress_task,
                on_event=on_event,
            )

        return self._process_consumption_result(
            result,
            endpoints,
            timeout,
            reporter,
            progress_task,
            on_event=on_event,
        )

    # MARK: - Event Handling

    def _setup_event_consumption(
        self, reporter: BaseReporter | None, progress_task: Any | None
    ) -> None:
        self._set_reporter_context(reporter, progress_task)
        if reporter and hasattr(self._interrupt_service, "set_reporter"):
            self._interrupt_service.set_reporter(reporter)
        self._interrupt_manager.store_resumed_session(None)

    def _build_event_handlers(self, reporter: BaseReporter | None) -> EventStreamHandlers:
        # Always wire up reporter hooks. They check for reporter availability dynamically,
        # which allows nested invocations to inherit a parent reporter through context vars.
        _ = reporter  # Parameter kept for API compatibility.
        return EventStreamHandlers(
            coerce_payload=self._coerce_payload,
            process_tool_requests=self._tool_event_dispatcher.process_tool_requests,
            process_tool_batch=self._tool_event_dispatcher.process_tool_batch,
            submit_tool_call=self._tool_event_dispatcher.submit_tool_call,
            acknowledge_barrier=self._tool_event_dispatcher.acknowledge_barrier,
            handle_user_input_request=self._interrupt_handler.handle_user_input_request,
            handle_interrupt_required=self._interrupt_handler.handle_interrupt_required,
            handle_model_tool_complete=self._reporter_hooks.handle_model_tool_complete,
            handle_system_tool_start=self._reporter_hooks.handle_system_tool_start,
            handle_system_tool_chunk=self._reporter_hooks.handle_system_tool_chunk,
            handle_system_tool_complete=self._reporter_hooks.handle_system_tool_complete,
            handle_system_tool_error=self._reporter_hooks.handle_system_tool_error,
            handle_action_update=self._reporter_hooks.handle_action_update,
            handle_status_message=self._reporter_hooks.handle_status_message,
            handle_enrichment=self._reporter_hooks.handle_enrichment,
        )

    def _handle_consumption_error(
        self,
        error: RuntimeError,
        endpoints: SessionEndpoints,
        timeout: float,
        reporter: BaseReporter | None,
        progress_task: Any | None,
        on_event: Callable[[SSEEvent], None] | None,
    ) -> dict[str, Any]:
        _ = endpoints  # Parameter kept for API compatibility.
        resumed_session_id = self._interrupt_manager.resumed_session_id
        if resumed_session_id and "without a valid final payload" in str(error):
            return self._chain_to_resumed_session(
                resumed_session_id,
                timeout,
                reporter,
                progress_task,
                on_event=on_event,
            )
        raise error

    def _process_consumption_result(
        self,
        result: dict[str, Any],
        endpoints: SessionEndpoints,
        timeout: float,
        reporter: BaseReporter | None,
        progress_task: Any | None,
        on_event: Callable[[SSEEvent], None] | None,
    ) -> dict[str, Any]:
        _ = endpoints  # Parameter kept for API compatibility.
        if self._interrupt_manager.should_chain(result):
            resumed_session_id = self._interrupt_manager.resumed_session_id
            return self._chain_to_resumed_session(
                resumed_session_id or "",
                timeout,
                reporter,
                progress_task,
                on_event=on_event,
            )
        return result

    def _chain_to_resumed_session(
        self,
        resumed_session_id: str,
        timeout: float,
        reporter: BaseReporter | None,
        progress_task: Any | None,
        on_event: Callable[[SSEEvent], None] | None,
    ) -> dict[str, Any]:
        base_url = getattr(self._client, "base_url", getattr(self._client, "_base_url", ""))
        resumed_endpoints = self._interrupt_manager.build_resumed_endpoints(
            base_url, resumed_session_id
        )
        self._interrupt_manager.store_resumed_session(None)
        return self.consume_events(
            resumed_endpoints,
            timeout,
            reporter,
            progress_task,
            on_event=on_event,
        )

    def _iter_events(self, events_url: str) -> Iterator[SSEEvent]:
        headers = self._get_client_headers()
        return self._sse_client.iter_events(events_url, headers=headers)

    def _get_client_headers(self) -> dict[str, str] | None:
        headers_fn = getattr(self._client, "headers", None)
        if not callable(headers_fn):
            return None
        try:
            headers_obj = headers_fn()
        except ServerAuthenticationError:
            raise
        except Exception as exc:
            LOGGER.warning("Failed to read client headers: %s", exc)
            return None
        if isinstance(headers_obj, dict) and all(
            isinstance(key, str) and isinstance(value, str) for key, value in headers_obj.items()
        ):
            return headers_obj
        if headers_obj is not None:
            LOGGER.warning(
                "Client headers() returned invalid type: %s",
                type(headers_obj).__name__,
            )
        return None

    # MARK: - Payload Helpers

    @staticmethod
    def _coerce_payload(payload: Any) -> dict[str, Any]:
        if isinstance(payload, dict):
            return payload
        try:
            decoded = loads(payload)
            return decoded if isinstance(decoded, dict) else {}
        except Exception:
            return {}
