"""Dependency decorator utilities.
Exposes decorators for declaring tool, agent, data, and interrupt dependencies.
"""

from __future__ import annotations

import inspect
from collections.abc import Callable
from typing import Any, Literal, TypeVar, cast, get_args, get_origin, get_type_hints

from maivn_shared import (
    AgentDependency,
    BaseDependency,
    DataDependency,
    InterruptDependency,
    ToolDependency,
    create_uuid,
)
from maivn_shared.domain.entities.dependencies import (
    AwaitForDependency,
    ExecutionInstanceControl,
    ExecutionTiming,
    InputType,
    ReevaluateDependency,
)
from pydantic import BaseModel

from maivn._internal.core.entities import BaseTool

# MARK: Types

DependencyT = TypeVar("DependencyT", bound=BaseDependency)
ComposeArtifactMode = Literal["forbid", "allow", "require"]
ComposeArtifactApproval = Literal["none", "explicit"]


# MARK: Public Decorators


def depends_on_agent(agent_ref: str | Any, arg_name: str) -> Callable:
    """Declare a dependency on another agent.

    Args:
        agent_ref: Agent object, agent ID, or agent name.
        arg_name: The argument name to receive the injected agent.
    """
    agent_id = getattr(agent_ref, "agent_id", str(agent_ref))
    return _create_dependency_decorator(
        dependency_model=AgentDependency,
        arg_name=arg_name,
        agent_id=agent_id,
    )


def depends_on_tool(tool_ref: str | BaseTool | Callable, arg_name: str) -> Callable:
    """Declare a dependency on another tool.

    Args:
        tool_ref: Tool object, tool ID/name, or callable (function/class) to be toolified.
        arg_name: The argument name to receive the result.
    """
    if callable(tool_ref):
        tool_id = getattr(tool_ref, "tool_id", None) or create_uuid(tool_ref)
    else:
        tool_id = getattr(tool_ref, "tool_id", str(tool_ref))

    return _create_dependency_decorator(
        dependency_model=ToolDependency,
        arg_name=arg_name,
        tool_id=tool_id,
    )


def depends_on_private_data(data_key: str, arg_name: str) -> Callable:
    """Declare a dependency on external data from the current scope's private_data.

    Args:
        data_key: The key to look up on the scope's private_data.
        arg_name: The argument name to receive the data value.
    """
    return _create_dependency_decorator(
        dependency_model=DataDependency,
        arg_name=arg_name,
        data_key=data_key,
    )


def compose_artifact_policy(
    arg_name: str,
    *,
    mode: ComposeArtifactMode = "allow",
    approval: ComposeArtifactApproval = "none",
) -> Callable:
    """Declare compose_artifact usage policy for a specific tool argument."""

    def decorator(obj: Callable | type[BaseModel]) -> Callable | type[BaseModel]:
        _validate_arg_target(obj, arg_name)
        policy = _normalize_compose_artifact_policy(
            arg_name=arg_name,
            mode=mode,
            approval=approval,
        )
        _attach_arg_policy(obj, policy)
        return obj

    return decorator


def depends_on_await_for(
    tool_ref: str | BaseTool | Callable,
    *,
    timing: ExecutionTiming = "after",
    instance_control: ExecutionInstanceControl = "each",
) -> Callable:
    return _create_execution_control_decorator(
        control_model=AwaitForDependency,
        tool_ref=tool_ref,
        timing=timing,
        instance_control=instance_control,
    )


def depends_on_reevaluate(
    tool_ref: str | BaseTool | Callable,
    *,
    timing: ExecutionTiming = "after",
    instance_control: ExecutionInstanceControl = "each",
) -> Callable:
    return _create_execution_control_decorator(
        control_model=ReevaluateDependency,
        tool_ref=tool_ref,
        timing=timing,
        instance_control=instance_control,
    )


def depends_on_interrupt(
    arg_name: str,
    input_handler: Callable[[str], Any],
    prompt: str = "",
    input_type: InputType | None = None,
    choices: list[str] | None = None,
) -> Callable:
    """Declare a dependency that interrupts execution for user input.

    Args:
        arg_name: The argument name to receive the user input.
        input_handler: The function to call to get user input.
        prompt: The prompt to display when requesting input. Optional when
            the input_handler has its own prompting logic.
        input_type: Override the input type (text, choice, boolean, number, email, password).
            If not provided, will be auto-detected from type annotations.
        choices: Override choices for choice input type.
            If not provided, will be auto-detected from Literal type annotations.
    """

    def decorator(func: Callable) -> Callable:
        if _attach_interrupt_team_dependency_if_supported(
            func,
            arg_name=arg_name,
            input_handler=input_handler,
            prompt=prompt,
            input_type=input_type or "text",
            choices=choices or [],
        ):
            return func

        if _should_store_pending_team_dependency(func, arg_name):
            dependency = InterruptDependency(
                arg_name=arg_name,
                prompt=prompt,
                input_handler=input_handler,
                input_type=input_type or "text",
                choices=choices or [],
            )
            _attach_dependency(func, dependency)
            return func

        _validate_arg_in_signature(func, arg_name)

        # Auto-detect input_type and choices from type annotations
        detected_type, detected_choices = _detect_input_type_from_annotation(func, arg_name)
        final_type = input_type or detected_type
        final_choices = choices if choices is not None else detected_choices

        dependency = InterruptDependency(
            arg_name=arg_name,
            prompt=prompt,
            input_handler=input_handler,
            input_type=final_type,
            choices=final_choices,
        )
        _attach_dependency(func, dependency)
        return func

    return decorator


def _create_execution_control_decorator(
    control_model: type[AwaitForDependency] | type[ReevaluateDependency],
    tool_ref: str | BaseTool | Callable,
    *,
    timing: ExecutionTiming,
    instance_control: ExecutionInstanceControl,
) -> Callable[[Callable], Callable]:
    def decorator(func: Callable) -> Callable:
        resolver = getattr(func, "_resolve_team_control_reference", None)
        if callable(resolver):
            resolve_team_control = cast(Callable[[Any], tuple[str, str]], resolver)
            tool_id, tool_name = resolve_team_control(tool_ref)
        else:
            tool_id, tool_name = _resolve_tool_reference(tool_ref)
        control = control_model(
            tool_id=tool_id,
            tool_name=tool_name,
            timing=timing,
            instance_control=instance_control,
        )
        _attach_execution_control(func, control)
        return func

    return decorator


# MARK: Internal Helpers


def _detect_input_type_from_annotation(
    func: Callable,
    arg_name: str,
) -> tuple[InputType, list[str]]:
    """Detect input type and choices from function parameter type annotation.

    Args:
        func: The function to inspect.
        arg_name: The argument name to check.

    Returns:
        Tuple of (input_type, choices).
        - For Literal types: ("choice", [literal_values])
        - For bool: ("boolean", [])
        - For int/float: ("number", [])
        - Otherwise: ("text", [])
    """
    try:
        hints = get_type_hints(func)
        if arg_name not in hints:
            return "text", []

        annotation = hints[arg_name]

        # Unwrap Optional/Union to check underlying types
        origin = get_origin(annotation)
        if origin is not None and origin is not Literal:
            args = [arg for arg in get_args(annotation) if arg is not type(None)]
            if len(args) == 1:
                annotation = args[0]

        # Check for Literal type
        origin = get_origin(annotation)
        if origin is Literal:
            literal_values = get_args(annotation)
            choices = [str(v) for v in literal_values]
            return "choice", choices

        # Check for bool
        if annotation is bool:
            return "boolean", []

        # Check for numeric types
        if annotation in (int, float):
            return "number", []

        return "text", []
    except Exception:
        # If annotation inspection fails, default to text
        return "text", []


def _create_dependency_decorator(
    dependency_model: type[DependencyT],
    arg_name: str,
    **model_kwargs: Any,
) -> Callable[[Callable], Callable]:
    """Create a dependency decorator with the given model and arguments.

    This decorator works regardless of order with @toolify by:
    1. Always attaching to _dependencies attribute
    2. Calling __maivn_register_dependency__ if it exists (toolify applied first)
    3. Storing in __maivn_pending_deps__ if toolify hasn't been applied yet
    """

    def decorator(func: Callable) -> Callable:
        if _attach_team_dependency_if_supported(func, dependency_model, arg_name, model_kwargs):
            return func
        if _should_store_pending_team_dependency(func, arg_name):
            dependency = dependency_model(arg_name=arg_name, **model_kwargs)
            _attach_dependency(func, dependency)
            return func
        _validate_arg_in_signature(func, arg_name)
        dependency = dependency_model(arg_name=arg_name, **model_kwargs)
        _attach_dependency(func, dependency)
        return func

    return decorator


def _validate_arg_in_signature(func: Callable, arg_name: str) -> None:
    """Validate that arg_name exists in the function signature."""
    sig = inspect.signature(func)
    if arg_name not in sig.parameters:
        func_name = getattr(func, "__name__", "<function>")
        raise ValueError(
            f"Argument '{arg_name}' specified in dependency decorator "
            f"not found in function '{func_name}' signature: {sig}"
        )


def _validate_arg_target(obj: Callable | type[BaseModel], arg_name: str) -> None:
    if inspect.isclass(obj) and issubclass(obj, BaseModel):
        model_fields = getattr(obj, "model_fields", {})
        if arg_name not in model_fields:
            model_name = getattr(obj, "__name__", "<model>")
            raise ValueError(
                f"Argument '{arg_name}' specified in compose_artifact_policy "
                f"not found in model '{model_name}' fields"
            )
        return

    _validate_arg_in_signature(cast(Callable, obj), arg_name)


def _normalize_compose_artifact_policy(
    *,
    arg_name: str,
    mode: ComposeArtifactMode,
    approval: ComposeArtifactApproval,
) -> dict[str, str]:
    if mode not in {"forbid", "allow", "require"}:
        raise ValueError(f"Unsupported compose_artifact mode: {mode}")
    if approval not in {"none", "explicit"}:
        raise ValueError(f"Unsupported compose_artifact approval: {approval}")
    return {
        "arg_name": arg_name,
        "policy": "compose_artifact",
        "mode": mode,
        "approval": approval,
    }


def _attach_dependency(func: Callable, dependency: BaseDependency) -> None:
    """Attach dependency to function, handling both pre and post toolify scenarios."""
    func_with_attrs = cast(Any, func)

    # Attach to _dependencies for local discovery
    deps = list(getattr(func_with_attrs, "_dependencies", []))
    deps.append(dependency)
    func_with_attrs._dependencies = deps

    # Try to register with tool if toolify was applied first
    register_fn = getattr(func_with_attrs, "__maivn_register_dependency__", None)
    if callable(register_fn):
        try:
            register_fn(dependency)
        except Exception:
            # Expected: registration may fail if tool not yet initialized
            pass
    else:
        # Store as pending for toolify to pick up later
        pending = list(getattr(func_with_attrs, "__maivn_pending_deps__", []))
        pending.append(dependency)
        func_with_attrs.__maivn_pending_deps__ = pending


def _attach_execution_control(
    func: Callable,
    control: AwaitForDependency | ReevaluateDependency,
) -> None:
    register_team_control = getattr(func, "_add_team_execution_control", None)
    if callable(register_team_control):
        register_team_control(control)
        return

    func_with_attrs = cast(Any, func)

    controls = list(getattr(func_with_attrs, "__maivn_execution_controls__", []))
    controls.append(control)
    func_with_attrs.__maivn_execution_controls__ = controls

    register_fn = getattr(func_with_attrs, "__maivn_register_execution_control__", None)
    if callable(register_fn):
        try:
            register_fn(control)
        except Exception:
            pass
    else:
        pending = list(getattr(func_with_attrs, "__maivn_pending_execution_controls__", []))
        pending.append(control)
        func_with_attrs.__maivn_pending_execution_controls__ = pending


def _attach_arg_policy(obj: Callable | type[BaseModel], policy: dict[str, str]) -> None:
    obj_with_attrs = cast(Any, obj)

    policies = list(getattr(obj_with_attrs, "__maivn_arg_policies__", []))
    policies.append(policy)
    obj_with_attrs.__maivn_arg_policies__ = policies

    register_fn = getattr(obj_with_attrs, "__maivn_register_arg_policy__", None)
    if callable(register_fn):
        try:
            register_fn(policy)
        except Exception:
            pass
    else:
        pending = list(getattr(obj_with_attrs, "__maivn_pending_arg_policies__", []))
        pending.append(policy)
        obj_with_attrs.__maivn_pending_arg_policies__ = pending


def _attach_team_dependency_if_supported(
    target: Any,
    dependency_model: type[DependencyT],
    arg_name: str,
    model_kwargs: dict[str, Any],
) -> bool:
    register_team_dependency = getattr(target, "_add_team_dependency", None)
    if not callable(register_team_dependency):
        return False

    dependency = dependency_model(arg_name=arg_name, **model_kwargs)
    register_team_dependency(dependency)
    return True


def _attach_interrupt_team_dependency_if_supported(
    target: Any,
    *,
    arg_name: str,
    input_handler: Callable[[str], Any],
    prompt: str,
    input_type: InputType,
    choices: list[str],
) -> bool:
    register_team_dependency = getattr(target, "_add_team_dependency", None)
    if not callable(register_team_dependency):
        return False

    dependency = InterruptDependency(
        arg_name=arg_name,
        prompt=prompt,
        input_handler=input_handler,
        input_type=input_type,
        choices=choices,
    )
    register_team_dependency(dependency)
    return True


def _should_store_pending_team_dependency(target: Any, arg_name: str) -> bool:
    if not callable(target):
        return False
    try:
        signature = inspect.signature(target)
    except (TypeError, ValueError):
        return False
    if arg_name in signature.parameters:
        return False
    return _returns_agent(target, signature)


def _returns_agent(target: Callable, signature: inspect.Signature) -> bool:
    annotation = signature.return_annotation
    if annotation == inspect.Signature.empty:
        return False
    if isinstance(annotation, str):
        return annotation == "Agent" or annotation.endswith(".Agent")
    return getattr(annotation, "__name__", None) == "Agent"


def _resolve_tool_reference(tool_ref: str | BaseTool | Callable) -> tuple[str, str]:
    if callable(tool_ref):
        tool_id = getattr(tool_ref, "tool_id", None) or create_uuid(tool_ref)
        tool_name = getattr(tool_ref, "name", None) or getattr(tool_ref, "__name__", str(tool_ref))
        return str(tool_id), str(tool_name)

    tool_id = getattr(tool_ref, "tool_id", str(tool_ref))
    tool_name = getattr(tool_ref, "name", None) or str(tool_ref)
    return str(tool_id), str(tool_name)


__all__ = [
    "compose_artifact_policy",
    "depends_on_agent",
    "depends_on_await_for",
    "depends_on_private_data",
    "depends_on_interrupt",
    "depends_on_reevaluate",
    "depends_on_tool",
]
