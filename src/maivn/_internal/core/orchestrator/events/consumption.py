"""SSE event consumption helpers for orchestrators."""

# pyright: strict
from __future__ import annotations

import logging
from collections.abc import Callable, Iterator
from typing import Protocol, TypeAlias, cast

from maivn_shared import SessionClientProtocol, loads
from pydantic import JsonValue

from maivn._internal.core import SessionEndpoints, SSEEvent, ToolEventPayload, ToolEventValue
from maivn._internal.core.exceptions import ServerAuthenticationError
from maivn._internal.core.services import EventStreamHandlers
from maivn._internal.utils.reporting.terminal_reporter import BaseReporter

from .reporter_hooks import OrchestratorReporterHooks

LOGGER = logging.getLogger(__name__)


# MARK: Types

JsonObject: TypeAlias = dict[str, JsonValue]
ProgressTask: TypeAlias = object


class _EventProcessor(Protocol):
    def consume(
        self,
        *,
        events: Iterator[SSEEvent],
        resume_url: str,
        handlers: EventStreamHandlers,
        on_event: Callable[[SSEEvent], None] | None = None,
    ) -> JsonObject: ...


class _InterruptManager(Protocol):
    resumed_session_id: str | None

    def store_resumed_session(self, session_id: str | None) -> None: ...

    def should_chain(self, result: JsonObject) -> bool: ...

    def build_resumed_endpoints(self, base_url: str, session_id: str) -> SessionEndpoints: ...


class _ToolEventDispatcher(Protocol):
    def process_tool_requests(
        self,
        tool_events: dict[str, ToolEventPayload],
        resume_url: str,
    ) -> None: ...

    def process_tool_batch(
        self,
        tool_event_id: str,
        value: ToolEventValue,
        resume_url: str,
    ) -> None: ...

    def submit_tool_call(
        self,
        tool_event_id: str,
        tool_call_payload: JsonObject,
        resume_url: str,
    ) -> None: ...

    def acknowledge_barrier(self, tool_event_id: str, resume_url: str) -> None: ...


class _InterruptHandler(Protocol):
    def handle_user_input_request(
        self,
        tool_event_id: str,
        value: JsonObject,
        resume_url: str,
    ) -> None: ...

    def handle_interrupt_required(self, interrupt_data: JsonObject, resume_url: str) -> None: ...


class _SSEClient(Protocol):
    def iter_events(
        self,
        url: str,
        *,
        headers: dict[str, str] | None = None,
    ) -> Iterator[SSEEvent]: ...


# MARK: Event Consumption


class EventConsumptionCoordinator:
    """Coordinate SSE event consumption for orchestrators."""

    # MARK: - Initialization

    def __init__(
        self,
        *,
        client: SessionClientProtocol,
        event_processor: _EventProcessor,
        interrupt_manager: _InterruptManager,
        interrupt_service: object,
        tool_event_dispatcher: _ToolEventDispatcher,
        interrupt_handler: _InterruptHandler,
        sse_client: _SSEClient,
        reporter_hooks: OrchestratorReporterHooks,
        set_reporter_context: Callable[[BaseReporter | None, ProgressTask | None], None],
    ) -> None:
        self._client: SessionClientProtocol = client
        self._event_processor: _EventProcessor = event_processor
        self._interrupt_manager: _InterruptManager = interrupt_manager
        self._interrupt_service: object = interrupt_service
        self._tool_event_dispatcher: _ToolEventDispatcher = tool_event_dispatcher
        self._interrupt_handler: _InterruptHandler = interrupt_handler
        self._sse_client: _SSEClient = sse_client
        self._reporter_hooks: OrchestratorReporterHooks = reporter_hooks
        self._set_reporter_context: Callable[[BaseReporter | None, ProgressTask | None], None] = (
            set_reporter_context
        )

    # MARK: - Public API

    def consume_events(
        self,
        endpoints: SessionEndpoints,
        timeout: float,
        reporter: BaseReporter | None,
        progress_task: ProgressTask | None = None,
        on_event: Callable[[SSEEvent], None] | None = None,
    ) -> JsonObject:
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
        self, reporter: BaseReporter | None, progress_task: ProgressTask | None
    ) -> None:
        self._set_reporter_context(reporter, progress_task)
        set_reporter = getattr(self._interrupt_service, "set_reporter", None)
        if reporter and callable(set_reporter):
            _ = set_reporter(reporter)
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
        progress_task: ProgressTask | None,
        on_event: Callable[[SSEEvent], None] | None,
    ) -> JsonObject:
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
        result: JsonObject,
        endpoints: SessionEndpoints,
        timeout: float,
        reporter: BaseReporter | None,
        progress_task: ProgressTask | None,
        on_event: Callable[[SSEEvent], None] | None,
    ) -> JsonObject:
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
        progress_task: ProgressTask | None,
        on_event: Callable[[SSEEvent], None] | None,
    ) -> JsonObject:
        base_url = self._client.base_url or ""
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
        headers_attr = cast(object, getattr(self._client, "headers", None))
        if not callable(headers_attr):
            return None
        headers_fn = cast(Callable[[], object], headers_attr)
        try:
            headers_obj = headers_fn()
        except ServerAuthenticationError:
            raise
        except Exception as exc:  # noqa: BLE001 - missing headers should not abort streaming
            LOGGER.warning("Failed to read client headers: %s", exc)
            return None
        if isinstance(headers_obj, dict):
            headers = cast(dict[object, object], headers_obj)
            if all(
                isinstance(key, str) and isinstance(value, str) for key, value in headers.items()
            ):
                return {str(key): str(value) for key, value in headers.items()}
        if headers_obj is not None:
            headers_type = type(cast(object, headers_obj)).__name__
            LOGGER.warning(
                "Client headers() returned invalid type: %s",
                headers_type,
            )
        return None

    # MARK: - Payload Helpers

    @staticmethod
    def _coerce_payload(payload: object) -> JsonObject:
        if isinstance(payload, dict):
            return EventConsumptionCoordinator._coerce_mapping(cast(dict[object, object], payload))
        if not isinstance(payload, (str, bytes)):
            return {}
        try:
            decoded = cast(object, loads(payload))
            if isinstance(decoded, dict):
                return EventConsumptionCoordinator._coerce_mapping(
                    cast(dict[object, object], decoded)
                )
            return {}
        except Exception:  # noqa: BLE001 - invalid JSON payloads are ignored
            return {}

    @staticmethod
    def _coerce_mapping(mapping: dict[object, object]) -> JsonObject:
        return {str(key): cast(JsonValue, value) for key, value in mapping.items()}
