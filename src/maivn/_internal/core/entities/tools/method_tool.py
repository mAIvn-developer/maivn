"""Method-based tool entity.

A :class:`MethodTool` wraps a bound method of a toolset instance. It is the
result of :func:`maivn.toolify` plus :func:`maivn.toolset` plus
``scope.add_toolset(instance)``: each ``@toolify``-marked method on a
``@toolset``-decorated class becomes one :class:`MethodTool`.

The tool's identity is derived from the *bound* method, so the same method
on two distinct instances produces two distinct tools (different
``tool_id``). The owning instance is kept on the tool so hosts can perform
audit, lifecycle cleanup, and Swarm-level grouping by owner.
"""

# pyright: strict
from __future__ import annotations

from collections.abc import Callable

from maivn_shared import ToolType
from pydantic import Field
from typing_extensions import override

from ..mixins import FunctionToolIdentifiableMixin
from .base_tool import METHOD_TOOL_TYPE, BaseTool

# MARK: - MethodTool


class MethodTool(FunctionToolIdentifiableMixin, BaseTool):
    """Tool implementation for bound methods on toolset instances.

    Structurally similar to :class:`FunctionTool`. Adds an ``owner``
    reference and a ``qualified_name`` for clean audit logs (``"GitHub.create_issue"``).
    Execution dispatch is identical: the executor calls
    ``self.func(*args, **kwargs)``, which the bound method handles natively.
    """

    tool_type: ToolType = Field(
        default=METHOD_TOOL_TYPE,
        description="Type of tool (always 'method' for this class)",
    )
    func: Callable[..., object] = Field(
        ...,
        description="Bound method (owner.method) that implements the tool",
    )
    owner: object = Field(
        ...,
        description=(
            "The toolset instance the method is bound to. Kept for audit "
            "identity and lifecycle cleanup; never inspected by the runtime."
        ),
    )
    qualified_name: str = Field(
        default="",
        description=(
            "Human-readable identifier in the form ``ClassName.method_name``. "
            "Used in audit logs and error messages."
        ),
    )

    # MARK: Identity

    @override
    def _get_id_source(self) -> object:
        """Identity sourced from owner identity plus qualified method name.

        Two distinct toolset instances produce two distinct tools even when
        they expose the same method. The underlying function is shared
        across instances, so the inherited mixin behavior (hashing
        ``self.func``) would collide. We instead build the identity from
        ``id(self.owner)`` and ``qualified_name``, both stable for the
        lifetime of the tool.
        """
        return f"method:{id(self.owner)}:{self.qualified_name or self.name}"

    # MARK: Function Metadata

    def get_function_name(self) -> str:
        """Return the underlying method name (without owner prefix)."""
        return self._callable_name(self.func, "<method>")

    # MARK: String Representation

    @override
    def __str__(self) -> str:
        """Return a readable identifier including the owning class."""
        return self._format_tool_label(self.qualified_name or self.get_function_name())


__all__ = [
    "MethodTool",
]
