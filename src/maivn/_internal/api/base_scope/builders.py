"""Builders for BaseScope decorators and structured output."""

from __future__ import annotations

from collections.abc import Callable, Iterable, Iterator, Sequence
from typing import Any, Literal, cast

from maivn_shared import BaseMessage, MemoryConfig, SessionResponse
from maivn_shared.domain.entities.dependencies import ExecutionInstanceControl, ExecutionTiming
from pydantic import BaseModel

from maivn._internal.core.entities.tools import BaseTool
from maivn._internal.core.services.toolify import ToolifyOptions
from maivn._internal.utils.decorators import (
    compose_artifact_policy,
    depends_on_agent,
    depends_on_await_for,
    depends_on_interrupt,
    depends_on_private_data,
    depends_on_reevaluate,
    depends_on_tool,
)
from maivn._internal.utils.reporting import create_reporter
from maivn._internal.utils.reporting.context import current_reporter, get_current_reporter
from maivn._internal.utils.reporting.terminal_reporter import BaseReporter
from maivn._internal.utils.reporting.terminal_reporter.event_router import (
    EventPayloadSink,
    EventRouterReporter,
)

# MARK: - Toolify Builder


class ToolifyDecoratorBuilder:
    """Builder for toolify decorator with fluent dependency configuration."""

    # MARK: - Initialization

    def __init__(self, scope: Any, options: ToolifyOptions) -> None:
        self._scope = scope
        self._options = options
        self._decorators: list[Callable[[Any], Any]] = []

    # MARK: - Dependency Configuration

    def depends_on_agent(self, agent_ref: str | Any, arg_name: str) -> ToolifyDecoratorBuilder:
        self._decorators.append(depends_on_agent(agent_ref=agent_ref, arg_name=arg_name))
        return self

    def depends_on_tool(
        self,
        tool_ref: str | BaseTool | Callable,
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
        tool_ref: str | BaseTool | Callable,
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
        tool_ref: str | BaseTool | Callable,
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
        input_handler: Callable[[str], Any],
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

    def __call__(self, obj: Any) -> Any:
        decorated_obj = self._apply_decorators(obj)
        tool = self._create_and_register_tool(decorated_obj)
        self._attach_tool_id(decorated_obj, tool)
        return decorated_obj

    # MARK: - Private Helpers

    def _apply_decorators(self, obj: Any) -> Any:
        for decorator in self._decorators:
            obj = decorator(obj)
        pending_deps = getattr(obj, "__maivn_pending_deps__", None)
        if pending_deps is not None:
            obj.__maivn_pending_deps__ = []
        pending_controls = getattr(obj, "__maivn_pending_execution_controls__", None)
        if pending_controls is not None:
            obj.__maivn_pending_execution_controls__ = []
        pending_arg_policies = getattr(obj, "__maivn_pending_arg_policies__", None)
        if pending_arg_policies is not None:
            obj.__maivn_pending_arg_policies__ = []
        return obj

    def _create_and_register_tool(self, obj: Any) -> BaseTool:
        tool = self._scope._toolify_service.create_tool(obj, self._options)
        self._scope._toolify_service.register_tool(
            tool=tool,
            registrar=self._scope._tool_registrar,
            dependency_repo=self._scope._dependency_repo,
        )
        self._scope._toolify_service.setup_dependency_callback(
            obj=obj,
            tool=tool,
            dependency_repo=self._scope._dependency_repo,
        )
        self._scope._tools_dirty = True
        return tool

    def _attach_tool_id(self, obj: Any, tool: BaseTool) -> None:
        tool_id = getattr(tool, "tool_id", None)
        if tool_id is not None:
            try:
                obj.tool_id = tool_id
            except Exception:
                pass


# MARK: - Structured Output Builder


class StructuredOutputInvocationBuilder:
    """Builder for structured output invocation with type-safe response handling."""

    # MARK: - Initialization

    def __init__(self, scope: Any, model: type[BaseModel]) -> None:
        self._scope = scope
        self._model = model

    # MARK: - Invocation

    def invoke(
        self,
        messages: Sequence[BaseMessage],
        *,
        force_final_tool: bool = False,
        model: Literal["fast", "balanced", "max"] | None = None,
        reasoning: Literal["minimal", "low", "medium", "high"] | None = None,
        stream_response: bool = True,
        thread_id: str | None = None,
        verbose: bool = False,
        metadata: dict[str, Any] | None = None,
        memory_config: MemoryConfig | dict[str, Any] | None = None,
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
            allow_private_in_system_tools=allow_private_in_system_tools,
        )

    # MARK: - Private Helpers

    def _get_invoke_function(self) -> Callable[..., SessionResponse]:
        scope_any = cast(Any, self._scope)
        invoke_fn = getattr(scope_any, "invoke", None)
        if invoke_fn is None:
            raise AttributeError("Scope does not support invoke().")
        return cast(Callable[..., SessionResponse], invoke_fn)


# MARK: - Event Invocation Builder


class EventInvocationBuilder:
    """Builder for invoking scopes with event filtering and payload routing."""

    def __init__(
        self,
        scope: Any,
        *,
        include: Iterable[str] | str | None = None,
        exclude: Iterable[str] | str | None = None,
        on_event: EventPayloadSink | None = None,
        auto_verbose: bool = True,
    ) -> None:
        self._scope = scope
        self._include = include
        self._exclude = exclude
        self._on_event = on_event
        self._auto_verbose = auto_verbose

    def invoke(self, *args: Any, **kwargs: Any) -> SessionResponse:
        invoke_fn = self._get_scope_method("invoke")
        call_kwargs = self._prepare_call_kwargs(kwargs)
        reporter = self._build_router_reporter()
        token = current_reporter.set(reporter)
        try:
            result = invoke_fn(*args, **call_kwargs)
        finally:
            current_reporter.reset(token)
        return cast(SessionResponse, result)

    def stream(self, *args: Any, **kwargs: Any) -> Iterator[Any]:
        stream_fn = self._get_scope_method("stream")
        call_kwargs = self._prepare_call_kwargs(kwargs)
        reporter = self._build_router_reporter()

        def _stream_with_event_reporter() -> Iterator[Any]:
            stream_iter: Iterator[Any] | None = None
            try:
                while True:
                    token = current_reporter.set(reporter)
                    try:
                        if stream_iter is None:
                            result = stream_fn(*args, **call_kwargs)
                            stream_iter = iter(cast(Iterable[Any], result))
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
                            close()
                        finally:
                            current_reporter.reset(token)

        return _stream_with_event_reporter()

    def _get_scope_method(self, method_name: str) -> Callable[..., Any]:
        scope_any = cast(Any, self._scope)
        method = getattr(scope_any, method_name, None)
        if method is None:
            raise AttributeError(f"Scope does not support {method_name}().")
        return cast(Callable[..., Any], method)

    def _prepare_call_kwargs(self, kwargs: dict[str, Any]) -> dict[str, Any]:
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
