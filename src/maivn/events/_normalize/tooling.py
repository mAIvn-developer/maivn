"""Tool event normalization helpers."""

from __future__ import annotations

from typing import Any

from .context import NormalizationOptions
from .helpers import clean_text, coerce_mapping

# MARK: Tool Resolution


def extract_tool_identifier(tool_call: dict[str, Any]) -> str:
    for key in ("tool_id", "id", "name", "tool_name"):
        candidate = clean_text(tool_call.get(key))
        if candidate is not None:
            return candidate
    return ""


def extract_tool_name(
    tool_call: dict[str, Any],
    tool_id: str,
    options: NormalizationOptions,
) -> str:
    explicit_name = clean_text(tool_call.get("name") or tool_call.get("tool_name"))

    # Prefer canonical metadata/map names over the raw explicit name when it
    # matches the tool_id.  Dynamic agent invocation tools have UUID-based IDs
    # that the LLM API uses as the function ``name`` field; the metadata map
    # holds the human-readable agent name that should appear in the UI.
    metadata = options.tool_metadata_map.get(tool_id) if options.tool_metadata_map else None
    if isinstance(metadata, dict):
        metadata_name = clean_text(metadata.get("tool_name") or metadata.get("name"))
        if metadata_name is not None:
            return metadata_name

    mapped_name = options.tool_name_map.get(tool_id) if options.tool_name_map else None
    if mapped_name:
        return mapped_name

    if explicit_name is not None:
        return explicit_name
    if ":" in tool_id:
        return tool_id.rsplit(":", 1)[-1]
    return tool_id or "tool"


def extract_tool_type(
    tool_call: dict[str, Any],
    tool_id: str,
    options: NormalizationOptions,
) -> str:
    # Prefer canonical metadata over raw tool_call fields so that dynamic
    # agent invocation tools resolve to "agent" instead of the default "func".
    metadata = options.tool_metadata_map.get(tool_id) if options.tool_metadata_map else None
    if isinstance(metadata, dict):
        metadata_type = clean_text(metadata.get("tool_type"))
        if metadata_type is not None:
            return metadata_type.lower()

    explicit_type = clean_text(tool_call.get("tool_type") or tool_call.get("type"))
    if explicit_type is not None:
        return explicit_type.lower()
    return "func"


def extract_tool_scope(
    tool_id: str,
    *,
    tool_type: str,
    tool_name: str,
    options: NormalizationOptions,
) -> tuple[str | None, str | None]:
    metadata = options.tool_metadata_map.get(tool_id) if options.tool_metadata_map else None
    metadata_agent_name = None
    metadata_swarm_name = None
    if isinstance(metadata, dict):
        metadata_agent_name = clean_text(metadata.get("agent_name"))
        metadata_swarm_name = clean_text(metadata.get("swarm_name"))

    resolved_agent_name = metadata_agent_name
    if resolved_agent_name is None and tool_type == "agent":
        resolved_agent_name = tool_name
    if resolved_agent_name is None:
        resolved_agent_name = options.default_agent_name

    return resolved_agent_name, metadata_swarm_name or options.default_swarm_name


def extract_tool_args(
    tool_call: dict[str, Any],
    tool_id: str,
    *,
    tool_type: str,
    options: NormalizationOptions,
) -> dict[str, Any]:
    resolved_args = coerce_mapping(tool_call.get("args"))
    if tool_type != "agent":
        return resolved_args

    metadata = options.tool_metadata_map.get(tool_id) if options.tool_metadata_map else None
    if not isinstance(metadata, dict):
        return resolved_args

    target_agent_id = clean_text(metadata.get("target_agent_id"))
    if target_agent_id is not None and "agent_id" not in resolved_args:
        resolved_args["agent_id"] = target_agent_id
    return resolved_args
