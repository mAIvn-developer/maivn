"""Reporter-facing callbacks for orchestrator events."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from maivn._internal.utils.reporting.terminal_reporter import BaseReporter

from ..reporter_hooks import OrchestratorReporterHooksHelperMixin

# System tools that should NEVER emit events to SDK reporters.
# These are completely internal system tools that should not be visible to end users.
# - reevaluate: Internal re-evaluation mechanism; triggers new planning phases but
#   should not appear as user-facing tool executions in the UI.
_SILENT_SYSTEM_TOOLS = frozenset({"reevaluate"})


class OrchestratorReporterHooks(OrchestratorReporterHooksHelperMixin):
    """Reporter-facing callbacks for tool events."""

    # MARK: - Initialization

    def __init__(
        self,
        reporter_supplier: Callable[[], BaseReporter | None],
        tool_agent_lookup: Callable[[str], str | None] | None = None,
        swarm_name_supplier: Callable[[], str | None] | None = None,
    ) -> None:
        self._get_reporter = reporter_supplier
        self._tool_agent_lookup = tool_agent_lookup or (lambda _name: None)
        self._get_swarm_name = swarm_name_supplier or (lambda: None)
        # Tracks full streamed response text by assistant source to emit clean deltas.
        self._response_stream_text_by_assistant_id: dict[str, str] = {}
        # When True, streaming response chunks are suppressed to prevent nested
        # agent synthesis content from appearing as the main response in the UI.
        self._is_nested = False
        # When True, nested orchestrators are allowed to forward streaming
        # assistant chunks (used for swarm use_as_final_output agent responses).
        self._allow_nested_response_stream = False
        # Cache whether a reporter implementation accepts enrichment scope/memory args.
        self._enrichment_support_by_reporter_type: dict[type[Any], tuple[bool, bool, bool]] = {}

    # MARK: - Public Hooks

    def handle_model_tool_complete(self, payload: dict[str, Any]) -> None:
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
        result_for_display = result
        if private_data_injected or interrupt_data_injected:
            result_for_display = {"result": result}
            if private_data_injected:
                result_for_display["private_data_injected"] = private_data_injected
            if interrupt_data_injected:
                result_for_display["interrupt_data_injected"] = interrupt_data_injected

        reporter.report_model_tool_complete(
            tool_name,
            event_id=tool_id,
            agent_name=agent_name,
            swarm_name=swarm_name,
            result=result_for_display,
        )

    def handle_action_update(self, payload: dict[str, Any]) -> None:
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

        report_agent_assignment = getattr(reporter, "report_agent_assignment", None)
        if callable(report_agent_assignment):
            report_agent_assignment(
                agent_name=agent_name,
                status=assignment_status,
                assignment_id=action_id_text or f"agent:{agent_name}",
                swarm_name=swarm_name,
                error=str(error) if error else None,
                result=result,
            )

    def handle_system_tool_start(self, payload: dict[str, Any]) -> None:
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
            tool_args=payload.get("args") if isinstance(payload.get("args"), dict) else None,
            swarm_name=swarm_name,
        )

    def handle_system_tool_chunk(self, payload: dict[str, Any]) -> None:
        tool_name = str(payload.get("tool_name", "")).strip().lower()
        if tool_name in _SILENT_SYSTEM_TOOLS:
            return

        reporter = self._get_reporter()
        if not reporter:
            return

        reporter.report_system_tool_progress(
            event_id=self._get_sys_tool_id(payload),
            tool_name=str(payload.get("tool_name", "")),
            chunk_count=payload.get("chunk_count", 0),
            elapsed_seconds=payload.get("elapsed_seconds", 0.0),
            text=payload.get("text"),
        )

    def handle_system_tool_complete(self, payload: dict[str, Any]) -> None:
        tool_name = str(payload.get("tool_name", "")).strip()
        if tool_name.lower() in _SILENT_SYSTEM_TOOLS:
            return

        reporter = self._get_reporter()
        if not reporter:
            return

        reporter.report_tool_complete(
            self._get_sys_tool_id(payload),
            elapsed_ms=payload.get("elapsed_ms"),
            result=payload.get("result"),
        )

    def handle_system_tool_error(self, payload: dict[str, Any]) -> None:
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
            elapsed_ms=payload.get("elapsed_ms"),
        )

    def handle_status_message(self, payload: dict[str, Any]) -> None:
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

    def handle_enrichment(self, payload: dict[str, Any]) -> None:
        reporter = self._get_reporter()
        if not reporter:
            return

        phase, message, scope_type, scope_id, scope_name, memory, redaction = (
            self._extract_enrichment_values(payload)
        )
        if not phase:
            return

        if self._is_nested and not scope_type:
            return

        supports_scope, supports_memory, supports_redaction = self._get_enrichment_support(reporter)
        kwargs = self._build_enrichment_kwargs(
            phase=phase,
            message=message,
            supports_scope=supports_scope,
            supports_memory=supports_memory,
            supports_redaction=supports_redaction,
            scope_id=scope_id,
            scope_name=scope_name,
            scope_type=scope_type,
            memory=memory,
            redaction=redaction,
        )
        reporter.report_enrichment(**kwargs)
