"""Shared scope defaults and per-call configuration resolution."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from maivn._internal.compat.options import (
    MemoryConfig,
    SessionOrchestrationConfig,
    SystemToolsConfig,
)
from maivn._internal.scope.normalization import (
    coerce_orchestration_config,
    coerce_system_tools_config,
)
from maivn._internal.scope.options import scope_options

if TYPE_CHECKING:
    from collections.abc import Callable, Mapping

    from maivn._internal.models import RoutingPreference, RunOptions
    from maivn._internal.scope.types import (
        AutoSkills,
        ReasoningEffort,
        RuntimeModelChoice,
        ScopeResourceBindingType,
    )


def merge_system_tools_config(
    defaults: object, override: object = None, *, allow_private: bool = False
) -> SystemToolsConfig:
    """Resolve both scope types' system-tool defaults without mutating either input."""
    base = coerce_system_tools_config(defaults)
    if allow_private:
        base = base.model_copy(update={'allow_private_data': True})
    if override is None:
        return base
    payload = base.model_dump(exclude_none=True)
    payload.update(coerce_system_tools_config(override).model_dump(exclude_none=True))
    return SystemToolsConfig.model_validate(payload)


def merge_orchestration_config(
    defaults: object, override: object = None
) -> SessionOrchestrationConfig:
    """Resolve the orchestration defaults shared by agent and swarm execution."""
    base = coerce_orchestration_config(defaults)
    if override is None:
        return base
    payload = base.model_dump(exclude_none=True)
    payload.update(coerce_orchestration_config(override).model_dump(exclude_none=True))
    return SessionOrchestrationConfig.model_validate(payload)


@dataclass(frozen=True, slots=True)
class ScopeDefaults:
    """Snapshot current scope settings for one invocation, stream, or thread request."""

    name: str
    binding_type: ScopeResourceBindingType
    model: str
    attached_skill_ids: list[str]
    auto_skills: AutoSkills | None
    resolve_memory: Callable[[object], MemoryConfig | None]
    resolve_system_tools: Callable[[object], SystemToolsConfig]
    resolve_orchestration: Callable[[object], SessionOrchestrationConfig]
    timeout: float | None = None
    max_results: int | None = None

    def options(  # noqa: PLR0913 - mirrors existing public run overrides without changing them.
        self,
        options: RunOptions | None,
        *,
        thread_id: str | None = None,
        model: RuntimeModelChoice | None = None,
        memory_config: MemoryConfig | Mapping[str, object] | None = None,
        reasoning: ReasoningEffort | None = None,
        routing_preference: RoutingPreference | None = None,
        system_tools_config: SystemToolsConfig | Mapping[str, object] | None = None,
        orchestration_config: SessionOrchestrationConfig | Mapping[str, object] | None = None,
        stream_response: bool | None = None,
        origin: str | None = None,
    ) -> RunOptions:
        """Apply scope defaults and call overrides using the existing precedence rules."""
        return scope_options(
            options,
            self.model,
            self.attached_skill_ids,
            self.auto_skills,
            scope_name=self.name,
            scope_binding_type=self.binding_type,
            thread_id=thread_id,
            model=model,
            memory_config=self.resolve_memory(memory_config),
            reasoning=reasoning,
            routing_preference=routing_preference,
            system_tools_config=self.resolve_system_tools(system_tools_config),
            orchestration_config=self.resolve_orchestration(orchestration_config),
            stream_response=stream_response,
            timeout=self.timeout,
            max_results=self.max_results,
            origin=origin,
        )
