from __future__ import annotations

import inspect
from typing import Any

from maivn._internal.utils.reporting.terminal_reporter import BaseReporter


class OrchestratorReporterHooksHelperMixin:
    @staticmethod
    def _get_sys_tool_id(payload: dict[str, Any]) -> str:
        assignment_id = str(payload.get("assignment_id", "")).strip()
        if assignment_id:
            return assignment_id
        assignment_index = payload.get("assignment_index")
        tool_name = str(payload.get("tool_name", "")).strip()
        if isinstance(assignment_index, int) and assignment_index >= 0:
            return (
                f"system-tool:{tool_name}:{assignment_index}"
                if tool_name
                else f"system-tool:{assignment_index}"
            )
        return f"system-tool:{tool_name}" if tool_name else "system-tool:unknown"

    @staticmethod
    def _map_action_status(status: str) -> str:
        if status in {"started", "running", "in_progress"}:
            return "in_progress"
        if status in {"completed", "success"}:
            return "completed"
        if status in {"failed", "error"}:
            return "failed"
        return status or "in_progress"

    def _handle_streaming_response_update(
        self: Any,
        payload: dict[str, Any],
        reporter: BaseReporter,
    ) -> None:
        full_text = payload.get("streaming_content")
        if not isinstance(full_text, str) or not full_text:
            return

        if self._is_nested and not self._allow_nested_response_stream:
            return

        assistant_id_raw = payload.get("assistant_id")
        assistant_id = (
            assistant_id_raw.strip()
            if isinstance(assistant_id_raw, str) and assistant_id_raw.strip()
            else "assistant"
        )

        previous = self._response_stream_text_by_assistant_id.get(assistant_id, "")
        delta = self._compute_stream_delta(previous, full_text)

        self._response_stream_text_by_assistant_id[assistant_id] = full_text

        if len(self._response_stream_text_by_assistant_id) > 64:
            stale_key = next(iter(self._response_stream_text_by_assistant_id))
            if stale_key != assistant_id:
                self._response_stream_text_by_assistant_id.pop(stale_key, None)

        if not delta:
            return

        reporter.report_response_chunk(
            delta,
            assistant_id=assistant_id,
            full_text=full_text,
        )

    @staticmethod
    def _compute_stream_delta(previous: str, full_text: str) -> str:
        if not full_text:
            return ""
        if not previous:
            return full_text
        if full_text.startswith(previous):
            return full_text[len(previous) :]
        if previous.startswith(full_text):
            return ""

        max_len = min(len(previous), len(full_text))
        shared = 0
        while shared < max_len and previous[shared] == full_text[shared]:
            shared += 1
        if shared == 0:
            return full_text
        return full_text[shared:]

    def _resolve_agent_name(self: Any, payload: dict[str, Any], tool_name: str) -> str | None:
        agent_name = payload.get("agent_name")
        if isinstance(agent_name, str) and agent_name.strip():
            return agent_name
        if tool_name:
            resolved = self._tool_agent_lookup(tool_name)
            if resolved:
                return resolved
        action_id = payload.get("action_id")
        if isinstance(action_id, str) and action_id.strip():
            return action_id
        return None

    def _resolve_swarm_name(self: Any, payload: dict[str, Any]) -> str | None:
        swarm_name = payload.get("swarm_name")
        if isinstance(swarm_name, str) and swarm_name.strip():
            return swarm_name
        return self._get_swarm_name()

    @staticmethod
    def _resolve_model_tool_id(payload: dict[str, Any], tool_name: str) -> str:
        assignment_id = payload.get("assignment_id")
        if isinstance(assignment_id, str) and assignment_id.strip():
            return assignment_id
        assignment_index = payload.get("assignment_index")
        if isinstance(assignment_index, int) and assignment_index >= 0:
            if tool_name:
                return f"model-tool:{tool_name}:{assignment_index}"
            return f"model-tool:{assignment_index}"
        return tool_name or "model-tool"

    def _get_enrichment_support(self: Any, reporter: BaseReporter) -> tuple[bool, bool, bool]:
        reporter_type = type(reporter)
        supports_scope, supports_memory, supports_redaction = (
            self._enrichment_support_by_reporter_type.get(
                reporter_type,
                (False, False, False),
            )
        )
        if reporter_type not in self._enrichment_support_by_reporter_type:
            try:
                params = inspect.signature(reporter.report_enrichment).parameters
            except (TypeError, ValueError):
                supports_scope = False
                supports_memory = False
                supports_redaction = False
            else:
                accepts_var_kwargs = any(
                    parameter.kind == inspect.Parameter.VAR_KEYWORD for parameter in params.values()
                )
                supports_scope = accepts_var_kwargs or "scope_id" in params
                supports_memory = accepts_var_kwargs or "memory" in params
                supports_redaction = accepts_var_kwargs or "redaction" in params
            self._enrichment_support_by_reporter_type[reporter_type] = (
                supports_scope,
                supports_memory,
                supports_redaction,
            )
        return supports_scope, supports_memory, supports_redaction

    @staticmethod
    def _extract_enrichment_values(
        payload: dict[str, Any],
    ) -> tuple[
        str,
        str,
        str | None,
        str | None,
        str | None,
        dict[str, Any] | None,
        dict[str, Any] | None,
    ]:
        phase = str(payload.get("phase", "")).strip()
        message = str(payload.get("message", "")).strip()

        scope_type_raw = payload.get("scope_type")
        scope_type = (
            scope_type_raw.strip().lower()
            if isinstance(scope_type_raw, str) and scope_type_raw.strip()
            else None
        )
        if scope_type not in {"agent", "swarm"}:
            scope_type = None

        scope_id_raw = payload.get("scope_id")
        scope_id = (
            scope_id_raw.strip() if isinstance(scope_id_raw, str) and scope_id_raw.strip() else None
        )
        scope_name_raw = payload.get("scope_name")
        scope_name = (
            scope_name_raw.strip()
            if isinstance(scope_name_raw, str) and scope_name_raw.strip()
            else None
        )
        memory = payload.get("memory") if isinstance(payload.get("memory"), dict) else None
        redaction = payload.get("redaction") if isinstance(payload.get("redaction"), dict) else None
        return phase, message, scope_type, scope_id, scope_name, memory, redaction

    @staticmethod
    def _build_enrichment_kwargs(
        *,
        phase: str,
        message: str,
        supports_scope: bool,
        supports_memory: bool,
        supports_redaction: bool,
        scope_id: str | None,
        scope_name: str | None,
        scope_type: str | None,
        memory: dict[str, Any] | None,
        redaction: dict[str, Any] | None,
    ) -> dict[str, Any]:
        kwargs: dict[str, Any] = {
            "phase": phase,
            "message": message or phase,
        }
        if supports_scope:
            kwargs["scope_id"] = scope_id
            kwargs["scope_name"] = scope_name
            kwargs["scope_type"] = scope_type
        if supports_memory and memory is not None:
            kwargs["memory"] = memory
        if supports_redaction and redaction is not None:
            kwargs["redaction"] = redaction
        return kwargs
