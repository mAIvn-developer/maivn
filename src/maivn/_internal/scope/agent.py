# ruff: noqa: PLR0913 - the wide signatures here are public SDK entry points; collapsing them into a config object would break callers.
"""Developer-authored single-agent scope."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Literal, cast

from maivn._internal.api.async_stream import run_blocking
from maivn._internal.claiming import RunReport
from maivn._internal.client import Client
from maivn._internal.compat.interrupts import (
    FollowupInputHandler,
    FollowupQuestionsConfig,
    default_terminal_followup,
)
from maivn._internal.compat.invocation import (
    EventInvocationBuilder,
    StructuredOutputInvocationBuilder,
    coerce_structured_response,
    response_from_stream_events,
    run_abatch,
    run_batch,
)
from maivn._internal.compat.options import (
    MemoryConfig,
    SessionOrchestrationConfig,
    SystemToolsConfig,
)
from maivn._internal.compat.tooling import (
    ScopeToolifyBuilder,
    ToolRegistrationOptions,
    ToolTarget,
    assign_tool_id,
    tool_metadata_for_callable,
    toolify_builder_for_scope,
)
from maivn._internal.errors import MissingCredentialError
from maivn._internal.reporting.terminal_reporter.factory import create_reporter
from maivn._internal.scope.execution_hooks import (
    ainvoke_with_scope_hooks,
    async_invoke_with_scope_hooks,
    invoke_with_scope_hooks,
    scope_execution_hooks,
    stream_with_scope_hooks,
)
from maivn._internal.scope.followup import FollowupInvocationBuilder
from maivn._internal.scope.normalization import (
    coerce_memory_config,
    coerce_optional_memory_config,
    coerce_orchestration_config,
    coerce_system_tools_config,
    normalize_mcp_servers,
    normalize_private_data,
    normalize_resource_specs,
    normalize_skill_specs,
    normalize_string_list,
    private_data_mapping,
    register_initial_tools,
)
from maivn._internal.scope.options import (
    messages_with_system_prompt,
    plan_structured_output,
    response_with_thread_id,
)
from maivn._internal.scope.preparation import (
    ScopeDefaults,
    merge_orchestration_config,
    merge_system_tools_config,
)
from maivn._internal.scope.resource_binding import aregister_scope_resources_once
from maivn._internal.scope.skill_authoring import (
    agent_scope,
    append_unique,
    build_auto_skills,
    skill_request,
    skill_set_from_input,
    skill_set_with_scope,
)
from maivn._internal.scope.stream_reporting import report_event, reported_stream
from maivn._internal.scope.tools import (
    compile_scope_tools,
    has_final_tool,
    resolve_force_final_tool,
    select_targeted_tools,
    validate_tool_metadata,
)
from maivn._internal.scope.types import (
    DEFAULT_MODEL,
    AutoSkills,
    HookExecutionMode,
    ReasoningEffort,
    ResourceSpec,
    RuntimeModelChoice,
    SkillSpec,
    ToolSpec,
)
from maivn._internal.serving import (
    HEARTBEAT_INTERVAL_SECONDS,
    ServingSession,
    build_serving_manifest,
    resolve_project_id,
)
from maivn._internal.thread import BoundThread

if TYPE_CHECKING:
    from collections.abc import (
        AsyncGenerator,
        Callable,
        Generator,
        Iterable,
        Mapping,
        Sequence,
    )
    from pathlib import Path

    from maivn_contracts.agents import ServingTransport
    from maivn_contracts.messages import Message
    from pydantic import BaseModel

    from maivn._internal.claiming import ClaimedWork, ClaimListener, WorkRunner
    from maivn._internal.compat.mcp import MCPServer
    from maivn._internal.compat.privacy import PrivateData
    from maivn._internal.models import (
        ApprovalDecision,
        InvokeResponse,
        JsonObject,
        ProviderUsage,
        RoutingPreference,
        RunOptions,
        StreamEvent,
        ThreadAccepted,
        ThreadState,
        ToolMetadata,
    )
    from maivn._internal.serving import ServingListener
    from maivn._internal.skills import (
        Skill,
        SkillSet,
        SkillStep,
    )
    from maivn.messages import SdkMessagesInput, SystemMessage


@dataclass(slots=True)
class Agent:
    """Developer-authored agent descriptor backed by the v2 API client."""

    name: str
    agent_id: str | None = field(default=None, kw_only=True)
    version: int = field(default=1, kw_only=True)
    description: str | None = None
    system_prompt: str | SystemMessage | None = None
    api_key: str | None = field(default=None, repr=False)
    env_file: str | Path | None = field(default=None, kw_only=True)
    api_key_file: str | Path | None = field(default=None, kw_only=True)
    base_url: str | None = None
    client: Client | None = None
    timeout: float | None = None
    max_results: int | None = None
    model: str = DEFAULT_MODEL
    memory_config: MemoryConfig | Mapping[str, object] | None = None
    private_data: Mapping[object, object] | Sequence[PrivateData | Mapping[str, object]] | None = (
        None
    )
    system_tools_config: SystemToolsConfig | Mapping[str, object] | None = None
    orchestration_config: SessionOrchestrationConfig | Mapping[str, object] | None = None
    allow_private_in_system_tools: bool = False
    use_as_final_output: bool = False
    included_nested_synthesis: bool | Literal['auto'] = 'auto'
    force_final_tool: bool = False
    before_execute: Callable[..., object] | None = None
    after_execute: Callable[..., object] | None = None
    hook_execution_mode: HookExecutionMode = 'tool'
    tools: list[ToolSpec] = field(default_factory=list)
    skills: Sequence[SkillSpec] = field(default_factory=list[SkillSpec])
    resources: list[ResourceSpec] = field(default_factory=list)
    tags: list[str] = field(default_factory=list)
    skill_sets: list[SkillSet] = field(default_factory=list)
    attached_skill_ids: list[str] = field(default_factory=list)
    mcp_servers: list[MCPServer] = field(default_factory=list)
    _auto_skills: AutoSkills | None = None
    _compiled_tools_cache: list[ToolMetadata] | None = None
    _compiled_hook_configuration: tuple[tuple[int, str, int, int], ...] | None = field(
        default=None, init=False, repr=False
    )
    _tools_dirty: bool = True
    _registered_resource_ids: list[str] | None = None
    # Set by `Swarm._adopt_hook_scope` when this agent joins a swarm, and walked
    # back up by `scope/tools.py` to chain the outer scope's execution hooks.
    # Typed `object` to match that duck-typed walk and to keep the swarm module,
    # which already imports this one, out of this module's imports.
    _parent_hook_scope: object | None = field(default=None, init=False, repr=False, compare=False)

    def __post_init__(self) -> None:
        """Create a client when the caller did not inject one."""
        self.memory_config = coerce_memory_config(self.memory_config)
        self.private_data = normalize_private_data(self.private_data)
        self.system_tools_config = coerce_system_tools_config(self.system_tools_config)
        self.orchestration_config = coerce_orchestration_config(self.orchestration_config)
        self.tools = register_initial_tools(self.tools)
        self.skills = normalize_skill_specs(self.skills)
        self.resources = normalize_resource_specs(self.resources)
        self.tags = normalize_string_list(self.tags, 'tags')
        if self.client is None:
            try:
                self.client = Client(
                    api_key=self.api_key,
                    env_file=self.env_file,
                    api_key_file=self.api_key_file,
                    base_url=self.base_url,
                )
            except MissingCredentialError as exc:
                # Preserve the scope's existing missing-credential exception,
                # while letting Client resolve the process environment first.
                message = (
                    'Agent requires either a Client instance or an api_key, '
                    'or MAIVN_API_KEY in the process environment.'
                )
                raise ValueError(message) from exc

    def invoke(
        self,
        messages: SdkMessagesInput,
        *,
        force_final_tool: bool | None = None,
        targeted_tools: list[str] | None = None,
        structured_output: type[BaseModel] | None = None,
        thread_id: str | None = None,
        verbose: bool = False,
        reasoning: ReasoningEffort | None = None,
        routing_preference: RoutingPreference | None = None,
        metadata: Mapping[str, object] | None = None,
        memory_config: MemoryConfig | Mapping[str, object] | None = None,
        allow_private_in_system_tools: bool | None = None,
        model: RuntimeModelChoice | None = None,
        system_tools_config: SystemToolsConfig | Mapping[str, object] | None = None,
        orchestration_config: SessionOrchestrationConfig | Mapping[str, object] | None = None,
        stream_response: bool = False,
        options: RunOptions | None = None,
        origin: str | None = None,
    ) -> InvokeResponse:
        """Invoke this single-agent scope through the API.

        ``metadata``, per-call ``memory_config``, and per-call
        ``allow_private_in_system_tools`` are accepted for v1 source
        compatibility. ``reasoning`` is forwarded as an invoke request field.
        ``origin`` is a self-reported invoking client (e.g. ``"studio"``),
        display/filter only.
        """
        _ = (metadata, allow_private_in_system_tools)
        resolved_force_final_tool = resolve_force_final_tool(
            default=self.force_final_tool,
            call_value=force_final_tool,
        )
        _validate_agent_force_final_tool(
            self,
            force_final_tool=resolved_force_final_tool,
            structured_output=structured_output,
        )
        self._ensure_resources_registered()
        resolved_options = self._execution_defaults().options(
            options,
            thread_id=thread_id,
            reasoning=reasoning,
            routing_preference=routing_preference,
            model=model,
            memory_config=memory_config,
            system_tools_config=system_tools_config,
            orchestration_config=orchestration_config,
            stream_response=stream_response,
            origin=origin,
        )
        plan = plan_structured_output(
            resolved_options,
            structured_output,
            tools=select_targeted_tools(self.compile_tools(), targeted_tools),
            force_final_tool=resolved_force_final_tool,
        )
        resolved_options = plan.options
        resolved_messages = self._with_system_prompt(messages)
        hooks = scope_execution_hooks(self)
        if stream_response or verbose:
            events = stream_with_scope_hooks(
                self._client.stream(
                    resolved_messages,
                    options=resolved_options,
                    tools=plan.tools,
                    private_data=private_data_mapping(self.private_data),
                    force_final_tool=plan.force_final_tool,
                ),
                hooks,
                scope=self,
                messages=resolved_messages,
            )
            if verbose:
                events = reported_stream(events, create_reporter())
            response = response_with_thread_id(
                response_from_stream_events(events),
                resolved_options.thread_id,
            )
            return coerce_structured_response(response, structured_output)
        response = invoke_with_scope_hooks(
            lambda: self._client.invoke(
                resolved_messages,
                options=resolved_options,
                tools=plan.tools,
                private_data=private_data_mapping(self.private_data),
                force_final_tool=plan.force_final_tool,
            ),
            hooks,
            scope=self,
            messages=resolved_messages,
        )
        return coerce_structured_response(response, structured_output)

    def allow_followup_questions(
        self,
        *,
        input_handler: FollowupInputHandler = default_terminal_followup,
        max_questions: int = 3,
        max_questions_per_thread: int = 10,
        response_schema: JsonObject | type[BaseModel] | None = None,
        timeout: int | None = None,
    ) -> FollowupInvocationBuilder:
        """Return an inline per-call builder that enables runtime follow-up questions."""
        return FollowupInvocationBuilder(
            self,
            FollowupQuestionsConfig(
                input_handler=input_handler,
                max_questions=max_questions,
                max_questions_per_thread=max_questions_per_thread,
                response_schema=response_schema,
                timeout=timeout,
            ),
        )

    async def ainvoke(
        self,
        messages: SdkMessagesInput,
        *,
        force_final_tool: bool | None = None,
        targeted_tools: list[str] | None = None,
        structured_output: type[BaseModel] | None = None,
        thread_id: str | None = None,
        verbose: bool = False,
        reasoning: ReasoningEffort | None = None,
        routing_preference: RoutingPreference | None = None,
        metadata: Mapping[str, object] | None = None,
        memory_config: MemoryConfig | Mapping[str, object] | None = None,
        allow_private_in_system_tools: bool | None = None,
        model: RuntimeModelChoice | None = None,
        system_tools_config: SystemToolsConfig | Mapping[str, object] | None = None,
        orchestration_config: SessionOrchestrationConfig | Mapping[str, object] | None = None,
        stream_response: bool = False,
        options: RunOptions | None = None,
        origin: str | None = None,
    ) -> InvokeResponse:
        """Invoke this single-agent scope asynchronously through the API."""
        _ = (metadata, allow_private_in_system_tools)
        resolved_force_final_tool = resolve_force_final_tool(
            default=self.force_final_tool,
            call_value=force_final_tool,
        )
        _validate_agent_force_final_tool(
            self,
            force_final_tool=resolved_force_final_tool,
            structured_output=structured_output,
        )
        await self._aensure_resources_registered()
        resolved_options = self._execution_defaults().options(
            options,
            thread_id=thread_id,
            reasoning=reasoning,
            routing_preference=routing_preference,
            model=model,
            memory_config=memory_config,
            system_tools_config=system_tools_config,
            orchestration_config=orchestration_config,
            stream_response=stream_response,
            origin=origin,
        )
        plan = plan_structured_output(
            resolved_options,
            structured_output,
            tools=select_targeted_tools(self.compile_tools(), targeted_tools),
            force_final_tool=resolved_force_final_tool,
        )
        resolved_options = plan.options
        resolved_messages = self._with_system_prompt(messages)
        hooks = scope_execution_hooks(self)
        if stream_response or verbose:
            reporter = create_reporter() if verbose else None
            events = [
                report_event(event, reporter)
                async for event in ainvoke_with_scope_hooks(
                    lambda: self._client.astream(
                        resolved_messages,
                        options=resolved_options,
                        tools=plan.tools,
                        private_data=private_data_mapping(self.private_data),
                        force_final_tool=plan.force_final_tool,
                    ),
                    hooks,
                    scope=self,
                    messages=resolved_messages,
                )
            ]
            response = response_with_thread_id(
                response_from_stream_events(events),
                resolved_options.thread_id,
            )
            return coerce_structured_response(response, structured_output)
        response = await async_invoke_with_scope_hooks(
            lambda: self._client.ainvoke(
                resolved_messages,
                options=resolved_options,
                tools=plan.tools,
                private_data=private_data_mapping(self.private_data),
                force_final_tool=plan.force_final_tool,
            ),
            hooks,
            scope=self,
            messages=resolved_messages,
        )
        return coerce_structured_response(response, structured_output)

    def stream(
        self,
        messages: SdkMessagesInput,
        *,
        force_final_tool: bool | None = None,
        targeted_tools: list[str] | None = None,
        structured_output: type[BaseModel] | None = None,
        thread_id: str | None = None,
        verbose: bool = False,
        reasoning: ReasoningEffort | None = None,
        routing_preference: RoutingPreference | None = None,
        metadata: Mapping[str, object] | None = None,
        memory_config: MemoryConfig | Mapping[str, object] | None = None,
        allow_private_in_system_tools: bool | None = None,
        model: RuntimeModelChoice | None = None,
        system_tools_config: SystemToolsConfig | Mapping[str, object] | None = None,
        orchestration_config: SessionOrchestrationConfig | Mapping[str, object] | None = None,
        stream_response: bool = False,
        options: RunOptions | None = None,
        origin: str | None = None,
    ) -> Generator[StreamEvent, None, None]:
        """Stream this single-agent scope through the API."""
        _ = (metadata, allow_private_in_system_tools)
        resolved_force_final_tool = resolve_force_final_tool(
            default=self.force_final_tool,
            call_value=force_final_tool,
        )
        _validate_agent_force_final_tool(
            self,
            force_final_tool=resolved_force_final_tool,
            structured_output=structured_output,
        )
        self._ensure_resources_registered()
        resolved_options = self._execution_defaults().options(
            options,
            thread_id=thread_id,
            reasoning=reasoning,
            routing_preference=routing_preference,
            model=model,
            memory_config=memory_config,
            system_tools_config=system_tools_config,
            orchestration_config=orchestration_config,
            stream_response=stream_response,
            origin=origin,
        )
        plan = plan_structured_output(
            resolved_options,
            structured_output,
            tools=select_targeted_tools(self.compile_tools(), targeted_tools),
            force_final_tool=resolved_force_final_tool,
        )
        resolved_messages = self._with_system_prompt(messages)
        events = stream_with_scope_hooks(
            self._client.stream(
                resolved_messages,
                tools=plan.tools,
                private_data=private_data_mapping(self.private_data),
                force_final_tool=plan.force_final_tool,
                options=plan.options,
            ),
            scope_execution_hooks(self),
            scope=self,
            messages=resolved_messages,
        )
        return reported_stream(events, create_reporter()) if verbose else events

    async def astream(
        self,
        messages: SdkMessagesInput,
        *,
        force_final_tool: bool | None = None,
        targeted_tools: list[str] | None = None,
        structured_output: type[BaseModel] | None = None,
        thread_id: str | None = None,
        verbose: bool = False,
        reasoning: ReasoningEffort | None = None,
        routing_preference: RoutingPreference | None = None,
        metadata: Mapping[str, object] | None = None,
        memory_config: MemoryConfig | Mapping[str, object] | None = None,
        allow_private_in_system_tools: bool | None = None,
        model: RuntimeModelChoice | None = None,
        system_tools_config: SystemToolsConfig | Mapping[str, object] | None = None,
        orchestration_config: SessionOrchestrationConfig | Mapping[str, object] | None = None,
        stream_response: bool = False,
        options: RunOptions | None = None,
        origin: str | None = None,
        cancel_on_close: bool = False,
    ) -> AsyncGenerator[StreamEvent, None]:
        """Stream this scope, optionally cancelling its invocation on nonterminal close."""
        _ = (metadata, allow_private_in_system_tools)
        resolved_force_final_tool = resolve_force_final_tool(
            default=self.force_final_tool,
            call_value=force_final_tool,
        )
        _validate_agent_force_final_tool(
            self,
            force_final_tool=resolved_force_final_tool,
            structured_output=structured_output,
        )
        await self._aensure_resources_registered()
        reporter = create_reporter() if verbose else None
        resolved_options = self._execution_defaults().options(
            options,
            thread_id=thread_id,
            reasoning=reasoning,
            routing_preference=routing_preference,
            model=model,
            memory_config=memory_config,
            system_tools_config=system_tools_config,
            orchestration_config=orchestration_config,
            stream_response=stream_response,
            origin=origin,
        )
        plan = plan_structured_output(
            resolved_options,
            structured_output,
            tools=select_targeted_tools(self.compile_tools(), targeted_tools),
            force_final_tool=resolved_force_final_tool,
        )
        resolved_messages = self._with_system_prompt(messages)
        events = ainvoke_with_scope_hooks(
            lambda: self._client.astream(
                resolved_messages,
                tools=plan.tools,
                private_data=private_data_mapping(self.private_data),
                force_final_tool=plan.force_final_tool,
                options=plan.options,
                cancel_on_close=cancel_on_close,
            ),
            scope_execution_hooks(self),
            scope=self,
            messages=resolved_messages,
        )
        try:
            async for event in events:
                yield report_event(event, reporter)
        finally:
            await events.aclose()

    def structured_output(self, model: type[BaseModel]) -> StructuredOutputInvocationBuilder:
        """Return a bound invocable that validates the final response as ``model``."""
        return StructuredOutputInvocationBuilder(self, model)

    def events(
        self,
        *,
        include: Iterable[str] | str | None = None,
        exclude: Iterable[str] | str | None = None,
        on_event: Callable[[dict[str, object]], None] | None = None,
        auto_verbose: bool = True,
    ) -> EventInvocationBuilder:
        """Return a bound event-stream invocation wrapper."""
        return EventInvocationBuilder(
            self,
            include=include,
            exclude=exclude,
            on_event=on_event,
            auto_verbose=auto_verbose,
        )

    def batch(
        self,
        inputs: Iterable[SdkMessagesInput],
        *,
        max_concurrency: int | None = None,
        **invoke_kwargs: object,
    ) -> list[InvokeResponse]:
        """Invoke this agent for multiple inputs concurrently."""
        return run_batch(self, inputs, max_concurrency=max_concurrency, invoke_kwargs=invoke_kwargs)

    async def abatch(
        self,
        inputs: Iterable[SdkMessagesInput],
        *,
        max_concurrency: int | None = None,
        **invoke_kwargs: object,
    ) -> list[InvokeResponse]:
        """Asynchronously invoke this agent for multiple inputs concurrently."""
        return await run_abatch(
            self,
            inputs,
            max_concurrency=max_concurrency,
            invoke_kwargs=invoke_kwargs,
        )

    def start_thread(
        self,
        messages: SdkMessagesInput,
        *,
        options: RunOptions | None = None,
    ) -> ThreadAccepted:
        """Start a thread session for this agent."""
        return run_blocking(lambda: self.astart_thread(messages, options=options))

    async def astart_thread(
        self,
        messages: SdkMessagesInput,
        *,
        options: RunOptions | None = None,
    ) -> ThreadAccepted:
        """Start a thread asynchronously with this agent's resources and defaults."""
        _validate_agent_force_final_tool(
            self, force_final_tool=self.force_final_tool, structured_output=None
        )
        await self._aensure_resources_registered()
        return await self._client.astart_thread(
            self._with_system_prompt(messages),
            options=self._execution_defaults().options(options),
            tools=self.compile_tools(),
            private_data=private_data_mapping(self.private_data),
            force_final_tool=self.force_final_tool,
        )

    def post_thread_message(
        self,
        thread_id: str,
        message: SdkMessagesInput,
        *,
        options: RunOptions | None = None,
    ) -> ThreadAccepted:
        """Continue a thread with a user message."""
        return run_blocking(lambda: self.apost_thread_message(thread_id, message, options=options))

    async def apost_thread_message(
        self,
        thread_id: str,
        message: SdkMessagesInput,
        *,
        options: RunOptions | None = None,
    ) -> ThreadAccepted:
        """Continue a thread asynchronously with this agent's defaults."""
        _validate_agent_force_final_tool(
            self, force_final_tool=self.force_final_tool, structured_output=None
        )
        await self._aensure_resources_registered()
        return await self._client.apost_thread_message(
            thread_id,
            message,
            options=self._execution_defaults().options(options),
            tools=self.compile_tools(),
            private_data=private_data_mapping(self.private_data),
            force_final_tool=self.force_final_tool,
        )

    def time_travel_thread(
        self,
        thread_id: str,
        *,
        checkpoint_id: str,
        message: SdkMessagesInput,
        branch_from_message_id: str | None = None,
        options: RunOptions | None = None,
    ) -> ThreadAccepted:
        """Continue this agent from a prior checkpoint without changing thread id."""
        return run_blocking(
            lambda: self.atime_travel_thread(
                thread_id,
                checkpoint_id=checkpoint_id,
                message=message,
                branch_from_message_id=branch_from_message_id,
                options=options,
            )
        )

    async def atime_travel_thread(
        self,
        thread_id: str,
        *,
        checkpoint_id: str,
        message: SdkMessagesInput,
        branch_from_message_id: str | None = None,
        options: RunOptions | None = None,
    ) -> ThreadAccepted:
        """Continue asynchronously from a checkpoint with this agent's defaults."""
        _validate_agent_force_final_tool(
            self, force_final_tool=self.force_final_tool, structured_output=None
        )
        await self._aensure_resources_registered()
        return await self._client.atime_travel_thread(
            thread_id,
            checkpoint_id=checkpoint_id,
            message=message,
            branch_from_message_id=branch_from_message_id,
            options=self._execution_defaults().options(options),
            tools=self.compile_tools(),
            private_data=private_data_mapping(self.private_data),
            force_final_tool=self.force_final_tool,
        )

    def time_travel_thread_stream(
        self,
        thread_id: str,
        *,
        checkpoint_id: str,
        message: SdkMessagesInput,
        branch_from_message_id: str | None = None,
        force_final_tool: bool | None = None,
        targeted_tools: list[str] | None = None,
        structured_output: type[BaseModel] | None = None,
        verbose: bool = False,
        reasoning: ReasoningEffort | None = None,
        routing_preference: RoutingPreference | None = None,
        memory_config: MemoryConfig | Mapping[str, object] | None = None,
        model: RuntimeModelChoice | None = None,
        system_tools_config: SystemToolsConfig | Mapping[str, object] | None = None,
        orchestration_config: SessionOrchestrationConfig | Mapping[str, object] | None = None,
        options: RunOptions | None = None,
        origin: str | None = None,
    ) -> Generator[StreamEvent, None, None]:
        """Rewind this agent and stream the replacement path on the same thread."""
        resolved_force_final_tool = resolve_force_final_tool(
            default=self.force_final_tool,
            call_value=force_final_tool,
        )
        _validate_agent_force_final_tool(
            self,
            force_final_tool=resolved_force_final_tool,
            structured_output=structured_output,
        )
        self._ensure_resources_registered()
        resolved_options = self._execution_defaults().options(
            options,
            thread_id=thread_id,
            reasoning=reasoning,
            routing_preference=routing_preference,
            model=model,
            memory_config=memory_config,
            system_tools_config=system_tools_config,
            orchestration_config=orchestration_config,
            stream_response=True,
            origin=origin,
        )
        plan = plan_structured_output(
            resolved_options,
            structured_output,
            tools=select_targeted_tools(self.compile_tools(), targeted_tools),
            force_final_tool=resolved_force_final_tool,
        )
        events = self._client.time_travel_thread_stream(
            thread_id,
            checkpoint_id=checkpoint_id,
            message=message,
            branch_from_message_id=branch_from_message_id,
            options=plan.options,
            tools=plan.tools,
            private_data=private_data_mapping(self.private_data),
            force_final_tool=plan.force_final_tool,
        )
        return reported_stream(events, create_reporter()) if verbose else events

    async def atime_travel_thread_stream(
        self,
        thread_id: str,
        *,
        checkpoint_id: str,
        message: SdkMessagesInput,
        branch_from_message_id: str | None = None,
        force_final_tool: bool | None = None,
        targeted_tools: list[str] | None = None,
        structured_output: type[BaseModel] | None = None,
        verbose: bool = False,
        reasoning: ReasoningEffort | None = None,
        routing_preference: RoutingPreference | None = None,
        memory_config: MemoryConfig | Mapping[str, object] | None = None,
        model: RuntimeModelChoice | None = None,
        system_tools_config: SystemToolsConfig | Mapping[str, object] | None = None,
        orchestration_config: SessionOrchestrationConfig | Mapping[str, object] | None = None,
        options: RunOptions | None = None,
        origin: str | None = None,
    ) -> AsyncGenerator[StreamEvent, None]:
        """Asynchronously rewind this agent and stream the replacement path."""
        resolved_force_final_tool = resolve_force_final_tool(
            default=self.force_final_tool,
            call_value=force_final_tool,
        )
        _validate_agent_force_final_tool(
            self,
            force_final_tool=resolved_force_final_tool,
            structured_output=structured_output,
        )
        await self._aensure_resources_registered()
        resolved_options = self._execution_defaults().options(
            options,
            thread_id=thread_id,
            reasoning=reasoning,
            routing_preference=routing_preference,
            model=model,
            memory_config=memory_config,
            system_tools_config=system_tools_config,
            orchestration_config=orchestration_config,
            stream_response=True,
            origin=origin,
        )
        plan = plan_structured_output(
            resolved_options,
            structured_output,
            tools=select_targeted_tools(self.compile_tools(), targeted_tools),
            force_final_tool=resolved_force_final_tool,
        )
        reporter = create_reporter() if verbose else None
        events = self._client.atime_travel_thread_stream(
            thread_id,
            checkpoint_id=checkpoint_id,
            message=message,
            branch_from_message_id=branch_from_message_id,
            options=plan.options,
            tools=plan.tools,
            private_data=private_data_mapping(self.private_data),
            force_final_tool=plan.force_final_tool,
        )
        try:
            async for event in events:
                yield report_event(event, reporter)
        finally:
            await events.aclose()

    def submit_approval(
        self,
        thread_id: str,
        interrupt_id: str,
        *,
        decision: ApprovalDecision,
    ) -> ThreadAccepted:
        """Submit a thread approval decision."""
        return self._client.submit_approval(thread_id, interrupt_id, decision=decision)

    async def asubmit_approval(
        self,
        thread_id: str,
        interrupt_id: str,
        *,
        decision: ApprovalDecision,
    ) -> ThreadAccepted:
        """Submit a thread approval decision asynchronously."""
        return await self._client.asubmit_approval(thread_id, interrupt_id, decision=decision)

    def session_usage(self, session_id: str) -> ProviderUsage:
        """Return a usage snapshot for the session and its recorded related work.

        `settled` describes known work at this read, not permanent completion of
        the session family. Later derived work, such as memory enrichment, can
        change the totals even after a settled snapshot. Retain `as_of` and both
        completeness flags with the counts; waiting alone does not prove closure.
        """
        return self._client.session_usage(session_id)

    async def asession_usage(self, session_id: str) -> ProviderUsage:
        """Return provider-reported usage for a session asynchronously."""
        return await self._client.asession_usage(session_id)

    def get_thread(self, thread_id: str) -> ThreadState:
        """Fetch thread state and history."""
        return self._client.get_thread(thread_id)

    async def aget_thread(self, thread_id: str) -> ThreadState:
        """Fetch thread state and history asynchronously."""
        return await self._client.aget_thread(thread_id)

    def thread(
        self, thread_id: str | None = None, *, options: RunOptions | None = None
    ) -> BoundThread:
        """Bind a conversation identity while preserving this agent's execution defaults."""
        return BoundThread(
            self,
            thread_id,
            options=options,
            pass_thread_id=True,
            session_events=lambda session_id, replay_options: self._client.stream_session(
                session_id, options=replay_options
            ),
        )

    def thread_events(
        self,
        thread_id: str,
        *,
        options: RunOptions | None = None,
    ) -> Generator[StreamEvent, None, None]:
        """Stream thread events."""
        return self._client.thread_events(thread_id, options=options)

    async def athread_events(
        self,
        thread_id: str,
        *,
        options: RunOptions | None = None,
    ) -> AsyncGenerator[StreamEvent, None]:
        """Stream thread events asynchronously."""
        events = self._client.athread_events(thread_id, options=options)
        try:
            async for event in events:
                yield event
        finally:
            await events.aclose()

    def add_tool(
        self,
        tool: ToolTarget,
        name: str | None = None,
        description: str | None = None,
        *,
        permissions: object | None = None,
        destructive: bool = False,
        always_execute: bool = False,
        final_tool: bool = False,
        metadata: Mapping[str, object] | None = None,
        tags: list[str] | tuple[str, ...] | None = None,
        before_execute: Callable[..., object] | None = None,
        after_execute: Callable[..., object] | None = None,
    ) -> ToolTarget:
        """Register local callable metadata for future worker/tool planes."""
        metadata_handle = tool_metadata_for_callable(
            tool,
            ToolRegistrationOptions(
                name=name,
                description=description,
                permissions=permissions,
                destructive=destructive,
                always_execute=always_execute,
                final_tool=final_tool,
                metadata=metadata,
                tags=tags,
                before_execute=before_execute,
                after_execute=after_execute,
            ),
        )
        self.tools.append(metadata_handle)
        assign_tool_id(tool, metadata_handle.name)
        self._tools_dirty = True
        self._compiled_tools_cache = None
        return tool

    def toolify(
        self,
        name: str | None = None,
        description: str | None = None,
        *,
        permissions: object | None = None,
        destructive: bool = False,
        always_execute: bool = False,
        final_tool: bool = False,
        metadata: Mapping[str, object] | None = None,
        tags: list[str] | tuple[str, ...] | None = None,
        before_execute: Callable[..., object] | None = None,
        after_execute: Callable[..., object] | None = None,
    ) -> ScopeToolifyBuilder:
        """Decorate and register a callable using the v1 authoring pattern."""
        return toolify_builder_for_scope(
            self,
            name=name,
            description=description,
            permissions=permissions,
            destructive=destructive,
            always_execute=always_execute,
            final_tool=final_tool,
            metadata=metadata,
            tags=tags,
            before_execute=before_execute,
            after_execute=after_execute,
        )

    def register_mcp_servers(self, servers: MCPServer | Sequence[MCPServer]) -> None:
        """Register v1 MCPServer specs on this agent for later tool snapshots."""
        for server in normalize_mcp_servers(servers):
            existing = next(
                (registered for registered in self.mcp_servers if registered.name == server.name),
                None,
            )
            if existing is not None:
                if existing is not server:
                    message = f'MCP server name already registered: {server.name}'
                    raise ValueError(message)
                continue
            self.mcp_servers.append(server)
            self._tools_dirty = True

    def list_mcp_servers(self) -> list[MCPServer]:
        """Return MCP servers registered with this agent."""
        return list(self.mcp_servers)

    def compile_tools(self) -> list[ToolMetadata]:
        """Return the v1-compatible cached local tool metadata list."""
        return compile_scope_tools(self)

    def validate_tool_configuration(self) -> None:
        """Validate local tool metadata for v1-compatible final-output routing."""
        validate_tool_metadata(self.tools, owner=f"Agent '{self.name}'")

    def _init_services(self, *_args: object, **_kwargs: object) -> None:
        """Invalidate v1 service-backed caches; v2 has no local service repos yet."""
        self._compiled_tools_cache = None
        self._tools_dirty = True

    def resolve_memory_config(self, override: object = None) -> MemoryConfig | None:
        """Merge the agent's memory defaults with an optional per-call override."""
        return MemoryConfig.merge(
            coerce_memory_config(self.memory_config),
            coerce_optional_memory_config(override),
        )

    def resolve_system_tools_config(self, override: object = None) -> SystemToolsConfig:
        """Merge the agent's system-tool defaults with an optional override."""
        return merge_system_tools_config(
            self.system_tools_config,
            override,
            allow_private=self.allow_private_in_system_tools,
        )

    def resolve_orchestration_config(
        self,
        override: object = None,
    ) -> SessionOrchestrationConfig:
        """Merge the agent's orchestration defaults with an optional override."""
        return merge_orchestration_config(self.orchestration_config, override)

    def add_skill(
        self,
        skill: Skill | None = None,
        *,
        name: str | None = None,
        description: str | None = None,
        steps: Sequence[SkillStep | str] | None = None,
    ) -> Skill:
        """Register one first-class skill through the API."""
        stored = self._client.skills.create(
            skill_request(
                skill,
                name=name,
                description=description,
                steps=steps,
                scope=agent_scope(self.name),
            ),
        )
        cast('list[Skill]', self.skills).append(stored)
        if stored.memory_id is not None:
            self.attach_skill(stored.memory_id)
        return stored

    def add_skill_set(self, skill_set: SkillSet | str | Path) -> SkillSet:
        """Register a markdown or hardcoded first-class skill-set tree."""
        parsed = skill_set_from_input(skill_set)
        registered = self._client.skills.register_skill_set_tree(
            skill_set_with_scope(parsed, agent_scope(self.name)),
        )
        self.skill_sets.append(registered)
        return registered

    def attach_skill(self, skill_id: str) -> str:
        """Pin one stored skill for explicit injection on future runs."""
        append_unique(self.attached_skill_ids, skill_id)
        return skill_id

    def use_skills(self, query: str, *, limit: int = 16) -> Agent:
        """Opt into automatic skill retrieval on future runs."""
        self._auto_skills = build_auto_skills(query=query, limit=limit)
        return self

    def serving(
        self,
        *,
        project_id: str | None = None,
        agent_id: str | None = None,
        version: int | None = None,
        transport: ServingTransport = 'worker',
        endpoint_url: str | None = None,
        heartbeat_interval_seconds: float = HEARTBEAT_INTERVAL_SECONDS,
        listener: ServingListener | None = None,
        runner: WorkRunner | None = None,
        claim_listener: ClaimListener | None = None,
        client: Client | None = None,
    ) -> ServingSession:
        """Build the serving session this process would announce, without announcing.

        Useful when serving has to be composed with other work
        (``async with agent.serving(...):``); :meth:`serve` is the whole-process
        form. Identity defaults to this agent's ``agent_id`` (or name) and
        ``version``, shared with its trigger declarations. Per-session overrides
        do not retarget triggers that have already been declared or registered.

        ``runner`` is what a claimed fire is actually run against. It defaults
        to this agent, which is the only sensible answer for the worker
        transport: the process announced this agent, so the work belongs to it.
        A webhook-transport process receives its work as a signed push instead,
        so it takes no runner and never claims.

        ``client`` is who *announces*, which is not always who *runs*. It
        defaults to this agent's own client, the right answer for `maivn serve`
        in a developer's environment. It exists because Studio serves an app it
        has open from its own process: the announcement is then Studio's - its
        credential, its heartbeat, its lifetime - while the agent keeps running
        against whatever client the app gave it.

        Without the override an app whose client is a stub cannot be served at
        all. The bundled sample is exactly that: a `MockTransport` over a fake
        key, and because a Client shares one transport across every origin
        unless told otherwise, its announcement never left the process. It came
        back as the stub's own 404, indistinguishable from the server
        refusing a stranger.
        """
        resolved_runner = runner
        if resolved_runner is None and transport == 'worker':
            resolved_runner = self._claimed_work_runner
        announcing = client if client is not None else self._client
        return ServingSession(
            announcing,
            project_id=resolve_project_id(project_id, announcing.config),
            manifest=build_serving_manifest(
                self,
                agent_id=agent_id
                if agent_id is not None
                else (self.agent_id if self.agent_id is not None else self.name),
                version=self.version if version is None else version,
                transport=transport,
                endpoint_url=endpoint_url,
            ),
            heartbeat_interval_seconds=heartbeat_interval_seconds,
            listener=listener,
            runner=resolved_runner,
            claim_listener=claim_listener,
        )

    async def _claimed_work_runner(self, work: ClaimedWork) -> RunReport:
        """Run one claimed fire against this agent and return what it produced.

        The platform hands over an event and the message binding the trigger
        declared; the binding is rendered here because only this process can -
        the instructions, the tools and any callable the binding names all live
        here. Correlation travels as ``thread_id`` so a trigger that fires
        repeatedly for one session continues that conversation instead of
        starting a new one each time.

        The usage goes back with the answer. A served run persists in the
        ledger like any other and meters like any other, and this process is
        the only one that can see what the run consumed - the tokens were spent
        here, against whatever client this agent holds. The platform records
        the number as self-attested, because that is what it is.
        """
        response = await self.ainvoke(work.messages(), thread_id=work.session_id)
        return RunReport(output=response.response, usage=response.usage)

    async def serve(
        self,
        *,
        project_id: str | None = None,
        agent_id: str | None = None,
        version: int | None = None,
        transport: ServingTransport = 'worker',
        endpoint_url: str | None = None,
        heartbeat_interval_seconds: float = HEARTBEAT_INTERVAL_SECONDS,
        listener: ServingListener | None = None,
        runner: WorkRunner | None = None,
        claim_listener: ClaimListener | None = None,
    ) -> None:
        """Announce this agent to the platform and serve it until stopped.

        We do not host agents: this process is the agent, so publishing a
        version means announcing that it is running here and heartbeating until
        it is not. The platform learns an identity and a fingerprint of the
        runnable definition; the instructions, model settings and tool schemas
        never leave.

        On the worker transport - the default - this also claims the work the
        agent's triggers produce, runs it here, and reports the result. Nothing
        is pushed at this process; it asks. Returns after a clean stop, which is
        also what Ctrl-C produces: the DELETE goes out on the way past, and a
        fire taken but not finished is handed back rather than lost.
        """
        await self.serving(
            project_id=project_id,
            agent_id=agent_id,
            version=version,
            transport=transport,
            endpoint_url=endpoint_url,
            heartbeat_interval_seconds=heartbeat_interval_seconds,
            listener=listener,
            runner=runner,
            claim_listener=claim_listener,
        ).run()

    def _execution_defaults(self) -> ScopeDefaults:
        """Capture this scope's current defaults for every execution entry point."""
        return ScopeDefaults(
            name=self.name,
            binding_type='agent',
            model=self.model,
            attached_skill_ids=self.attached_skill_ids,
            auto_skills=self._auto_skills,
            resolve_system_tools=self.resolve_system_tools_config,
            resolve_orchestration=self.resolve_orchestration_config,
            resolve_memory=self.resolve_memory_config,
            timeout=self.timeout,
            max_results=self.max_results,
        )

    @property
    def _client(self) -> Client:
        client = self.client
        if client is None:
            message = 'Agent client was not initialized'
            raise RuntimeError(message)
        return client

    def _ensure_resources_registered(self) -> None:
        """Register declared resources before the first synchronous invocation."""
        if self._registered_resource_ids is not None:
            return
        run_blocking(
            lambda: aregister_scope_resources_once(
                self,
                scope_name=self.name,
                scope_binding_type='agent',
            ),
        )

    async def _aensure_resources_registered(self) -> None:
        """Register declared resources before the first asynchronous invocation."""
        await aregister_scope_resources_once(
            self,
            scope_name=self.name,
            scope_binding_type='agent',
        )

    def _with_system_prompt(self, messages: SdkMessagesInput) -> list[Message]:
        return messages_with_system_prompt(messages, self.system_prompt)


def _validate_agent_force_final_tool(
    agent: Agent,
    *,
    force_final_tool: bool,
    structured_output: type[BaseModel] | None,
) -> None:
    """Mirror v1 final-tool validation before invoking the v2 API."""
    if not force_final_tool or structured_output is not None:
        return
    if has_final_tool(agent.tools):
        return
    message = (
        'force_final_tool=True requires at least one tool with final_tool=True. '
        f"Agent '{agent.name}' has {len(agent.tools)} tool(s) but none are final."
    )
    raise ValueError(message)
