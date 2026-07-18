"""Builders for BaseScope decorators and structured output."""

# pyright: strict
from __future__ import annotations

from collections.abc import Callable, Iterable, Iterator, Sequence
from typing import Literal, TypeAlias, TypeVar, cast

from maivn_shared import (
    BaseMessage,
    MemoryConfig,
    SessionOrchestrationConfig,
    SessionResponse,
    SystemToolsConfig,
)
from maivn_shared.domain.entities.dependencies import ExecutionInstanceControl, ExecutionTiming
from pydantic import BaseModel

from maivn._internal.core.entities.tools import BaseTool
from maivn._internal.core.interfaces.repositories import DependencyRepoInterface
from maivn._internal.core.registrars import ToolRegistrar
from maivn._internal.core.services.toolify import ToolifyOptions, ToolifyService
from maivn._internal.utils.decorators import (
    compose_artifact_policy,
    depends_on_agent,
    depends_on_await_for,
    depends_on_interrupt,
    depends_on_private_data,
    depends_on_reevaluate,
    depends_on_tool,
)
from maivn._internal.utils.decorators._types import ToolReference
from maivn._internal.utils.reporting import create_reporter
from maivn._internal.utils.reporting.context import current_reporter, get_current_reporter
from maivn._internal.utils.reporting.terminal_reporter import BaseReporter
from maivn._internal.utils.reporting.terminal_reporter.event_router import (
    EventPayloadSink,
    EventRouterReporter,
)

# MARK: Types

_PENDING_DEPS_ATTR = "__maivn_pending_deps__"
_PENDING_CONTROLS_ATTR = "__maivn_pending_execution_controls__"
_PENDING_ARG_POLICIES_ATTR = "__maivn_pending_arg_policies__"

ToolifyTarget: TypeAlias = Callable[..., object] | type[BaseModel]
ToolDecorator: TypeAlias = Callable[[ToolifyTarget], ToolifyTarget]
TToolifyTarget = TypeVar("TToolifyTarget", bound=ToolifyTarget)


# MARK: - Toolify Builder


class ToolifyDecoratorBuilder:
    """Builder for toolify decorator with fluent dependency configuration."""

    # MARK: - Initialization

    def __init__(self, scope: object, options: ToolifyOptions) -> None:
        self._scope: object = scope
        self._options: ToolifyOptions = options
        self._decorators: list[ToolDecorator] = []

    # MARK: - Dependency Configuration

    def depends_on_agent(self, agent_ref: object, arg_name: str) -> ToolifyDecoratorBuilder:
        self._decorators.append(depends_on_agent(agent_ref=agent_ref, arg_name=arg_name))
        return self

    def depends_on_tool(
        self,
        tool_ref: ToolReference,
        arg_name: str,
    ) -> ToolifyDecoratorBuilder:
        self._decorators.append(depends_on_tool(tool_ref=tool_ref, arg_name=arg_name))
        return self

    def depends_on_private_data(self, data_key: str, arg_name: str) -> ToolifyDecoratorBuilder:
        self._decorators.append(depends_on_private_data(data_key=data_key, arg_name=arg_name))
        return self

    def compose_artifact_policy(
        self,
        arg_name: str,
        *,
        mode: Literal["forbid", "allow", "require"] = "allow",
        approval: Literal["none", "explicit"] = "none",
    ) -> ToolifyDecoratorBuilder:
        self._decorators.append(
            compose_artifact_policy(arg_name=arg_name, mode=mode, approval=approval)
        )
        return self

    def depends_on_await_for(
        self,
        tool_ref: ToolReference,
        *,
        timing: ExecutionTiming = "after",
        instance_control: ExecutionInstanceControl = "each",
    ) -> ToolifyDecoratorBuilder:
        self._decorators.append(
            depends_on_await_for(
                tool_ref=tool_ref,
                timing=timing,
                instance_control=instance_control,
            )
        )
        return self

    def depends_on_reevaluate(
        self,
        tool_ref: ToolReference,
        *,
        timing: ExecutionTiming = "after",
        instance_control: ExecutionInstanceControl = "each",
    ) -> ToolifyDecoratorBuilder:
        self._decorators.append(
            depends_on_reevaluate(
                tool_ref=tool_ref,
                timing=timing,
                instance_control=instance_control,
            )
        )
        return self

    def depends_on_interrupt(
        self,
        arg_name: str,
        prompt: str,
        input_handler: Callable[[str], object],
    ) -> ToolifyDecoratorBuilder:
        self._decorators.append(
            depends_on_interrupt(
                arg_name=arg_name,
                prompt=prompt,
                input_handler=input_handler,
            )
        )
        return self

    # MARK: - Invocation

    def __call__(self, obj: TToolifyTarget) -> TToolifyTarget:
        decorated_obj = self._apply_decorators(obj)
        tool = self._create_and_register_tool(decorated_obj)
        self._attach_tool_id(decorated_obj, tool)
        return decorated_obj

    # MARK: - Private Helpers

    def _apply_decorators(self, obj: TToolifyTarget) -> TToolifyTarget:
        decorated: ToolifyTarget = obj
        for decorator in self._decorators:
            decorated = decorator(decorated)
        pending_deps = getattr(decorated, _PENDING_DEPS_ATTR, None)
        if pending_deps is not None:
            setattr(decorated, _PENDING_DEPS_ATTR, [])
        pending_controls = getattr(decorated, _PENDING_CONTROLS_ATTR, None)
        if pending_controls is not None:
            setattr(decorated, _PENDING_CONTROLS_ATTR, [])
        pending_arg_policies = getattr(decorated, _PENDING_ARG_POLICIES_ATTR, None)
        if pending_arg_policies is not None:
            setattr(decorated, _PENDING_ARG_POLICIES_ATTR, [])
        return cast(TToolifyTarget, decorated)

    def _create_and_register_tool(self, obj: ToolifyTarget) -> BaseTool:
        toolify_service = self._toolify_service()
        tool = toolify_service.create_tool(obj, self._options)
        toolify_service.register_tool(
            tool=tool,
            registrar=cast(
                ToolRegistrar,
                getattr(self._scope, "_tool_registrar"),  # noqa: B009 - Pydantic PrivateAttr.
            ),
            dependency_repo=cast(
                DependencyRepoInterface,
                getattr(self._scope, "_dependency_repo"),  # noqa: B009 - Pydantic PrivateAttr.
            ),
        )
        toolify_service.setup_dependency_callback(
            obj=obj,
            tool=tool,
            dependency_repo=cast(
                DependencyRepoInterface,
                getattr(self._scope, "_dependency_repo"),  # noqa: B009 - Pydantic PrivateAttr.
            ),
        )
        setattr(self._scope, "_tools_dirty", True)  # noqa: B010 - Pydantic PrivateAttr.
        return tool

    def _toolify_service(self) -> ToolifyService:
        return cast(
            ToolifyService,
            getattr(self._scope, "_toolify_service"),  # noqa: B009 - Pydantic PrivateAttr.
        )

    def _attach_tool_id(self, obj: object, tool: BaseTool) -> None:
        tool_id = getattr(tool, "tool_id", None)
        if tool_id is not None:
            try:
                setattr(obj, "tool_id", tool_id)  # noqa: B010 - dynamic tool target.
            except Exception:  # noqa: BLE001 - dynamic targets may reject attribute assignment.
                pass


# MARK: - Structured Output Builder


class StructuredOutputInvocationBuilder:
    """Builder for structured output invocation with type-safe response handling."""

    # MARK: - Initialization

    def __init__(self, scope: object, model: type[BaseModel]) -> None:
        self._scope: object = scope
        self._model: type[BaseModel] = model

    # MARK: - Invocation

    def invoke(
        self,
        messages: Sequence[BaseMessage],
        *,
        force_final_tool: bool = False,
        model: Literal["fast", "balanced", "max", "ultra"] | None = None,
        reasoning: Literal["minimal", "low", "medium", "high"] | None = None,
        stream_response: bool = True,
        thread_id: str | None = None,
        verbose: bool = False,
        metadata: dict[str, object] | None = None,
        memory_config: MemoryConfig | dict[str, object] | None = None,
        system_tools_config: SystemToolsConfig | dict[str, object] | None = None,
        orchestration_config: SessionOrchestrationConfig | dict[str, object] | None = None,
        allow_private_in_system_tools: bool | None = None,
    ) -> SessionResponse:
        invoke_fn = self._get_invoke_function()
        return invoke_fn(
            messages=messages,
            force_final_tool=force_final_tool,
            structured_output=self._model,
            model=model,
            reasoning=reasoning,
            stream_response=stream_response,
            thread_id=thread_id,
            verbose=verbose,
            metadata=metadata,
            memory_config=memory_config,
            system_tools_config=system_tools_config,
            orchestration_config=orchestration_config,
            allow_private_in_system_tools=allow_private_in_system_tools,
        )

    # MARK: - Private Helpers

    def _get_invoke_function(self) -> Callable[..., SessionResponse]:
        invoke_fn = getattr(self._scope, "invoke", None)
        if invoke_fn is None:
            raise AttributeError("Scope does not support invoke().")
        return cast(Callable[..., SessionResponse], invoke_fn)


# MARK: - Event Invocation Builder


class EventInvocationBuilder:
    """Builder for invoking scopes with event filtering and payload routing."""

    def __init__(
        self,
        scope: object,
        *,
        include: Iterable[str] | str | None = None,
        exclude: Iterable[str] | str | None = None,
        on_event: EventPayloadSink | None = None,
        auto_verbose: bool = True,
    ) -> None:
        self._scope: object = scope
        self._include: Iterable[str] | str | None = include
        self._exclude: Iterable[str] | str | None = exclude
        self._on_event: EventPayloadSink | None = on_event
        self._auto_verbose: bool = auto_verbose

    def invoke(self, *args: object, **kwargs: object) -> SessionResponse:
        invoke_fn = self._get_scope_method("invoke")
        call_kwargs = self._prepare_call_kwargs(kwargs)
        reporter = self._build_router_reporter()
        token = current_reporter.set(reporter)
        try:
            result = invoke_fn(*args, **call_kwargs)
        finally:
            current_reporter.reset(token)
        return cast(SessionResponse, result)

    def stream(self, *args: object, **kwargs: object) -> Iterator[object]:
        stream_fn = self._get_scope_method("stream")
        call_kwargs = self._prepare_call_kwargs(kwargs)
        reporter = self._build_router_reporter()

        def _stream_with_event_reporter() -> Iterator[object]:
            stream_iter: Iterator[object] | None = None
            try:
                while True:
                    token = current_reporter.set(reporter)
                    try:
                        if stream_iter is None:
                            result = stream_fn(*args, **call_kwargs)
                            stream_iter = iter(cast(Iterable[object], result))
                        item = next(stream_iter)
                    except StopIteration:
                        return
                    finally:
                        current_reporter.reset(token)

                    yield item
            finally:
                if stream_iter is not None:
                    close = getattr(stream_iter, "close", None)
                    if callable(close):
                        token = current_reporter.set(reporter)
                        try:
                            _ = close()
                        finally:
                            current_reporter.reset(token)

        return _stream_with_event_reporter()

    def _get_scope_method(self, method_name: str) -> Callable[..., object]:
        method = getattr(self._scope, method_name, None)
        if method is None:
            raise AttributeError(f"Scope does not support {method_name}().")
        return cast(Callable[..., object], method)

    def _prepare_call_kwargs(self, kwargs: dict[str, object]) -> dict[str, object]:
        call_kwargs = dict(kwargs)
        if self._auto_verbose and "verbose" not in call_kwargs:
            call_kwargs["verbose"] = True
        return call_kwargs

    def _build_router_reporter(self) -> BaseReporter:
        base_reporter = get_current_reporter()
        if base_reporter is None:
            base_reporter = create_reporter(enabled=True)
        return EventRouterReporter(
            base_reporter,
            include=self._include,
            exclude=self._exclude,
            event_sink=self._on_event,
        )


__all__ = [
    "EventInvocationBuilder",
    "StructuredOutputInvocationBuilder",
    "ToolifyDecoratorBuilder",
]
