# ruff: noqa: PLR0913 - the wide signatures here are public SDK entry points; collapsing them into a config object would break callers.
"""V1-compatible SDK decorators backed by v2 metadata records."""

from __future__ import annotations

import re
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from inspect import signature
from typing import Literal, TypeAlias, TypeVar, cast, get_args, get_origin, get_type_hints, overload

from maivn._internal.compat.interrupts import is_agent_generated_prompt

TOOL_DEPENDENCIES_ATTR = '__maivn_tool_dependencies__'
ARGUMENT_PRODUCER_REQUIREMENTS_ATTR = '__maivn_argument_producer_requirements__'
COMPOSE_ARGUMENT_POLICIES_ATTR = '__maivn_compose_argument_policies__'
PRIVATE_DATA_DEPENDENCIES_ATTR = '__maivn_private_data_dependencies__'
INTERRUPT_DEPENDENCIES_ATTR = '__maivn_interrupt_dependencies__'
EXECUTION_CONTROLS_ATTR = '__maivn_execution_controls__'
OUTPUT_SCHEMA_ATTR = '__maivn_output_schema__'
TOOLIFY_ATTR = '__maivn_toolify__'
TOOLSET_ATTR = '__maivn_toolset__'

F = TypeVar('F', bound=Callable[..., object])
TClass = TypeVar('TClass', bound=type[object])
TToolifyTarget = TypeVar('TToolifyTarget', bound=Callable[..., object] | type[object])
ToolReference: TypeAlias = str | Callable[..., object] | type[object]
InputHandler: TypeAlias = Callable[[str], object]
InputType: TypeAlias = Literal['text', 'choice', 'boolean', 'number']
ExecutionTiming: TypeAlias = Literal['before', 'after']
ExecutionInstanceControl: TypeAlias = Literal['each', 'first', 'last', 'all']
ReferenceKind: TypeAlias = Literal['composition_reference']
ComposeArgumentMode: TypeAlias = Literal['allow', 'require', 'forbid']
ComposeArgumentApproval: TypeAlias = Literal['none', 'explicit']

_ARGUMENT_PATH_PATTERN = re.compile(r'^[A-Za-z_][A-Za-z0-9_]*(?:\.[A-Za-z_][A-Za-z0-9_]*)*$')
_TOOL_NAME_PATTERN = re.compile(r'^[A-Za-z_][A-Za-z0-9_-]*$')


@dataclass(frozen=True)
class ToolDependency:
    """Dependency on another tool output."""

    arg_name: str
    tool_id: str
    tool_name: str
    revision_policy: Literal['on_validation_error'] | None = None
    result_scope: Literal['invocation', 'conversation'] = 'invocation'


@dataclass(frozen=True)
class ArgumentProducerRequirement:
    """A tool argument path that accepts only a producer-issued reference."""

    argument_path: str
    producer_tool_name: str
    reference_kind: ReferenceKind = 'composition_reference'


@dataclass(frozen=True)
class ComposeArgumentPolicy:
    """Composition policy for one native argument on an SDK tool."""

    argument_path: str
    mode: ComposeArgumentMode = 'allow'
    approval: ComposeArgumentApproval = 'none'


@dataclass(frozen=True)
class AgentDependency:
    """Dependency on another agent output."""

    arg_name: str
    agent_id: str
    agent_ref: object | None = None


@dataclass(frozen=True)
class PrivateDataDependency:
    """Dependency on caller-provided private data."""

    arg_name: str
    data_key: str


@dataclass(frozen=True)
class InterruptDependency:
    """Approval/user-input dependency metadata for v2 interrupt handling."""

    arg_name: str
    input_handler: InputHandler
    prompt: str = ''
    input_type: InputType = 'text'
    choices: tuple[str, ...] = ()

    @property
    def prompt_source(self) -> Literal['authored', 'agent_generated', 'fallback']:
        """Classify prompt ownership without allowing equal strings to spoof the sentinel."""
        if is_agent_generated_prompt(self.prompt):
            return 'agent_generated'
        return 'authored' if self.prompt else 'fallback'


@dataclass(frozen=True)
class ExecutionControl:
    """Relative execution-control metadata for tool dependencies."""

    kind: Literal['await_for', 'reevaluate']
    tool_id: str
    tool_name: str
    timing: ExecutionTiming = 'after'
    instance_control: ExecutionInstanceControl = 'each'


@dataclass(frozen=True)
class MethodToolifyOptions:
    """Method-level tool registration options."""

    name: str | None = None
    description: str | None = None
    permissions: object | None = None
    destructive: bool = False
    always_execute: bool = False
    final_tool: bool = False
    metadata: dict[str, object] = field(default_factory=dict)
    tags: tuple[str, ...] = ()
    before_execute: Callable[..., object] | None = None
    after_execute: Callable[..., object] | None = None


@dataclass(frozen=True)
class ToolsetOptions:
    """Class-level toolset registration options."""

    prefix: str | None = None
    tags: tuple[str, ...] = ()
    require_marker: bool = True
    metadata: dict[str, object] = field(default_factory=dict)


def depends_on_agent(agent_ref: object, arg_name: str) -> Callable[[F], F]:
    """Declare a dependency on another agent."""
    agent_id = _reference_id(agent_ref, 'agent_id')

    def decorator(func: F) -> F:
        dependency = AgentDependency(
            arg_name=arg_name,
            agent_id=agent_id,
            agent_ref=agent_ref,
        )
        _append_metadata(func, TOOL_DEPENDENCIES_ATTR, dependency)
        return func

    return decorator


def depends_on_tool(
    tool_ref: ToolReference,
    arg_name: str,
    *,
    revision_policy: Literal['on_validation_error'] | None = None,
    result_scope: Literal['invocation', 'conversation'] = 'invocation',
) -> Callable[[F], F]:
    """Declare a dependency on another tool.

    ``on_validation_error`` explicitly permits reconstructing this producer and
    consumer relationship after a dependent validation fails. Both must be SDK
    Pydantic constructors whose validators and hooks are safe to execute again.
    Revisions belong to that failed consumer; other consumers keep their original
    results. The default never authorizes reconstruction.

    ``result_scope='conversation'`` permits an unchanged producer result from an
    earlier turn in the same thread. A newly executed producer takes precedence.
    Keep the default invocation scope for confirmations and freshness-sensitive data.
    """
    if result_scope not in ('invocation', 'conversation'):
        message = f'unsupported dependency result scope: {result_scope}'
        raise ValueError(message)
    tool_id, tool_name = _tool_reference(tool_ref)

    def decorator(func: F) -> F:
        _append_metadata(
            func,
            TOOL_DEPENDENCIES_ATTR,
            ToolDependency(arg_name, tool_id, tool_name, revision_policy, result_scope),
        )
        return func

    return decorator


def requires_producer_reference(
    argument_path: str,
    *,
    producer_tool_name: str,
    reference_kind: ReferenceKind = 'composition_reference',
) -> Callable[[F], F]:
    """Declare that one argument path requires a named producer's reference.

    This is planning metadata, not whole-result injection. The consumer remains
    responsible for fail-closed nominal reference validation at call time.
    """
    if _ARGUMENT_PATH_PATTERN.fullmatch(argument_path) is None:
        message = f'invalid argument producer path: {argument_path}'
        raise ValueError(message)
    if _TOOL_NAME_PATTERN.fullmatch(producer_tool_name) is None:
        message = f'invalid argument producer tool name: {producer_tool_name}'
        raise ValueError(message)

    def decorator(func: F) -> F:
        current = getattr(func, ARGUMENT_PRODUCER_REQUIREMENTS_ATTR, None)
        requirements = cast('list[ArgumentProducerRequirement]', current or [])
        if any(item.argument_path == argument_path for item in requirements):
            message = f'duplicate argument producer requirement for {argument_path}'
            raise ValueError(message)
        _append_metadata(
            func,
            ARGUMENT_PRODUCER_REQUIREMENTS_ATTR,
            ArgumentProducerRequirement(argument_path, producer_tool_name, reference_kind),
        )
        return func

    return decorator


def compose_argument_policy(
    arg_name: str,
    *,
    mode: ComposeArgumentMode = 'allow',
    approval: ComposeArgumentApproval = 'none',
) -> Callable[[F], F]:
    """Control high-cognition composition for one downstream tool argument."""
    if _ARGUMENT_PATH_PATTERN.fullmatch(arg_name) is None or '.' in arg_name:
        message = f'invalid compose_argument target: {arg_name}'
        raise ValueError(message)
    if mode not in ('allow', 'require', 'forbid'):
        message = f'unsupported compose_argument mode: {mode}'
        raise ValueError(message)
    if approval not in ('none', 'explicit'):
        message = f'unsupported compose_argument approval: {approval}'
        raise ValueError(message)

    def decorator(func: F) -> F:
        if arg_name not in signature(func).parameters:
            message = f"compose_argument_policy target '{arg_name}' is not a function argument"
            raise ValueError(message)
        current = cast(
            'list[ComposeArgumentPolicy]',
            getattr(func, COMPOSE_ARGUMENT_POLICIES_ATTR, None) or [],
        )
        if any(item.argument_path == arg_name for item in current):
            message = f'duplicate compose_argument policy for {arg_name}'
            raise ValueError(message)
        _append_metadata(
            func,
            COMPOSE_ARGUMENT_POLICIES_ATTR,
            ComposeArgumentPolicy(arg_name, mode, approval),
        )
        if mode == 'require':
            _append_metadata(
                func,
                ARGUMENT_PRODUCER_REQUIREMENTS_ATTR,
                ArgumentProducerRequirement(arg_name, 'compose_argument'),
            )
        return func

    return decorator


def depends_on_private_data(data_key: str, arg_name: str) -> Callable[[F], F]:
    """Declare a dependency on external private data."""

    def decorator(func: F) -> F:
        dependency = PrivateDataDependency(arg_name=arg_name, data_key=data_key)
        _append_metadata(func, PRIVATE_DATA_DEPENDENCIES_ATTR, dependency)
        from maivn._internal.private_model_inputs import (  # noqa: PLC0415 - depends on metadata types defined here.
            install_private_model_validation,
        )

        install_private_model_validation(func)
        return func

    return decorator


def depends_on_interrupt(
    arg_name: str,
    input_handler: InputHandler,
    prompt: str = '',
    input_type: InputType | None = None,
    choices: list[str] | None = None,
) -> Callable[[F], F]:
    """Declare a dependency that pauses for user input or approval."""

    def decorator(func: F) -> F:
        inferred_type, inferred_choices = _detect_input_type_from_annotation(func, arg_name)
        dependency = InterruptDependency(
            arg_name=arg_name,
            input_handler=input_handler,
            prompt=prompt,
            input_type=input_type or inferred_type,
            choices=tuple(choices) if choices is not None else inferred_choices,
        )
        _append_metadata(func, INTERRUPT_DEPENDENCIES_ATTR, dependency)
        return func

    return decorator


def depends_on_await_for(
    tool_ref: ToolReference,
    *,
    timing: ExecutionTiming = 'after',
    instance_control: ExecutionInstanceControl = 'each',
) -> Callable[[F], F]:
    """Block the decorated tool until the referenced tool finishes."""
    return _execution_control_decorator(
        'await_for',
        tool_ref,
        timing=timing,
        instance_control=instance_control,
    )


def depends_on_reevaluate(
    tool_ref: ToolReference,
    *,
    timing: ExecutionTiming = 'after',
    instance_control: ExecutionInstanceControl = 'each',
) -> Callable[[F], F]:
    """Trigger replanning relative to the referenced tool."""
    return _execution_control_decorator(
        'reevaluate',
        tool_ref,
        timing=timing,
        instance_control=instance_control,
    )


def tool_output(schema: object) -> Callable[[F], F]:
    """Declare the output schema for a function or method tool."""

    def decorator(func: F) -> F:
        setattr(func, OUTPUT_SCHEMA_ATTR, schema)
        return func

    return decorator


@overload
def toolify(func: TToolifyTarget) -> TToolifyTarget: ...


@overload
def toolify(
    func: None = None,
    *,
    name: str | None = None,
    description: str | None = None,
    permissions: object | None = None,
    destructive: bool = False,
    always_execute: bool = False,
    final_tool: bool = False,
    metadata: Mapping[str, object] | None = None,
    tags: list[str] | tuple[str, ...] | None = None,
    before_execute: Callable[..., object] | None = None,
    after_execute: Callable[..., object] | None = None,
) -> Callable[[TToolifyTarget], TToolifyTarget]: ...


def toolify(
    func: TToolifyTarget | None = None,
    *,
    name: str | None = None,
    description: str | None = None,
    permissions: object | None = None,
    destructive: bool = False,
    always_execute: bool = False,
    final_tool: bool = False,
    metadata: Mapping[str, object] | None = None,
    tags: list[str] | tuple[str, ...] | None = None,
    before_execute: Callable[..., object] | None = None,
    after_execute: Callable[..., object] | None = None,
) -> TToolifyTarget | Callable[[TToolifyTarget], TToolifyTarget]:
    """Mark a function or method as a v2-registerable tool."""
    options = MethodToolifyOptions(
        name=name,
        description=description,
        permissions=permissions,
        destructive=destructive,
        always_execute=always_execute,
        final_tool=final_tool,
        metadata=dict(metadata or {}),
        tags=tuple(tags or ()),
        before_execute=before_execute,
        after_execute=after_execute,
    )

    def apply(target: TToolifyTarget) -> TToolifyTarget:
        setattr(target, TOOLIFY_ATTR, options)
        return target

    if func is None:
        return apply
    return apply(func)


@overload
def toolset(cls: TClass) -> TClass: ...


@overload
def toolset(
    cls: None = None,
    *,
    prefix: str | None = None,
    tags: list[str] | tuple[str, ...] | None = None,
    require_marker: bool = True,
    metadata: Mapping[str, object] | None = None,
) -> Callable[[TClass], TClass]: ...


def toolset(
    cls: TClass | None = None,
    *,
    prefix: str | None = None,
    tags: list[str] | tuple[str, ...] | None = None,
    require_marker: bool = True,
    metadata: Mapping[str, object] | None = None,
) -> TClass | Callable[[TClass], TClass]:
    """Mark a class as a method toolset."""
    options = ToolsetOptions(
        prefix=prefix,
        tags=tuple(tags or ()),
        require_marker=require_marker,
        metadata=dict(metadata or {}),
    )

    def apply(target: TClass) -> TClass:
        setattr(target, TOOLSET_ATTR, options)
        return target

    if cls is None:
        return apply
    return apply(cls)


def _execution_control_decorator(
    kind: Literal['await_for', 'reevaluate'],
    tool_ref: ToolReference,
    *,
    timing: ExecutionTiming,
    instance_control: ExecutionInstanceControl,
) -> Callable[[F], F]:
    tool_id, tool_name = _tool_reference(tool_ref)

    def decorator(func: F) -> F:
        control = ExecutionControl(
            kind=kind,
            tool_id=tool_id,
            tool_name=tool_name,
            timing=timing,
            instance_control=instance_control,
        )
        _append_metadata(func, EXECUTION_CONTROLS_ATTR, control)
        return func

    return decorator


def _detect_input_type_from_annotation(
    func: Callable[..., object], arg_name: str
) -> tuple[
    InputType,
    tuple[str, ...],
]:
    try:
        hints = get_type_hints(func)
    except (NameError, TypeError, AttributeError):
        return 'text', ()
    annotation = hints.get(arg_name)
    if annotation is None:
        return 'text', ()
    origin = get_origin(annotation)
    if origin is Literal:
        choices = tuple(str(value) for value in get_args(annotation))
        return 'choice', choices
    if annotation is bool:
        return 'boolean', ()
    if annotation in (int, float):
        return 'number', ()
    return 'text', ()


def _tool_reference(tool_ref: ToolReference) -> tuple[str, str]:
    if isinstance(tool_ref, str):
        return tool_ref, tool_ref
    name = getattr(tool_ref, '__name__', type(tool_ref).__name__)
    tool_id = getattr(tool_ref, 'tool_id', name)
    return str(tool_id), str(name)


def _reference_id(ref: object, attr: str) -> str:
    for candidate in (attr, 'id', 'name'):
        value = getattr(ref, candidate, None)
        if isinstance(value, str) and value:
            return value
    return str(ref)


def _append_metadata(func: object, attr: str, value: object) -> None:
    current = getattr(func, attr, None)
    # A subclass inherits the metadata value, not ownership of its parent's list.
    values = list(cast('list[object]', current)) if current is not None else []
    values.append(value)
    setattr(func, attr, values)


__all__ = [
    'ARGUMENT_PRODUCER_REQUIREMENTS_ATTR',
    'COMPOSE_ARGUMENT_POLICIES_ATTR',
    'EXECUTION_CONTROLS_ATTR',
    'INTERRUPT_DEPENDENCIES_ATTR',
    'OUTPUT_SCHEMA_ATTR',
    'PRIVATE_DATA_DEPENDENCIES_ATTR',
    'TOOLIFY_ATTR',
    'TOOLSET_ATTR',
    'TOOL_DEPENDENCIES_ATTR',
    'AgentDependency',
    'ArgumentProducerRequirement',
    'ComposeArgumentApproval',
    'ComposeArgumentMode',
    'ComposeArgumentPolicy',
    'ExecutionControl',
    'ExecutionInstanceControl',
    'ExecutionTiming',
    'InputHandler',
    'InputType',
    'InterruptDependency',
    'MethodToolifyOptions',
    'PrivateDataDependency',
    'ReferenceKind',
    'ToolDependency',
    'ToolReference',
    'ToolsetOptions',
    'compose_argument_policy',
    'depends_on_agent',
    'depends_on_await_for',
    'depends_on_interrupt',
    'depends_on_private_data',
    'depends_on_reevaluate',
    'depends_on_tool',
    'requires_producer_reference',
    'tool_output',
    'toolify',
    'toolset',
]
