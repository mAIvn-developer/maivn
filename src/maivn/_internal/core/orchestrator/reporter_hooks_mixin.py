"""Shared reporter-hook helpers for orchestrator event consumers."""

# pyright: strict
from __future__ import annotations

import inspect
from collections.abc import Callable
from typing import TypeAlias, cast

from pydantic import JsonValue

from maivn._internal.utils.reporting.terminal_reporter import BaseReporter

# MARK: Types

JsonObject: TypeAlias = dict[str, JsonValue]
EnrichmentSupport: TypeAlias = tuple[bool, bool, bool, bool]
EnrichmentValues: TypeAlias = tuple[
    str,
    str,
    str | None,
    str | None,
    str | None,
    JsonObject | None,
    JsonObject | None,
    JsonObject | None,
]


# MARK: Reporter Hook Helpers


class OrchestratorReporterHooksHelperMixin:
    _is_nested: bool
    _allow_nested_response_stream: bool
    _response_stream_text_by_assistant_id: dict[str, str]
    _next_response_chunk_replaces: bool
    _enrichment_support_by_reporter_type: dict[type[BaseReporter], EnrichmentSupport]
    _tool_agent_lookup: Callable[[str], str | None]
    _swarm_name_supplier: Callable[[], str | None]

    def _get_swarm_name(self) -> str | None:
        return self._swarm_name_supplier()

    def __init__(self) -> None:
        self._is_nested = False
        self._allow_nested_response_stream = False
        self._response_stream_text_by_assistant_id = {}
        self._next_response_chunk_replaces = False
        self._enrichment_support_by_reporter_type = {}
        self._tool_agent_lookup = _no_agent_lookup
        self._swarm_name_supplier = _no_swarm_name

    @staticmethod
    def _get_sys_tool_id(payload: JsonObject) -> str:
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
        self,
        payload: JsonObject,
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
                _ = self._response_stream_text_by_assistant_id.pop(stale_key, None)

        if not delta:
            return

        # Detect a divergent stream: previous had content and the new cumulative text neither
        # extends nor retracts it. In that case the downstream UI must overwrite the current
        # bubble with the full new text instead of appending a suffix.
        replace_content = (
            bool(previous)
            and bool(full_text)
            and not (full_text.startswith(previous) or previous.startswith(full_text))
        )
        # A reevaluate cycle can mint a fresh assistant id, so the per-assistant cache may be
        # empty. The one-shot flag tells the next chunk to overwrite regardless of assistant id.
        if self._next_response_chunk_replaces:
            replace_content = True
            self._next_response_chunk_replaces = False
        if replace_content:
            delta = full_text

        try:
            reporter.report_response_chunk(
                delta,
                assistant_id=assistant_id,
                full_text=full_text,
                replace_content=replace_content,
            )
        except TypeError as exc:
            if "replace_content" not in str(exc):
                raise
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

    def _resolve_agent_name(self, payload: JsonObject, tool_name: str) -> str | None:
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

    def _resolve_swarm_name(self, payload: JsonObject) -> str | None:
        swarm_name = payload.get("swarm_name")
        if isinstance(swarm_name, str) and swarm_name.strip():
            return swarm_name
        return self._get_swarm_name()

    @staticmethod
    def _resolve_model_tool_id(payload: JsonObject, tool_name: str) -> str:
        assignment_id = payload.get("assignment_id")
        if isinstance(assignment_id, str) and assignment_id.strip():
            return assignment_id
        assignment_index = payload.get("assignment_index")
        if isinstance(assignment_index, int) and assignment_index >= 0:
            if tool_name:
                return f"model-tool:{tool_name}:{assignment_index}"
            return f"model-tool:{assignment_index}"
        return tool_name or "model-tool"

    def _get_enrichment_support(self, reporter: BaseReporter) -> EnrichmentSupport:
        reporter_type = type(reporter)
        supports = self._enrichment_support_by_reporter_type.get(reporter_type)
        if supports is not None:
            return supports
        try:
            params = inspect.signature(reporter.report_enrichment).parameters
        except (TypeError, ValueError):
            supports_scope = False
            supports_memory = False
            supports_redaction = False
            supports_reevaluate = False
        else:
            accepts_var_kwargs = any(
                parameter.kind == inspect.Parameter.VAR_KEYWORD for parameter in params.values()
            )
            supports_scope = accepts_var_kwargs or "scope_id" in params
            supports_memory = accepts_var_kwargs or "memory" in params
            supports_redaction = accepts_var_kwargs or "redaction" in params
            supports_reevaluate = (
                accepts_var_kwargs
                or "reevaluate" in params
                or "source" in params
                or "trigger_tool" in params
            )
        result = (supports_scope, supports_memory, supports_redaction, supports_reevaluate)
        self._enrichment_support_by_reporter_type[reporter_type] = result
        return result

    @classmethod
    def _extract_enrichment_values(cls, payload: JsonObject) -> EnrichmentValues:
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
        memory = cls._coerce_object_dict(payload.get("memory"))
        redaction = cls._coerce_object_dict(payload.get("redaction"))
        reevaluate = cls._coerce_object_dict(payload.get("reevaluate"))

        # Synthesize the group from flat fields when only scalars were
        # emitted (e.g. third-party emitters).
        if reevaluate is None:
            flat_reevaluate = {
                key: payload[key]
                for key in (
                    "source",
                    "trigger_tool",
                    "target_tool",
                    "reevaluate_count",
                    "collected_count",
                )
                if key in payload
            }
            if flat_reevaluate:
                reevaluate = flat_reevaluate
        return (
            phase,
            message,
            scope_type,
            scope_id,
            scope_name,
            memory,
            redaction,
            reevaluate,
        )

    @staticmethod
    def _build_enrichment_kwargs(
        *,
        phase: str,
        message: str,
        supports_scope: bool,
        supports_memory: bool,
        supports_redaction: bool,
        supports_reevaluate: bool,
        scope_id: str | None,
        scope_name: str | None,
        scope_type: str | None,
        memory: JsonObject | None,
        redaction: JsonObject | None,
        reevaluate: JsonObject | None,
    ) -> JsonObject:
        kwargs: JsonObject = {
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
        if supports_reevaluate and reevaluate:
            for key in ("source", "trigger_tool", "target_tool"):
                value = reevaluate.get(key)
                if value is None:
                    continue
                kwargs[key] = value
            for key in ("reevaluate_count", "collected_count"):
                value = reevaluate.get(key)
                if isinstance(value, int):
                    kwargs[key] = value
        return kwargs

    @staticmethod
    def _coerce_object_dict(value: object) -> JsonObject | None:
        if not isinstance(value, dict):
            return None
        raw_mapping = cast(dict[object, object], value)
        return {str(key): cast(JsonValue, item) for key, item in raw_mapping.items()}


def _no_agent_lookup(_name: str) -> str | None:
    return None


def _no_swarm_name() -> str | None:
    return None
