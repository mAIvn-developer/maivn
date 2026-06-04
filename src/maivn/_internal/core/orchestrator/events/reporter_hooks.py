"""Reporter-facing callbacks for orchestrator events."""

# pyright: strict
from __future__ import annotations

from collections.abc import Callable
from typing import TypeAlias, cast

from pydantic import JsonValue

from maivn._internal.utils.reporting.terminal_reporter import BaseReporter

from ..reporter_hooks_mixin import OrchestratorReporterHooksHelperMixin

# MARK: Constants

# System tools that should NEVER emit events to SDK reporters.
# These are completely internal system tools that should not be visible to end users.
# - reevaluate: Internal re-evaluation mechanism; triggers new planning phases but
#   should not appear as user-facing tool executions in the UI.
_SILENT_SYSTEM_TOOLS = frozenset({"reevaluate"})


# MARK: Types

JsonObject: TypeAlias = dict[str, JsonValue]
ToolArgs: TypeAlias = dict[str, object]
ReportEnrichment: TypeAlias = Callable[..., object]


# MARK: Reporter Hooks


class OrchestratorReporterHooks(OrchestratorReporterHooksHelperMixin):
    """Reporter-facing callbacks for tool events."""

    _get_reporter: Callable[[], BaseReporter | None]
    _tool_agent_lookup: Callable[[str], str | None]
    _swarm_name_supplier: Callable[[], str | None]
    _response_stream_text_by_assistant_id: dict[str, str]
    _next_response_chunk_replaces: bool
    _is_nested: bool
    _allow_nested_response_stream: bool
    _enrichment_support_by_reporter_type: dict[type[BaseReporter], tuple[bool, bool, bool, bool]]

    # MARK: - Initialization

    def __init__(
        self,
        reporter_supplier: Callable[[], BaseReporter | None],
        tool_agent_lookup: Callable[[str], str | None] | None = None,
        swarm_name_supplier: Callable[[], str | None] | None = None,
    ) -> None:
        super().__init__()
        self._get_reporter = reporter_supplier
        self._tool_agent_lookup = tool_agent_lookup or _no_agent_lookup
        self._swarm_name_supplier = swarm_name_supplier or _no_swarm_name

    # MARK: - Public Hooks

    def configure_execution_context(
        self,
        *,
        is_nested: bool,
        allow_nested_response_stream: bool,
    ) -> None:
        self._is_nested = is_nested
        self._allow_nested_response_stream = allow_nested_response_stream

    def handle_model_tool_complete(self, payload: JsonObject) -> None:
        reporter = self._get_reporter()
        if not reporter:
            return

        tool_name = str(payload.get("tool_name", "")).strip()
        if not tool_name:
            return

        tool_id = self._resolve_model_tool_id(payload, tool_name)
        agent_name = self._resolve_agent_name(payload, tool_name)
        swarm_name = self._resolve_swarm_name(payload)
        result = payload.get("result")
        private_data_injected = payload.get("private_data_injected")
        interrupt_data_injected = payload.get("interrupt_data_injected")
        result_for_display: object = result
        if private_data_injected or interrupt_data_injected:
            wrapped_result: JsonObject = {"result": result}
            if private_data_injected:
                wrapped_result["private_data_injected"] = private_data_injected
            if interrupt_data_injected:
                wrapped_result["interrupt_data_injected"] = interrupt_data_injected
            result_for_display = wrapped_result

        reporter.report_model_tool_complete(
            tool_name,
            event_id=tool_id,
            agent_name=agent_name,
            swarm_name=swarm_name,
            result=result_for_display,
        )

    def handle_action_update(self, payload: JsonObject) -> None:
        reporter = self._get_reporter()
        if not reporter:
            return

        self._handle_streaming_response_update(payload, reporter)

        action_type = str(payload.get("action_type", "")).strip().lower()
        if action_type != "swarm_agent":
            return

        action_id = payload.get("action_id")
        action_id_text = str(action_id).strip() if action_id is not None else ""
        if action_id_text.lower() == "none":
            action_id_text = ""

        action_name = str(payload.get("action_name", "")).strip()
        agent_name = action_name or action_id_text or "unknown-agent"
        status_raw = str(payload.get("status", "")).strip().lower()
        assignment_status = self._map_action_status(status_raw)
        swarm_name = self._resolve_swarm_name(payload)
        error = payload.get("error")
        result = payload.get("result")

        # The action id is stable per swarm assignment. The fallback remains name-stable
        # so status updates for the same invocation collapse to one card.
        reporter.report_agent_assignment(
            agent_name=agent_name,
            status=assignment_status,
            assignment_id=action_id_text or f"agent:{agent_name}",
            swarm_name=swarm_name,
            error=str(error) if error else None,
            result=result,
        )

    def handle_system_tool_start(self, payload: JsonObject) -> None:
        raw_tool_name = str(payload.get("tool_name", "")).strip()
        normalized = raw_tool_name.lower()
        if normalized in _SILENT_SYSTEM_TOOLS:
            return

        reporter = self._get_reporter()
        if not reporter:
            return

        agent_name = self._resolve_agent_name(payload, raw_tool_name)
        swarm_name = self._resolve_swarm_name(payload)
        reporter.report_tool_start(
            raw_tool_name,
            self._get_sys_tool_id(payload),
            tool_type="system",
            agent_name=agent_name,
            tool_args=cast(ToolArgs | None, self._coerce_object_dict(payload.get("args"))),
            swarm_name=swarm_name,
        )

    def handle_system_tool_chunk(self, payload: JsonObject) -> None:
        tool_name = str(payload.get("tool_name", "")).strip().lower()
        if tool_name in _SILENT_SYSTEM_TOOLS:
            return

        reporter = self._get_reporter()
        if not reporter:
            return

        reporter.report_system_tool_progress(
            event_id=self._get_sys_tool_id(payload),
            tool_name=str(payload.get("tool_name", "")),
            chunk_count=self._as_int(payload.get("chunk_count"), default=0),
            elapsed_seconds=self._as_float(payload.get("elapsed_seconds"), default=0.0),
            text=self._as_optional_str(payload.get("text")),
        )

    def handle_system_tool_complete(self, payload: JsonObject) -> None:
        tool_name = str(payload.get("tool_name", "")).strip()
        if tool_name.lower() in _SILENT_SYSTEM_TOOLS:
            return

        reporter = self._get_reporter()
        if not reporter:
            return

        reporter.report_tool_complete(
            self._get_sys_tool_id(payload),
            elapsed_ms=self._as_optional_int(payload.get("elapsed_ms")),
            result=payload.get("result"),
        )

    def handle_system_tool_error(self, payload: JsonObject) -> None:
        tool_name = str(payload.get("tool_name", "")).strip().lower()
        if tool_name in _SILENT_SYSTEM_TOOLS:
            return

        reporter = self._get_reporter()
        if not reporter:
            return

        reporter.report_tool_error(
            str(payload.get("tool_name", "")),
            str(payload.get("error", "")),
            event_id=self._get_sys_tool_id(payload),
            elapsed_ms=self._as_optional_int(payload.get("elapsed_ms")),
        )

    def handle_status_message(self, payload: JsonObject) -> None:
        """Forward a standalone status message to the reporter."""
        reporter = self._get_reporter()
        if not reporter:
            return

        message = payload.get("message")
        if not isinstance(message, str) or not message.strip():
            return

        assistant_id_raw = payload.get("assistant_id")
        assistant_id = (
            assistant_id_raw.strip()
            if isinstance(assistant_id_raw, str) and assistant_id_raw.strip()
            else "assistant"
        )

        reporter.report_status_message(message.strip(), assistant_id=assistant_id)

    def handle_enrichment(self, payload: JsonObject) -> None:
        reporter = self._get_reporter()
        if not reporter:
            return

        (
            phase,
            message,
            scope_type,
            scope_id,
            scope_name,
            memory,
            redaction,
            reevaluate,
        ) = self._extract_enrichment_values(payload)
        if not phase:
            return

        if phase == "reevaluate_accrued":
            self._response_stream_text_by_assistant_id.clear()
            self._next_response_chunk_replaces = True

        if self._is_nested and not scope_type:
            return

        supports_scope, supports_memory, supports_redaction, supports_reevaluate = (
            self._get_enrichment_support(reporter)
        )
        kwargs = self._build_enrichment_kwargs(
            phase=phase,
            message=message,
            supports_scope=supports_scope,
            supports_memory=supports_memory,
            supports_redaction=supports_redaction,
            supports_reevaluate=supports_reevaluate,
            scope_id=scope_id,
            scope_name=scope_name,
            scope_type=scope_type,
            memory=memory,
            redaction=redaction,
            reevaluate=reevaluate,
        )
        _ = cast(ReportEnrichment, reporter.report_enrichment)(**kwargs)

    # MARK: - Coercion Helpers

    @staticmethod
    def _as_optional_str(value: object) -> str | None:
        return value if isinstance(value, str) else None

    @staticmethod
    def _as_optional_int(value: object) -> int | None:
        return value if isinstance(value, int) else None

    @classmethod
    def _as_int(cls, value: object, *, default: int) -> int:
        result = cls._as_optional_int(value)
        return result if result is not None else default

    @staticmethod
    def _as_float(value: object, *, default: float) -> float:
        if isinstance(value, (int, float)):
            return float(value)
        return default


# MARK: Defaults


def _no_agent_lookup(_name: str) -> str | None:
    return None


def _no_swarm_name() -> str | None:
    return None
