"""Tool normalization helpers for structured output flows."""

from __future__ import annotations

from maivn._internal.core.entities import BaseTool

# MARK: - Public API


def normalize_tools_for_structured_output(
    tools: list[BaseTool],
    structured_tool: BaseTool,
) -> list[BaseTool]:
    """Normalize tool list when structured output is enabled.

    If an existing tool uses the same model class as structured_tool,
    that tool is promoted to final_tool=True and no duplicate is added.
    """
    structured_model = getattr(structured_tool, "model", None)
    existing_match: BaseTool | None = None

    # Check if any existing tool uses the same model class
    if structured_model is not None:
        for tool in tools:
            tool_model = getattr(tool, "model", None)
            if tool_model is structured_model:
                existing_match = tool
                break

    # If we found an existing tool with the same model, use it as final
    if existing_match is not None:
        normalized: list[BaseTool] = []
        for tool in tools:
            if tool is existing_match:
                # Promote this tool to final_tool=True
                updated = _try_set_final_tool(tool, True)
                normalized.append(updated if updated is not None else tool)
            else:
                # Set other tools to final_tool=False
                if getattr(tool, "final_tool", False):
                    updated = _try_set_final_tool(tool, False)
                    if updated is None:
                        continue
                    tool = updated
                normalized.append(tool)
        return normalized

    # No existing match - use original logic
    normalized = []
    for tool in tools:
        if getattr(tool, "final_tool", False):
            updated = _try_set_final_tool(tool, False)
            if updated is None:
                continue
            tool = updated
        normalized.append(tool)

    normalized.append(structured_tool)

    stabilized: list[BaseTool] = []
    for tool in normalized[:-1]:
        if getattr(tool, "final_tool", False):
            updated = _try_set_final_tool(tool, False)
            if updated is None:
                continue
            tool = updated
        stabilized.append(tool)

    final_tool = normalized[-1]
    final_tool = _try_set_final_tool(final_tool, True) or final_tool
    stabilized.append(final_tool)

    return stabilized


# MARK: - Private Helpers


def _try_set_final_tool(tool: BaseTool, final_tool: bool) -> BaseTool | None:
    """Best-effort helper to set a tool's final_tool flag.

    Tries strategies in order of preference: pydantic model_copy, direct
    attribute set (fails on frozen models / assignment validators),
    object.__setattr__ (fails on slotted classes), and full rebuild via
    model_dump. Returns ``None`` only if every strategy failed.
    """
    # Narrow the exception set to the realistic failure modes for each strategy:
    # AttributeError (slots/frozen), TypeError (wrong shape), ValueError
    # (Pydantic ValidationError inherits from ValueError).
    expected = (AttributeError, TypeError, ValueError)

    if hasattr(tool, "model_copy"):
        try:
            return tool.model_copy(update={"final_tool": final_tool})
        except expected:
            pass

    try:
        tool.final_tool = final_tool
        return tool
    except expected:
        pass

    try:
        object.__setattr__(tool, "final_tool", final_tool)
        return tool
    except expected:
        pass

    if hasattr(tool, "model_dump"):
        try:
            data = tool.model_dump()
            data["final_tool"] = final_tool
            return tool.__class__(**data)
        except expected:
            pass

    return None


__all__ = ["normalize_tools_for_structured_output"]
