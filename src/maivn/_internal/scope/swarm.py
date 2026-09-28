# ruff: noqa: PLR0913 - the wide signatures here are public SDK entry points; collapsing them into a config object would break callers.
"""Developer-authored multi-agent swarm scope and its member decorator builder."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, cast

from maivn._internal.api.async_stream import run_blocking
from maivn._internal.client import Client
from maivn._internal.compat.decorators import (
    EXECUTION_CONTROLS_ATTR,
    TOOL_DEPENDENCIES_ATTR,
    AgentDependency,
    ToolDependency,
)
from maivn._internal.compat.interrupts import (
    FollowupInputHandler,
    FollowupQuestionsConfig,
    default_terminal_followup,
)
from maivn._internal.compat.invocation import (
    EventInvocationBuilder,
    response_from_stream_events,
    run_abatch,
    run_batch,
)
from maivn._internal.reporting.terminal_reporter.factory import create_reporter
from maivn._internal.scope.agent import Agent
from maivn._internal.scope.execution_hooks import (
    ainvoke_with_scope_hooks,
    astream_with_member_hooks,
    async_invoke_with_scope_hooks,
    has_member_execution_hooks,
    invoke_with_scope_hooks,
    scope_execution_hooks,
    stream_with_member_hooks,
    stream_with_scope_hooks,
)
from maivn._internal.scope.followup import FollowupInvocationBuilder
from maivn._internal.scope.normalization import (
    coerce_optional_memory_config,
    coerce_orchestration_config,
    coerce_system_tools_config,
    normalize_resource_specs,
    normalize_skill_specs,
    normalize_string_list,
    register_initial_tools,
)
from maivn._internal.scope.options import (
    messages_with_system_prompt,
    response_with_thread_id,
)
from maivn._internal.scope.preparation import (
    ScopeDefaults,
    merge_orchestration_config,
    merge_system_tools_config,
)
from maivn._internal.scope.resource_binding import aregister_scope_resources_once
from maivn._internal.scope.skill_authoring import (
    append_unique,
    build_auto_skills,
    skill_request,
    skill_set_from_input,
    skill_set_with_scope,
    swarm_scope,
)
from maivn._internal.scope.stream_reporting import report_event, reported_stream
from maivn._internal.scope.tools import (
    compile_scope_tools,
    has_final_tool,
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

    from pydantic import BaseModel

    from maivn._internal.compat.options import (
        MemoryConfig,
        SessionOrchestrationConfig,
        SystemToolsConfig,
    )
    from maivn._internal.compat.tooling import (
        ToolTarget,
    )
    from maivn._internal.models import (
        ApprovalDecision,
        InvokeResponse,
        JsonObject,
        RoutingPreference,
        RunOptions,
        StreamEvent,
        ThreadAccepted,
        ThreadState,
        ToolMetadata,
    )
    from maivn._internal.skills import (
        Skill,
        SkillSet,
        SkillStep,
    )
    from maivn.messages import SdkMessagesInput, SystemMessage


@dataclass(slots=True)
class Swarm:
    """Developer-authored multi-agent descriptor backed by the v2 API."""

    name: str
    agent_id: str | None = field(default=None, kw_only=True)
    version: int = field(default=1, kw_only=True)
    api_key: str | None = field(default=None, kw_only=True, repr=False)
    env_file: str | Path | None = field(default=None, kw_only=True)
    api_key_file: str | Path | None = field(default=None, kw_only=True)
    agents: list[Agent] = field(default_factory=list)
    description: str | None = None
    system_prompt: str | SystemMessage | None = None
    client: Client | None = None
    model: str = DEFAULT_MODEL
    before_execute: Callable[..., object] | None = None
    after_execute: Callable[..., object] | None = None
    system_tools_config: SystemToolsConfig | Mapping[str, object] | None = None
    orchestration_config: SessionOrchestrationConfig | Mapping[str, object] | None = None
    allow_private_in_system_tools: bool = False
    hook_execution_mode: HookExecutionMode = 'tool'
    tools: list[ToolSpec] = field(default_factory=list)
    skills: Sequence[SkillSpec] = field(default_factory=list[SkillSpec])
    resources: list[ResourceSpec] = field(default_factory=list)
    tags: list[str] = field(default_factory=list)
    skill_sets: list[SkillSet] = field(default_factory=list)
    attached_skill_ids: list[str] = field(default_factory=list)
    _auto_skills: AutoSkills | None = None
    _compiled_tools_cache: list[ToolMetadata] | None = None
    _compiled_hook_configuration: tuple[tuple[int, str, int, int], ...] | None = field(
        default=None, init=False, repr=False
    )
    _tools_dirty: bool = True
    _registered_resource_ids: list[str] | None = None

    def __post_init__(self) -> None:
        """Use the injected client or the first available member agent client."""
        self.system_tools_config = coerce_system_tools_config(self.system_tools_config)
        self.orchestration_config = coerce_orchestration_config(self.orchestration_config)
        self.tools = register_initial_tools(self.tools)
        self.skills = normalize_skill_specs(self.skills)
        self.resources = normalize_resource_specs(self.resources)
        self.tags = normalize_string_list(self.tags, 'tags')
        for agent in self.agents:
            self._adopt_hook_scope(agent)
        if self.client is None:
            if any(value is not None for value in (self.api_key, self.env_file, self.api_key_file)):
                self.client = Client(
                    api_key=self.api_key,
                    env_file=self.env_file,
                    api_key_file=self.api_key_file,
                )
                return
            if not self.agents:
                return
            first_client = self.agents[0].client
            if first_client is None:
                message = 'First swarm agent client was not initialized'
                raise RuntimeError(message)
            self.client = first_client

    def _adopt_hook_scope(self, agent: Agent) -> None:
        """Chain this swarm's execution hooks around the member's tools.

        Membership makes the swarm the member's outer hook scope; the member's
        compiled-tool cache is invalidated so tools compiled before adoption
        pick up the chain on their next compile.
        """
        # Swarm<->Agent family-private wiring (owner ruling 2026-08-20): this
        # hand-off is deliberately internal and must NEVER gain a public method.
        agent._parent_hook_scope = self  # pyright: ignore[reportPrivateUsage] # noqa: SLF001 - membership wiring.
        setattr(agent, '_tools_dirty', True)  # noqa: B010 - membership wiring.

    def add_agent(self, agent: Agent) -> None:
        """Register one member agent on this swarm."""
        if agent not in self.agents:
            self.agents.append(agent)
            self._adopt_hook_scope(agent)
        if self.client is None:
            self.client = agent.client

    def list_agents(self) -> list[Agent]:
        """Return member agents registered with this swarm."""
        return list(self.agents)

    @property
    def member(self) -> SwarmMemberDecoratorBuilder:
        """Build a v1-compatible member-agent registration decorator."""
        return SwarmMemberDecoratorBuilder(self)

    def invoke(
        self,
        messages: SdkMessagesInput,
        *,
        force_final_tool: bool = False,
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
        """Invoke the swarm through the API."""
        _ = (metadata, allow_private_in_system_tools)
        _validate_swarm_force_final_tool(self, force_final_tool=force_final_tool)
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
        resolved_messages = messages_with_system_prompt(messages, self.system_prompt)
        hooks = scope_execution_hooks(self)
        if stream_response or verbose or has_member_execution_hooks(self):
            events = stream_with_member_hooks(
                stream_with_scope_hooks(
                    self._client.stream(
                        resolved_messages,
                        options=resolved_options,
                        tools=self.compile_tools(),
                        force_final_tool=force_final_tool,
                        swarm_members=list(self.agents),
                    ),
                    hooks,
                    scope=self,
                    messages=resolved_messages,
                ),
                self,
            )
            if verbose:
                events = reported_stream(events, create_reporter())
            return response_with_thread_id(
                response_from_stream_events(events),
                resolved_options.thread_id,
            )
        return invoke_with_scope_hooks(
            lambda: self._client.invoke(
                resolved_messages,
                options=resolved_options,
                tools=self.compile_tools(),
                force_final_tool=force_final_tool,
                swarm_members=list(self.agents),
            ),
            hooks,
            scope=self,
            messages=resolved_messages,
        )

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
        force_final_tool: bool = False,
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
        """Invoke the swarm asynchronously through the API."""
        _ = (metadata, allow_private_in_system_tools)
        _validate_swarm_force_final_tool(self, force_final_tool=force_final_tool)
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
        resolved_messages = messages_with_system_prompt(messages, self.system_prompt)
        hooks = scope_execution_hooks(self)
        if stream_response or verbose or has_member_execution_hooks(self):
            reporter = create_reporter() if verbose else None
            events = [
                report_event(event, reporter)
                async for event in astream_with_member_hooks(
                    ainvoke_with_scope_hooks(
                        lambda: self._client.astream(
                            resolved_messages,
                            options=resolved_options,
                            tools=self.compile_tools(),
                            force_final_tool=force_final_tool,
                            swarm_members=list(self.agents),
                        ),
                        hooks,
                        scope=self,
                        messages=resolved_messages,
                    ),
                    self,
                )
            ]
            return response_with_thread_id(
                response_from_stream_events(events),
                resolved_options.thread_id,
            )
        return await async_invoke_with_scope_hooks(
            lambda: self._client.ainvoke(
                resolved_messages,
                options=resolved_options,
                tools=self.compile_tools(),
                force_final_tool=force_final_tool,
                swarm_members=list(self.agents),
            ),
            hooks,
            scope=self,
            messages=resolved_messages,
        )

    def stream(
        self,
        messages: SdkMessagesInput,
        *,
        force_final_tool: bool = False,
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
        """Stream swarm events through the API."""
        _ = (metadata, allow_private_in_system_tools)
        _validate_swarm_force_final_tool(self, force_final_tool=force_final_tool)
        self._ensure_resources_registered()
        resolved_messages = messages_with_system_prompt(messages, self.system_prompt)
        events = stream_with_member_hooks(
            stream_with_scope_hooks(
                self._client.stream(
                    resolved_messages,
                    options=self._execution_defaults().options(
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
                    ),
                    tools=self.compile_tools(),
                    force_final_tool=force_final_tool,
                    swarm_members=list(self.agents),
                ),
                scope_execution_hooks(self),
                scope=self,
                messages=resolved_messages,
            ),
            self,
        )
        return reported_stream(events, create_reporter()) if verbose else events

    async def astream(
        self,
        messages: SdkMessagesInput,
        *,
        force_final_tool: bool = False,
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
    ) -> AsyncGenerator[StreamEvent, None]:
        """Stream swarm events asynchronously through the API."""
        _ = (metadata, allow_private_in_system_tools)
        _validate_swarm_force_final_tool(self, force_final_tool=force_final_tool)
        await self._aensure_resources_registered()
        reporter = create_reporter() if verbose else None
        resolved_messages = messages_with_system_prompt(messages, self.system_prompt)
        events = astream_with_member_hooks(
            ainvoke_with_scope_hooks(
                lambda: self._client.astream(
                    resolved_messages,
                    options=self._execution_defaults().options(
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
                    ),
                    tools=self.compile_tools(),
                    force_final_tool=force_final_tool,
                    swarm_members=list(self.agents),
                ),
                scope_execution_hooks(self),
                scope=self,
                messages=resolved_messages,
            ),
            self,
        )
        try:
            async for event in events:
                yield report_event(event, reporter)
        finally:
            await events.aclose()

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
        """Invoke this swarm for multiple inputs concurrently."""
        return run_batch(self, inputs, max_concurrency=max_concurrency, invoke_kwargs=invoke_kwargs)

    async def abatch(
        self,
        inputs: Iterable[SdkMessagesInput],
        *,
        max_concurrency: int | None = None,
        **invoke_kwargs: object,
    ) -> list[InvokeResponse]:
        """Asynchronously invoke this swarm for multiple inputs concurrently."""
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
        """Start a thread session for this swarm."""
        return run_blocking(lambda: self.astart_thread(messages, options=options))

    async def astart_thread(
        self,
        messages: SdkMessagesInput,
        *,
        options: RunOptions | None = None,
    ) -> ThreadAccepted:
        """Start a thread asynchronously with this swarm's resources and defaults."""
        await self._aensure_resources_registered()
        return await self._client.astart_thread(
            messages_with_system_prompt(messages, self.system_prompt),
            options=self._execution_defaults().options(options),
            tools=self.compile_tools(),
            swarm_members=list(self.agents),
        )

    def post_thread_message(
        self,
        thread_id: str,
        message: SdkMessagesInput,
        *,
        options: RunOptions | None = None,
    ) -> ThreadAccepted:
        """Continue a swarm thread with a user message."""
        return run_blocking(lambda: self.apost_thread_message(thread_id, message, options=options))

    async def apost_thread_message(
        self,
        thread_id: str,
        message: SdkMessagesInput,
        *,
        options: RunOptions | None = None,
    ) -> ThreadAccepted:
        """Continue a thread asynchronously with this swarm's defaults."""
        await self._aensure_resources_registered()
        return await self._client.apost_thread_message(
            thread_id,
            message,
            options=self._execution_defaults().options(options),
            tools=self.compile_tools(),
            swarm_members=list(self.agents),
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
        """Continue this swarm from a prior checkpoint without changing thread id."""
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
        """Continue asynchronously from a checkpoint with this swarm's defaults."""
        await self._aensure_resources_registered()
        return await self._client.atime_travel_thread(
            thread_id,
            checkpoint_id=checkpoint_id,
            message=message,
            branch_from_message_id=branch_from_message_id,
            options=self._execution_defaults().options(options),
            tools=self.compile_tools(),
            swarm_members=list(self.agents),
        )

    def time_travel_thread_stream(
        self,
        thread_id: str,
        *,
        checkpoint_id: str,
        message: SdkMessagesInput,
        branch_from_message_id: str | None = None,
        force_final_tool: bool = False,
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
        """Rewind this swarm and stream the replacement path on the same thread."""
        _validate_swarm_force_final_tool(self, force_final_tool=force_final_tool)
        self._ensure_resources_registered()
        events = self._client.time_travel_thread_stream(
            thread_id,
            checkpoint_id=checkpoint_id,
            message=message,
            branch_from_message_id=branch_from_message_id,
            options=self._execution_defaults().options(
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
            ),
            tools=self.compile_tools(),
            force_final_tool=force_final_tool,
            swarm_members=list(self.agents),
        )
        return reported_stream(events, create_reporter()) if verbose else events

    async def atime_travel_thread_stream(
        self,
        thread_id: str,
        *,
        checkpoint_id: str,
        message: SdkMessagesInput,
        branch_from_message_id: str | None = None,
        force_final_tool: bool = False,
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
        """Asynchronously rewind this swarm and stream the replacement path."""
        _validate_swarm_force_final_tool(self, force_final_tool=force_final_tool)
        await self._aensure_resources_registered()
        reporter = create_reporter() if verbose else None
        events = self._client.atime_travel_thread_stream(
            thread_id,
            checkpoint_id=checkpoint_id,
            message=message,
            branch_from_message_id=branch_from_message_id,
            options=self._execution_defaults().options(
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
            ),
            tools=self.compile_tools(),
            force_final_tool=force_final_tool,
            swarm_members=list(self.agents),
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

    def get_thread(self, thread_id: str) -> ThreadState:
        """Fetch thread state and history."""
        return self._client.get_thread(thread_id)

    async def aget_thread(self, thread_id: str) -> ThreadState:
        """Fetch thread state and history asynchronously."""
        return await self._client.aget_thread(thread_id)

    def thread(
        self, thread_id: str | None = None, *, options: RunOptions | None = None
    ) -> BoundThread:
        """Bind a conversation identity while preserving this swarm's execution defaults."""
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

    def compile_tools(self) -> list[ToolMetadata]:
        """Return the v1-compatible cached local tool metadata list."""
        return compile_scope_tools(self)

    def validate_tool_configuration(self) -> None:
        """Validate swarm and member final-output tool declarations."""
        validate_tool_metadata(self.tools, owner=f"Swarm '{self.name}'")
        for agent in self.agents:
            agent.validate_tool_configuration()
        final_output_agents = [agent for agent in self.agents if agent.use_as_final_output]
        if has_final_tool(self.tools) and final_output_agents:
            names = [agent.name for agent in final_output_agents]
            message = (
                'swarm has conflicting final-output declarations: swarm-scope final_tool '
                f'and use_as_final_output agent(s) {names}'
            )
            raise ValueError(message)
        agents_with_final_tool = [agent for agent in self.agents if has_final_tool(agent.tools)]
        if not final_output_agents and len(agents_with_final_tool) > 1:
            names = [agent.name for agent in agents_with_final_tool]
            message = (
                'Ambiguous final_tool ownership: no use_as_final_output agent is designated '
                f'and multiple agents own final_tools: {names}'
            )
            raise ValueError(message)
        if len(final_output_agents) > 1:
            names = [agent.name for agent in final_output_agents]
            message = f'swarm has multiple use_as_final_output agents: {names}'
            raise ValueError(message)

    def _init_services(self, *_args: object, **_kwargs: object) -> None:
        """Invalidate v1 service-backed caches; v2 has no local service repos yet."""
        self._compiled_tools_cache = None
        self._tools_dirty = True

    def resolve_system_tools_config(self, override: object = None) -> SystemToolsConfig:
        """Merge the swarm's system-tool defaults with an optional override."""
        return merge_system_tools_config(
            self.system_tools_config,
            override,
            allow_private=self.allow_private_in_system_tools,
        )

    def resolve_orchestration_config(
        self,
        override: object = None,
    ) -> SessionOrchestrationConfig:
        """Merge the swarm's orchestration defaults with an optional override."""
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
                scope=swarm_scope(self.name),
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
            skill_set_with_scope(parsed, swarm_scope(self.name)),
        )
        self.skill_sets.append(registered)
        return registered

    def attach_skill(self, skill_id: str) -> str:
        """Pin one stored skill for explicit injection on future runs."""
        append_unique(self.attached_skill_ids, skill_id)
        return skill_id

    def use_skills(self, query: str, *, limit: int = 16) -> Swarm:
        """Opt into automatic skill retrieval on future runs."""
        self._auto_skills = build_auto_skills(query=query, limit=limit)
        return self

    def _execution_defaults(self) -> ScopeDefaults:
        """Capture this scope's current defaults for every execution entry point."""
        return ScopeDefaults(
            name=self.name,
            binding_type='swarm',
            model=self.model,
            attached_skill_ids=self.attached_skill_ids,
            auto_skills=self._auto_skills,
            resolve_system_tools=self.resolve_system_tools_config,
            resolve_orchestration=self.resolve_orchestration_config,
            resolve_memory=coerce_optional_memory_config,
        )

    @property
    def _client(self) -> Client:
        client = self.client
        if client is None:
            client = Client()
            self.client = client
        return client

    def _ensure_resources_registered(self) -> None:
        """Register declared resources before the first synchronous invocation."""
        for agent in self.agents:
            agent._ensure_resources_registered()  # pyright: ignore[reportPrivateUsage] # noqa: SLF001 - owned swarm membership.
        if self._registered_resource_ids is not None:
            return
        run_blocking(
            lambda: aregister_scope_resources_once(
                self,
                scope_name=self.name,
                scope_binding_type='swarm',
            ),
        )

    async def _aensure_resources_registered(self) -> None:
        """Register declared resources before the first asynchronous invocation."""
        for agent in self.agents:
            await agent._aensure_resources_registered()  # pyright: ignore[reportPrivateUsage] # noqa: SLF001 - owned swarm membership.
        await aregister_scope_resources_once(
            self,
            scope_name=self.name,
            scope_binding_type='swarm',
        )


class SwarmMemberDecoratorBuilder:
    """V1-compatible builder for registering dependency-aware swarm members."""

    def __init__(self, swarm: Swarm) -> None:
        """Create a member builder bound to one swarm."""
        self._swarm = swarm
        self._dependencies: list[object] = []
        self._execution_controls: list[object] = []

    def __call__(
        self,
        target: Agent | Callable[[], object] | None = None,
    ) -> Agent | SwarmMemberDecoratorBuilder:
        """Register an Agent instance or a zero-argument Agent factory."""
        if target is None:
            return self

        agent = _resolve_member_agent(target)
        _copy_member_metadata(target, agent)
        _extend_metadata(agent, TOOL_DEPENDENCIES_ATTR, self._dependencies)
        _extend_metadata(agent, EXECUTION_CONTROLS_ATTR, self._execution_controls)
        self._swarm.add_agent(agent)
        return agent

    def depends_on_agent(
        self,
        agent_ref: Agent | str,
        arg_name: str,
    ) -> SwarmMemberDecoratorBuilder:
        """Record a dependency on another member agent."""
        self._dependencies.append(
            AgentDependency(
                arg_name=arg_name,
                agent_id=_agent_id(agent_ref),
                agent_ref=agent_ref,
            )
        )
        return self

    def depends_on_tool(
        self,
        tool_ref: str | ToolTarget,
        arg_name: str,
    ) -> SwarmMemberDecoratorBuilder:
        """Record a dependency on a swarm-level or member tool."""
        tool_id, tool_name = _tool_id_and_name(tool_ref)
        self._dependencies.append(
            ToolDependency(arg_name=arg_name, tool_id=tool_id, tool_name=tool_name)
        )
        return self


def _resolve_member_agent(target: Agent | Callable[[], object]) -> Agent:
    """Resolve a member decorator target into an Agent instance."""
    if isinstance(target, Agent):
        return target
    agent = target()
    if isinstance(agent, Agent):
        return agent
    message = 'swarm.member expects an Agent instance or zero-argument Agent factory'
    raise TypeError(message)


def _copy_member_metadata(source: object, agent: Agent) -> None:
    """Copy dependency metadata from a member factory or prebuilt agent."""
    _copy_metadata_attr(source, agent, TOOL_DEPENDENCIES_ATTR)
    _copy_metadata_attr(source, agent, EXECUTION_CONTROLS_ATTR)


def _copy_metadata_attr(source: object, target: object, attr: str) -> None:
    """Copy one metadata list between decorated objects."""
    values = getattr(source, attr, None)
    if isinstance(values, list):
        _extend_metadata(target, attr, cast('list[object]', values))


def _extend_metadata(target: object, attr: str, values: Sequence[object]) -> None:
    """Append metadata values to a target object without duplicating identities."""
    if not values:
        return
    current = getattr(target, attr, None)
    if current is None:
        stored: list[object] = []
        setattr(target, attr, stored)
    elif isinstance(current, list):
        stored = cast('list[object]', current)
    else:
        message = f'{attr} metadata must be a list'
        raise TypeError(message)
    for value in values:
        if value not in stored:
            stored.append(value)


def _agent_id(agent_ref: Agent | str) -> str:
    """Return a stable v1-ish identifier for an agent reference."""
    if isinstance(agent_ref, str):
        return agent_ref
    for attr in ('agent_id', 'id', 'name'):
        value = getattr(agent_ref, attr, None)
        if isinstance(value, str) and value:
            return value
    return str(agent_ref)


def _tool_id_and_name(tool_ref: str | ToolTarget) -> tuple[str, str]:
    """Return v1-compatible tool dependency identifiers."""
    if isinstance(tool_ref, str):
        return tool_ref, tool_ref
    name = str(getattr(tool_ref, '__name__', type(tool_ref).__name__))
    tool_id = getattr(tool_ref, 'tool_id', name)
    return str(tool_id), name


def _validate_swarm_force_final_tool(swarm: Swarm, *, force_final_tool: bool) -> None:
    """Mirror v1 swarm force-final disambiguation for compat invocations."""
    if not force_final_tool:
        return
    swarm_final = has_final_tool(swarm.tools)
    final_output_agents = [agent for agent in swarm.agents if agent.use_as_final_output]
    agents_with_final_tool = [agent for agent in swarm.agents if has_final_tool(agent.tools)]

    if swarm_final and final_output_agents:
        names = [agent.name for agent in final_output_agents]
        message = (
            'Swarm.invoke(force_final_tool=True) has conflicting final-output '
            'declarations: the swarm owns a swarm-scope final_tool and '
            f'sub-agent(s) {names} are marked use_as_final_output=True.'
        )
        raise ValueError(message)
    if not swarm_final and not final_output_agents and not agents_with_final_tool:
        message = (
            'Swarm.invoke(force_final_tool=True) requires at least one of: '
            'a swarm-scope tool with final_tool=True, an agent with use_as_final_output=True, '
            'or an agent that owns a tool with final_tool=True.'
        )
        raise ValueError(message)
    if len(agents_with_final_tool) > 1 and not final_output_agents:
        message = (
            'Swarm.invoke(force_final_tool=True) is ambiguous: multiple agents own '
            'final_tool but none are marked use_as_final_output=True.'
        )
        raise ValueError(message)
