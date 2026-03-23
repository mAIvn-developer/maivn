"""Reporter helpers for ToolEventDispatcher."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from maivn._internal.utils.reporting.terminal_reporter import BaseReporter

if TYPE_CHECKING:
    from .dispatcher import ToolEventDispatcher


# MARK: Reporter Helpers


def report_tool_start(
    dispatcher: ToolEventDispatcher,
    tool_id: str,
    tool_event_id: str,
    reporter: BaseReporter | None,
    progress_task: Any | None,
    tool_args: dict[str, Any] | None,
    *,
    private_data_injected: Any = None,
    interrupt_data_injected: Any = None,
) -> None:
    """Notify reporter that a tool started executing."""
    if not reporter:
        return

    tool_name = dispatcher._get_tool_name(tool_id)
    tool_type = resolve_tool_type(dispatcher, tool_id)

    target_agent_id: str | None = None
    try:
        tool = dispatcher._tool_execution_service.resolve_tool(tool_id)
        agent_id = getattr(tool, "target_agent_id", None) or getattr(tool, "agent_id", None)
        if isinstance(agent_id, str) and agent_id.strip():
            target_agent_id = agent_id.strip()
    except Exception:  # noqa: BLE001
        target_agent_id = None

    agent_name = dispatcher._tool_agent_lookup(tool_id)
    swarm_name = dispatcher._get_swarm_name()
    safe_args = sanitize_args_for_reporting(
        args=tool_args,
        private_data_injected=private_data_injected,
        interrupt_data_injected=interrupt_data_injected,
    )
    if tool_type == "agent" and target_agent_id:
        safe_args = dict(safe_args or {})
        safe_args["agent_id"] = target_agent_id

    reporter.report_tool_start(
        tool_name,
        tool_event_id,
        tool_type,
        agent_name,
        safe_args,
        swarm_name,
    )
    if progress_task:
        reporter.update_progress(progress_task, f"Executing {tool_name}...")


def report_tool_complete(
    tool_event_id: str,
    elapsed_ms_value: int,
    result: Any,
    reporter: BaseReporter | None,
    *,
    private_data_injected: Any = None,
    interrupt_data_injected: Any = None,
) -> None:
    """Notify reporter of tool completion with contextual metadata."""
    if not reporter:
        return

    result_for_display: Any = result
    if private_data_injected or interrupt_data_injected:
        result_for_display = {"result": result}
        if private_data_injected:
            result_for_display["private_data_injected"] = private_data_injected
        if interrupt_data_injected:
            result_for_display["interrupt_data_injected"] = interrupt_data_injected

    reporter.report_tool_complete(tool_event_id, elapsed_ms_value, result_for_display)


def report_tool_error(
    tool_id: str,
    error_message: str,
    tool_event_id: str,
    reporter: BaseReporter | None,
) -> None:
    """Notify reporter of tool failure."""
    if reporter:
        reporter.report_tool_error(tool_id, error_message, event_id=tool_event_id)


# MARK: Tool Metadata Helpers


def resolve_tool_type(dispatcher: ToolEventDispatcher, tool_id: str) -> str:
    """Infer tool type for reporter output."""
    try:
        tool = dispatcher._tool_execution_service.resolve_tool(tool_id)
        tool_type = getattr(tool, "tool_type", None)
        if tool_type:
            return str(tool_type)
        if hasattr(tool, "tags") and tool.tags and "agent_invocation" in tool.tags:
            return "agent"
        if tool.__class__.__name__ == "ModelTool":
            return "model"
    except Exception:  # noqa: BLE001
        pass
    return "func"


def summarize_injected_keys(payload: Any) -> list[str]:
    """Return a safe summary of injected keys (never values)."""
    if isinstance(payload, list):
        return [str(item) for item in payload]
    if isinstance(payload, dict):
        return [str(key) for key in payload.keys()]
    if payload is None:
        return []
    return [type(payload).__name__]


def sanitize_args_for_reporting(
    args: dict[str, Any] | None,
    *,
    private_data_injected: Any,
    interrupt_data_injected: Any,
) -> dict[str, Any] | None:
    """Return a safe tool args payload for terminal reporters."""
    private_keys = summarize_injected_keys(private_data_injected)
    interrupt_keys = summarize_injected_keys(interrupt_data_injected)
    if not isinstance(args, dict):
        return None

    if not args and not private_keys and not interrupt_keys:
        return None

    safe: dict[str, Any] = {"arg_keys": sorted(str(key) for key in args.keys())}
    for key in ("use_as_final_output", "force_final_tool"):
        if isinstance(args.get(key), bool):
            safe[key] = args[key]
    if isinstance(args.get("model"), str):
        safe["model"] = args["model"]
    if private_keys:
        safe["private_data_injected"] = private_keys
    if interrupt_keys:
        safe["interrupt_data_injected"] = interrupt_keys
    return safe
