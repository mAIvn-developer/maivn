"""Unified per-tool override applied at registration time.

A single ``ToolOverride`` shape works across all three registration paths:

* ``agent.add_tool(tool, override=ToolOverride(...))`` — customize a
  single function/model tool when registering it directly.
* ``agent.add_toolset(instance, overrides={"method_name":
  ToolOverride(...)})`` — customize specific methods of a generic
  ``@toolset`` without modifying the class.
* ``MCPServer(..., tool_overrides={"mcp_tool_name": ToolOverride(...)})``
  — customize specific tools exposed by an MCP server.

Every field is optional. Only fields you set are applied; everything
else falls through to whatever the decorator / registration call would
normally produce. The merge is non-destructive: ``tags`` and
``metadata`` are *added to* the existing values, ``dependencies`` are
*appended*. Scalar fields (``name``, ``description``, ``always_execute``,
``final_tool``) are *replaced* when set.
"""

# pyright: strict
from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from typing import TypeAlias

from maivn_shared import BaseDependency

# MARK: Types

ToolHook: TypeAlias = Callable[..., object]
ToolMapping: TypeAlias = Mapping[str, object]


# MARK: ToolOverride


@dataclass(frozen=True)
class ToolOverride:
    """Per-tool configuration applied at registration time.

    Use to retarget a generic tool for a specific application without
    touching its decorator. Common patterns:

    * Pin a discovery tool with ``always_execute=True`` so the planner
      can't skip it before the final-output tool.
    * Bake in a default query / format with ``default_args``.
    * Attach app-specific dependencies (e.g.
      ``dependencies=[depends_on_private_data("user_id", "id")]``).
    * Rename or re-describe a tool to make its purpose unambiguous in
      the current app's context.

    Attributes:
        name: Replace the tool's exposed name (post-prefix). For
            ``add_toolset`` overrides the key is the bare method name;
            this field renames the public tool only.
        description: Replace the tool's description (what the LLM sees).
            Useful when a generic tool needs an app-specific framing.
        tags: Extra tags merged onto whatever the tool already carries.
            Never replaces — only adds.
        metadata: Extra metadata merged onto the tool's metadata dict.
            Top-level keys are added; existing keys are overwritten.
        always_execute: When set, force the planner to schedule this
            tool. ``True`` pins it, ``False`` un-pins. ``None`` (default)
            leaves the decorator-supplied value alone.
        final_tool: When set, mark (or un-mark) this tool as the final
            structured output. Same semantics as :attr:`always_execute`.
        default_args: Argument defaults applied at execution time before
            model-supplied args. Model-supplied args win. For MCP tools,
            this merges over ``default_tool_args`` / ``tool_defaults``.
        dependencies: Extra dependencies appended to the tool's
            dependency list. Accepts the existing decorator helpers'
            output (e.g. ``depends_on_tool(...)``,
            ``depends_on_private_data(...)``).
        before_execute: Replace the tool's pre-execution hook.
        after_execute: Replace the tool's post-execution hook.
    """

    name: str | None = None
    description: str | None = None
    tags: Sequence[str] | None = None
    metadata: ToolMapping | None = None
    always_execute: bool | None = None
    final_tool: bool | None = None
    default_args: ToolMapping | None = None
    dependencies: Sequence[BaseDependency] | None = None
    before_execute: ToolHook | None = None
    after_execute: ToolHook | None = None

    def is_empty(self) -> bool:
        """Return ``True`` when no field is set (the override is a no-op)."""
        return (
            self.name is None
            and self.description is None
            and not self.tags
            and not self.metadata
            and self.always_execute is None
            and self.final_tool is None
            and not self.default_args
            and not self.dependencies
            and self.before_execute is None
            and self.after_execute is None
        )


@dataclass(frozen=True)
class _AppliedOverride:
    """Internal helper carrying the merged result of an override application."""

    name: str | None
    description: str | None
    tags: list[str]
    metadata: dict[str, object]
    always_execute: bool
    final_tool: bool
    default_args: dict[str, object]
    dependencies: list[BaseDependency] = field(default_factory=list)
    before_execute: ToolHook | None = None
    after_execute: ToolHook | None = None


# MARK: Override Application


def apply_override(
    override: ToolOverride | None,
    *,
    base_name: str | None,
    base_description: str | None,
    base_tags: Sequence[str] | None,
    base_metadata: ToolMapping | None,
    base_always_execute: bool,
    base_final_tool: bool,
    base_default_args: ToolMapping | None = None,
    base_dependencies: Sequence[BaseDependency] | None = None,
    base_before_execute: ToolHook | None = None,
    base_after_execute: ToolHook | None = None,
) -> _AppliedOverride:
    """Merge a :class:`ToolOverride` over the registration defaults.

    Scalars (``name``, ``description``, ``always_execute``,
    ``final_tool``, ``before_execute``, ``after_execute``) are replaced
    when the override sets them. ``tags`` and ``dependencies`` are
    appended (dedup preserved for tags). ``metadata`` and
    ``default_args`` are dict-merged (override keys win).
    """
    merged_tags: list[str] = list(base_tags or [])
    merged_metadata: dict[str, object] = dict(base_metadata or {})
    merged_default_args: dict[str, object] = dict(base_default_args or {})
    merged_dependencies: list[BaseDependency] = list(base_dependencies or [])

    name = base_name
    description = base_description
    always_execute = base_always_execute
    final_tool = base_final_tool
    before_execute = base_before_execute
    after_execute = base_after_execute

    if override is not None:
        if override.name is not None:
            name = override.name
        if override.description is not None:
            description = override.description
        if override.tags:
            for tag in override.tags:
                if tag not in merged_tags:
                    merged_tags.append(tag)
        if override.metadata:
            merged_metadata.update(override.metadata)
        if override.always_execute is not None:
            always_execute = override.always_execute
        if override.final_tool is not None:
            final_tool = override.final_tool
        if override.default_args:
            merged_default_args.update(override.default_args)
        if override.dependencies:
            for dep in override.dependencies:
                if dep not in merged_dependencies:
                    merged_dependencies.append(dep)
        if override.before_execute is not None:
            before_execute = override.before_execute
        if override.after_execute is not None:
            after_execute = override.after_execute

    return _AppliedOverride(
        name=name,
        description=description,
        tags=merged_tags,
        metadata=merged_metadata,
        always_execute=always_execute,
        final_tool=final_tool,
        default_args=merged_default_args,
        dependencies=merged_dependencies,
        before_execute=before_execute,
        after_execute=after_execute,
    )


# MARK: Exports

__all__ = ["ToolOverride", "apply_override"]
