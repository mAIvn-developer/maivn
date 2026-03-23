"""Tool and assistant forwarding mixin for EventRouterReporter."""

from __future__ import annotations

from typing import Any

from ..event_categories import resolve_tool_category, resolve_tool_category_from_event_id

# MARK: Tool Forwarding


class ToolRouterMixin:
    _tool_category_by_event_id: dict[str, str]
    _forward: Any
    _reporter: Any

    def report_tool_start(
        self,
        tool_name: str,
        event_id: str,
        tool_type: str | None = None,
        agent_name: str | None = None,
        tool_args: dict[str, Any] | None = None,
        swarm_name: str | None = None,
    ) -> None:
        category = resolve_tool_category(tool_type)
        self._tool_category_by_event_id[str(event_id)] = category
        self._forward(
            category=category,
            event_name="tool_start",
            payload={
                "tool_name": tool_name,
                "event_id": event_id,
                "tool_type": tool_type,
                "agent_name": agent_name,
                "tool_args": tool_args,
                "swarm_name": swarm_name,
            },
            forward=lambda: self._reporter.report_tool_start(
                tool_name,
                event_id,
                tool_type,
                agent_name,
                tool_args,
                swarm_name,
            ),
        )

    def report_tool_complete(
        self,
        event_id: str,
        elapsed_ms: int | None = None,
        result: Any | None = None,
    ) -> None:
        event_key = str(event_id)
        category = resolve_tool_category_from_event_id(
            event_key,
            self._tool_category_by_event_id,
        )
        self._tool_category_by_event_id.pop(event_key, None)
        self._forward(
            category=category,
            event_name="tool_complete",
            payload={
                "event_id": event_id,
                "elapsed_ms": elapsed_ms,
                "result": result,
            },
            forward=lambda: self._reporter.report_tool_complete(event_id, elapsed_ms, result),
        )

    def report_tool_error(
        self,
        tool_name: str,
        error: str,
        event_id: str | None = None,
        elapsed_ms: int | None = None,
    ) -> None:
        event_key = str(event_id).strip() if event_id is not None else ""
        category = resolve_tool_category_from_event_id(
            event_key,
            self._tool_category_by_event_id,
        )
        if not event_key:
            category = resolve_tool_category(None)
        if event_key:
            self._tool_category_by_event_id.pop(event_key, None)
        self._forward(
            category=category,
            event_name="tool_error",
            payload={
                "tool_name": tool_name,
                "error": error,
                "event_id": event_id,
                "elapsed_ms": elapsed_ms,
            },
            forward=lambda: self._reporter.report_tool_error(
                tool_name,
                error,
                event_id,
                elapsed_ms,
            ),
        )

    def report_model_tool_complete(
        self,
        tool_name: str,
        event_id: str | None = None,
        agent_name: str | None = None,
        swarm_name: str | None = None,
        result: Any | None = None,
    ) -> None:
        event_key = str(event_id).strip() if event_id is not None else ""
        if event_key:
            self._tool_category_by_event_id[event_key] = "model"
        try:
            self._forward(
                category="model",
                event_name="model_tool_complete",
                payload={
                    "tool_name": tool_name,
                    "event_id": event_id,
                    "agent_name": agent_name,
                    "swarm_name": swarm_name,
                    "result": result,
                },
                forward=lambda: self._reporter.report_model_tool_complete(
                    tool_name,
                    event_id=event_id,
                    agent_name=agent_name,
                    swarm_name=swarm_name,
                    result=result,
                ),
            )
        finally:
            if event_key:
                self._tool_category_by_event_id.pop(event_key, None)


# MARK: Assistant Forwarding


class AssistantRouterMixin:
    _forward: Any
    _reporter: Any

    def report_response_chunk(
        self,
        text: str,
        *,
        assistant_id: str | None = None,
        full_text: str | None = None,
    ) -> None:
        self._forward(
            category="response",
            event_name="response_chunk",
            payload={
                "text": text,
                "assistant_id": assistant_id,
                "full_text": full_text,
            },
            forward=lambda: self._reporter.report_response_chunk(
                text,
                assistant_id=assistant_id,
                full_text=full_text,
            ),
        )

    def report_status_message(
        self,
        message: str,
        *,
        assistant_id: str | None = None,
    ) -> None:
        self._forward(
            category="lifecycle",
            event_name="status_message",
            payload={"message": message, "assistant_id": assistant_id},
            forward=lambda: self._reporter.report_status_message(
                message,
                assistant_id=assistant_id,
            ),
        )
