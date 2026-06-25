"""State compilation utilities for agent orchestration."""

# pyright: strict
from __future__ import annotations

import threading
import warnings
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from typing import Literal, Protocol, TypeAlias, cast

from maivn_shared import (
    BaseMessage,
    InterruptDependency,
    MemoryAssetsConfig,
    MemoryConfig,
    ModelConfig,
    ModelTier,
    SessionExecutionConfig,
    SessionOrchestrationConfig,
    SessionRequest,
    StructuredOutputConfig,
    SwarmConfig,
    SystemToolsConfig,
    ToolSpec,
)
from pydantic import BaseModel, JsonValue

from maivn._internal.core.entities import BaseTool, FunctionTool
from maivn._internal.core.entities.state_compilation_config import (
    StateCompilationConfig,
)
from maivn._internal.core.services.toolify import ToolifyOptions, ToolifyService
from maivn._internal.core.tool_specs import ToolSpecFactory

from ..helpers import get_optimal_worker_count
from .dependency_updates import deduplicate_tool_specs, update_tool_dependency_references
from .dynamic_tool_factory import DynamicToolFactory
from .tool_cache import (
    CompiledToolSpecCache,
    compilation_fingerprint,
    toolspec_cache_enabled,
)
from .tool_normalization import normalize_tools_for_structured_output

# MARK: - Types

JsonObject: TypeAlias = dict[str, JsonValue]
ModelSelection: TypeAlias = ModelTier | ModelConfig
ReasoningLevel: TypeAlias = Literal["minimal", "low", "medium", "high"]


class StateCompilationScope(Protocol):
    @property
    def id(self) -> str: ...


class StateCompiler:
    """Handles compilation of agent state and messages for orchestration."""

    def __init__(
        self,
        tool_spec_factory: ToolSpecFactory,
        *,
        config: StateCompilationConfig | None = None,
        dynamic_tool_factory: DynamicToolFactory | None = None,
    ) -> None:
        self._tool_spec_factory: ToolSpecFactory = tool_spec_factory
        self._config: StateCompilationConfig = config or StateCompilationConfig()
        self._dynamic_tool_factory: DynamicToolFactory = (
            dynamic_tool_factory or DynamicToolFactory()
        )
        self._dynamic_tools: list[FunctionTool] = []
        self._tool_spec_lock: threading.Lock = threading.Lock()
        self._compiled_cache: CompiledToolSpecCache = CompiledToolSpecCache()

    # MARK: - Public API

    def compile_state(
        self,
        messages: list[BaseMessage],
        tools: list[BaseTool],
        scope: StateCompilationScope,
        timeout: int | None = None,
        force_final_tool: bool = False,
        targeted_tools: list[str] | None = None,
        structured_output: type[BaseModel] | None = None,
        model: ModelSelection | None = None,
        force_model: str | None = None,
        reasoning: ReasoningLevel | None = None,
        stream_response: bool = True,
        status_messages: bool = False,
        max_results: int | None = None,
        execution_config: SessionExecutionConfig | None = None,
        memory_config: MemoryConfig | None = None,
        system_tools_config: SystemToolsConfig | None = None,
        orchestration_config: SessionOrchestrationConfig | None = None,
        memory_assets_config: MemoryAssetsConfig | None = None,
        swarm_config: SwarmConfig | None = None,
        metadata: JsonObject | None = None,
        config: StateCompilationConfig | None = None,
    ) -> SessionRequest:
        """Compile messages and tools into a state dictionary for orchestration.

        Args:
            messages: List of messages to compile.
            tools: List of tools to compile.
            scope: Agent scope.
            timeout: Optional timeout in seconds.
            force_final_tool: Whether to force final tool.
            targeted_tools: Optional list of tool_id strings to target.
            structured_output: Optional Pydantic model for structured output.
            model: Optional model tier ("auto", "fast", "balanced", "max").
            force_model: Optional exact model identifier to force (e.g. "claude-opus-5").
            reasoning: Optional reasoning level.
            stream_response: Whether to stream intermediate responses.
            status_messages: Whether to emit status messages at swarm lifecycle milestones.
            max_results: Optional max results.
            execution_config: Optional execution configuration supplied by the orchestrator.
            memory_config: Optional memory configuration.
            system_tools_config: Optional system-tool configuration.
            orchestration_config: Optional orchestration loop controls.
            memory_assets_config: Optional memory asset payloads.
            swarm_config: Optional swarm orchestration configuration.
            metadata: Optional metadata to merge into the session request.
            config: Optional compilation configuration.

        Returns:
            Compiled SessionRequest.
        """
        active_config = config or self._config
        if force_model is not None:
            if isinstance(model, ModelConfig):
                raise ValueError("force_model cannot be combined with ModelConfig")
            warnings.warn(
                "force_model is deprecated and will be removed soon. "
                "Use model=ModelConfig.for_all(model_id=...) or scoped ModelConfig fields.",
                DeprecationWarning,
                stacklevel=2,
            )

        try:
            self._tool_spec_factory.reset_cache()
        except Exception:  # noqa: BLE001 - cache reset is best-effort
            pass

        all_tools = self._build_tool_list(tools, scope)

        if structured_output is not None:
            toolify_service = ToolifyService()
            structured_output_name = getattr(structured_output, "__name__", "StructuredOutput")
            structured_output_description = (
                getattr(structured_output, "__doc__", "") or ""
            ).strip() or f"Structured output schema for {structured_output_name}."
            structured_tool = toolify_service.create_tool(
                structured_output,
                ToolifyOptions(
                    name=structured_output_name,
                    description=structured_output_description,
                    final_tool=True,
                ),
            )
            all_tools = normalize_tools_for_structured_output(all_tools, structured_tool)

        tool_specs = self._compile_tool_specs(all_tools, scope.id)

        metadata = self._build_metadata(active_config, metadata)
        execution_config = self._build_execution_config(
            scope,
            timeout,
            execution_config,
            active_config,
        )
        system_tools_config = self._build_system_tools_config(scope, system_tools_config)
        memory_assets_config = self._build_memory_assets_config(scope, memory_assets_config)
        system_tools_config = self._auto_approve_compose_artifact_targets(
            tool_specs,
            system_tools_config,
        )

        structured_output_config: StructuredOutputConfig | None = None
        if structured_output is not None:
            structured_output_config = StructuredOutputConfig(
                enabled=True,
                model=getattr(structured_output, "__name__", None),
            )
            force_final_tool = True
            targeted_tools = None
        interrupt_data_keys = self._extract_interrupt_data_keys(all_tools)
        private_data = _optional_attr(scope, "private_data")

        return SessionRequest(
            messages=messages,
            tools=tool_specs,
            metadata=metadata,
            memory_config=self._build_memory_config(scope, memory_config),
            execution_config=execution_config,
            system_tools_config=system_tools_config,
            structured_output_config=structured_output_config,
            orchestration_config=orchestration_config,
            memory_assets_config=memory_assets_config,
            swarm_config=swarm_config,
            force_final_tool=force_final_tool,
            targeted_tools=targeted_tools,
            model=model,
            force_model=force_model,
            reasoning=reasoning,
            stream_response=stream_response,
            status_messages=status_messages,
            max_results=max_results,
            private_data=cast(JsonObject, private_data) if private_data else None,
            interrupt_data_keys=interrupt_data_keys if interrupt_data_keys else None,
        )

    # MARK: - Tool List Building

    def _build_tool_list(
        self,
        tools: list[BaseTool],
        scope: StateCompilationScope,
    ) -> list[BaseTool]:
        """Build complete tool list including dynamic tools.

        Args:
            tools: Base tools from agent
            scope: Agent scope

        Returns:
            Complete list of tools including dynamic ones
        """
        all_tools = list(tools) if tools else []

        agent_tools, user_tools = self._dynamic_tool_factory.create_dependency_tools(
            all_tools, scope
        )
        dynamic_tools = agent_tools + user_tools

        if dynamic_tools:
            self._register_dynamic_tools(dynamic_tools)
            self._merge_dynamic_tools(all_tools, dynamic_tools)

        return all_tools

    def _register_dynamic_tools(self, dynamic_tools: list[FunctionTool]) -> None:
        """Register dynamic tools for later indexing.

        Args:
            dynamic_tools: New dynamic tools to register
        """
        existing_ids = {tool.tool_id for tool in self._dynamic_tools}
        for tool in dynamic_tools:
            if tool.tool_id not in existing_ids:
                self._dynamic_tools.append(tool)
                existing_ids.add(tool.tool_id)

    def _merge_dynamic_tools(
        self, all_tools: list[BaseTool], dynamic_tools: list[FunctionTool]
    ) -> None:
        """Merge dynamic tools into the main tool list.

        Args:
            all_tools: Main tool list to merge into (modified in place)
            dynamic_tools: Dynamic tools to merge
        """
        existing_ids = {tool.tool_id for tool in all_tools}
        for tool in dynamic_tools:
            if tool.tool_id not in existing_ids:
                all_tools.append(tool)
                existing_ids.add(tool.tool_id)

    # MARK: - Tool Spec Creation

    def _compile_tool_specs(self, all_tools: list[BaseTool], agent_id: str) -> list[ToolSpec]:
        """Compile tool specs, reusing a content-addressed cache when possible.

        The cache key is a fingerprint of the tools' resolved schemas + agent id
        (see ``tool_cache``). On a hit we skip the flatten / dedup / dependency-
        rewrite entirely; an uncacheable tool set (fingerprint ``None``) or a
        disabled cache always recompiles, preserving the original behavior.
        """
        cache_key = (
            compilation_fingerprint(all_tools, agent_id) if toolspec_cache_enabled() else None
        )
        if cache_key is not None:
            with self._tool_spec_lock:
                cached = self._compiled_cache.get(cache_key)
            if cached is not None:
                return cached

        tool_specs = self._create_tool_specs_parallel(
            all_tools, agent_id, max_workers=min(len(all_tools), get_optimal_worker_count())
        )
        update_tool_dependency_references(tool_specs, all_tools)

        if cache_key is not None:
            with self._tool_spec_lock:
                self._compiled_cache.put(cache_key, tool_specs)
        return tool_specs

    def _create_tool_specs_parallel(
        self, tools: list[BaseTool], agent_id: str, max_workers: int | None = None
    ) -> list[ToolSpec]:
        """Create ToolSpecs in parallel for performance.

        Args:
            tools: List of tools to create specs for
            agent_id: Agent identifier
            max_workers: Maximum parallel workers

        Returns:
            List of ToolSpec instances
        """
        if len(tools) <= 2:
            return self._create_tool_specs_sequential(tools, agent_id)

        return self._create_tool_specs_threaded(tools, agent_id, max_workers)

    def _create_tool_specs_sequential(self, tools: list[BaseTool], agent_id: str) -> list[ToolSpec]:
        """Create ToolSpecs sequentially for small tool counts.

        Args:
            tools: List of tools to create specs for
            agent_id: Agent identifier

        Returns:
            Deduplicated list of ToolSpec instances
        """
        all_specs: list[ToolSpec] = []
        for tool in tools:
            all_specs.extend(self._create_single_tool_spec(tool, agent_id))
        return deduplicate_tool_specs(all_specs)

    def _create_tool_specs_threaded(
        self, tools: list[BaseTool], agent_id: str, max_workers: int | None
    ) -> list[ToolSpec]:
        """Create ToolSpecs in parallel using thread pool.

        Args:
            tools: List of tools to create specs for
            agent_id: Agent identifier
            max_workers: Maximum parallel workers

        Returns:
            Deduplicated list of ToolSpec instances
        """
        max_workers = max_workers or get_optimal_worker_count()

        with ThreadPoolExecutor(max_workers=max_workers) as executor:
            futures = [
                executor.submit(self._create_single_tool_spec, tool, agent_id) for tool in tools
            ]
            all_specs: list[ToolSpec] = []
            for future in futures:
                all_specs.extend(future.result())

        return deduplicate_tool_specs(all_specs)

    def _create_single_tool_spec(self, tool: BaseTool, agent_id: str) -> list[ToolSpec]:
        """Create ToolSpecs for a single tool.

        Args:
            tool: Tool to create specs for
            agent_id: Agent identifier

        Returns:
            List of ToolSpec instances for the tool
        """
        # ToolSpecFactory owns mutable caches and a function registry. Keep each
        # create_all call atomic while preserving the existing threaded collection path.
        with self._tool_spec_lock:
            return self._tool_spec_factory.create_all(
                agent_id=agent_id,
                tool=tool,
                dependencies=tool.dependencies,
                always_execute=tool.always_execute,
                final_tool=tool.final_tool,
            )

    # MARK: - Metadata Building

    @staticmethod
    def _build_metadata(
        config: StateCompilationConfig,
        override_metadata: JsonObject | None = None,
    ) -> JsonObject:
        """Build metadata dictionary for the session request.

        Args:
            config: Compilation configuration.
            override_metadata: Optional metadata to merge on top of defaults.

        Returns:
            Metadata dictionary.
        """
        metadata = cast(JsonObject, dict(config.base_metadata))
        if override_metadata:
            metadata.update(override_metadata)

        return metadata

    @staticmethod
    def _auto_approve_compose_artifact_targets(
        tool_specs: list[ToolSpec],
        system_tools_config: SystemToolsConfig | None,
    ) -> SystemToolsConfig | None:
        """Auto-derive approved compose_artifact targets from tool specs.

        Scans all tool specs for compose_artifact arg policies with
        approval='explicit'. Any such targets are automatically added to
        system_tools_config so that the server-side policy check passes
        without requiring explicit invocation-level approval.

        This ensures consistent behavior regardless of the invocation
        client (SDK terminal, studio, or other frontends).
        """
        derived_targets: list[str] = []

        for spec in tool_specs:
            tool_name = spec.name
            if not tool_name:
                continue

            spec_metadata = spec.metadata
            if not isinstance(spec_metadata, dict):
                continue

            arg_policies = spec_metadata.get("arg_policies")
            if not isinstance(arg_policies, dict):
                continue

            for arg_name, policy_map in cast(dict[str, JsonValue], arg_policies).items():
                if not isinstance(policy_map, dict):
                    continue
                compose_policy = policy_map.get("compose_artifact")
                if not isinstance(compose_policy, dict):
                    continue
                if compose_policy.get("approval") == "explicit":
                    derived_targets.append(f"{tool_name}.{arg_name}")

        if not derived_targets:
            return system_tools_config

        existing = (
            system_tools_config.approved_compose_artifact_targets
            if system_tools_config is not None
            else None
        )
        if existing is True:
            return system_tools_config
        if isinstance(existing, list):
            combined = list(existing)
            for target in derived_targets:
                if target not in combined:
                    combined.append(target)
            approval_config = SystemToolsConfig(approved_compose_artifact_targets=combined)
        else:
            approval_config = SystemToolsConfig(approved_compose_artifact_targets=derived_targets)
        return SystemToolsConfig.merge(system_tools_config, approval_config)

    def _build_execution_config(
        self,
        scope: StateCompilationScope,
        timeout: int | None,
        override: SessionExecutionConfig | None,
        config: StateCompilationConfig,
    ) -> SessionExecutionConfig | None:
        base = SessionExecutionConfig(
            agent_id=scope.id,
            timeout=timeout if timeout is not None and config.include_timeout else None,
        )
        merged_config = SessionExecutionConfig.merge(base, override)
        return (
            merged_config if merged_config is not None and merged_config.is_configured() else None
        )

    @staticmethod
    def _build_system_tools_config(
        scope: StateCompilationScope,
        override: SystemToolsConfig | None,
    ) -> SystemToolsConfig | None:
        if override is not None and override.is_configured():
            return override
        resolve_config = _optional_attr(scope, "resolve_system_tools_config")
        if not callable(resolve_config):
            return override

        resolved = cast(Callable[[SystemToolsConfig | None], object], resolve_config)(None)
        if isinstance(resolved, SystemToolsConfig) and resolved.is_configured():
            return resolved
        return override

    @staticmethod
    def _build_memory_assets_config(
        scope: StateCompilationScope,
        override: MemoryAssetsConfig | None,
    ) -> MemoryAssetsConfig | None:
        if override is not None and override.is_configured():
            return override

        build_assets = _optional_attr(scope, "build_memory_asset_payloads")
        if not callable(build_assets):
            return override

        default_agent_id = scope.id
        default_swarm_id = None
        get_swarm = _optional_attr(scope, "get_swarm")
        if callable(get_swarm):
            swarm = cast(Callable[[], object | None], get_swarm)()
            if swarm is not None:
                default_swarm_id = _optional_str_attr(swarm, "id")

        raw_assets = build_assets(
            default_agent_id=default_agent_id,
            default_swarm_id=default_swarm_id,
        )
        if not isinstance(raw_assets, tuple):
            return override
        raw_assets_tuple = cast(tuple[object, ...], raw_assets)
        if len(raw_assets_tuple) != 2:
            return override

        skills, resources = raw_assets_tuple
        config = MemoryAssetsConfig.model_validate(
            {
                "defined_skills": skills if isinstance(skills, list) else [],
                "bound_resources": resources if isinstance(resources, list) else [],
            }
        )
        return config if config.is_configured() else override

    def _build_memory_config(
        self, scope: StateCompilationScope, override: MemoryConfig | None
    ) -> MemoryConfig | None:
        resolver = _optional_attr(scope, "resolve_memory_config")
        if callable(resolver):
            resolved = resolver(override)
            if isinstance(resolved, MemoryConfig) and resolved.is_configured():
                return resolved
            return None
        if isinstance(override, MemoryConfig) and override.is_configured():
            return override
        return None

    def _extract_interrupt_data_keys(self, tools: list[BaseTool]) -> list[str]:
        """Extract interrupt data keys from tools with user dependencies.

        Args:
            tools: List of tools to extract keys from

        Returns:
            List of interrupt data key names
        """
        keys: list[str] = []
        for tool in tools:
            dependencies = tool.dependencies
            if not dependencies:
                continue

            for dep in dependencies:
                if isinstance(dep, InterruptDependency) and dep.arg_name:
                    keys.append(dep.arg_name)

        return keys


# MARK: - Attribute Helpers


def _optional_attr(value: object, attr: str) -> object | None:
    return cast(object | None, getattr(value, attr, None))


def _optional_str_attr(value: object, attr: str) -> str | None:
    attr_value = _optional_attr(value, attr)
    return attr_value if isinstance(attr_value, str) and attr_value else None


__all__ = ["StateCompiler"]
