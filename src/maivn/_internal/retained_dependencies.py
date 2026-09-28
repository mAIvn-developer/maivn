"""Validate server-bound conversation results at the developer execution boundary."""

from __future__ import annotations

from typing import TYPE_CHECKING, cast

from maivn_contracts.runtime import PLACEHOLDER_PATTERN

from maivn._internal.private_placeholders import resolve_private_placeholders

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence

    from maivn_contracts.tools import ToolCall
    from maivn_contracts.tools.model import RetainedToolResult

    from maivn._internal.compat.decorators import ToolDependency


class RetainedDependencyError(ValueError):
    """Value-free refusal for a retained dependency that cannot be used."""

    def __init__(self, *, unavailable: bool = False) -> None:
        self.sdk_error_code = (
            'sdk_retained_dependency_unavailable'
            if unavailable
            else 'sdk_retained_dependency_invalid'
        )
        super().__init__(
            'Retained dependency is unavailable'
            if unavailable
            else 'Retained dependency does not match its declared conversation scope'
        )


def validated_retained_dependencies(
    call: ToolCall,
    dependencies: Sequence[ToolDependency],
    *,
    thread_id: str | None,
    private_data: Mapping[str, object],
) -> dict[str, RetainedToolResult]:
    """Reject foreign bindings before any consumer or interrupt handler executes."""
    declared = {dependency.arg_name: dependency for dependency in dependencies}
    retained: dict[str, RetainedToolResult] = {}
    for argument, binding in (call.retained_dependencies or {}).items():
        dependency = declared.get(argument)
        if (
            dependency is None
            or dependency.result_scope != 'conversation'
            or thread_id is None
            or binding.thread_id != thread_id
            or binding.source_session_id == call.lineage.session_id
            or binding.producer.namespace != 'sdk'
            or binding.producer.version != 'v1'
            or binding.producer.tool_id not in {dependency.tool_id, dependency.tool_name}
        ):
            raise RetainedDependencyError
        value = resolve_private_placeholders(binding.result, private_data)
        if _has_placeholder(value):
            raise RetainedDependencyError(unavailable=True)
        retained[argument] = binding.model_copy(update={'result': value})
    return retained


def _has_placeholder(value: object) -> bool:
    if isinstance(value, str):
        return PLACEHOLDER_PATTERN.search(value) is not None
    if isinstance(value, dict):
        return any(_has_placeholder(item) for item in cast('dict[object, object]', value).values())
    if isinstance(value, (list, tuple)):
        return any(_has_placeholder(item) for item in cast('Sequence[object]', value))
    return False
