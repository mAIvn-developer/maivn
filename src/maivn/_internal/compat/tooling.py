# ruff: noqa: PLR0913 - the wide signatures here are public SDK entry points; collapsing them into a config object would break callers.
"""V1-compatible toolset registration for public scopes."""

from __future__ import annotations

import contextlib
import re
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from inspect import Parameter, Signature, getdoc, signature
from typing import (
    Literal,
    ParamSpec,
    Protocol,
    TypeAlias,
    TypeVar,
    cast,
    get_args,
    get_type_hints,
    overload,
)

from maivn_contracts.artifacts import GeneratedFile, GeneratedFiles
from pydantic import BaseModel, TypeAdapter

from maivn._internal.compat.decorators import (
    EXECUTION_CONTROLS_ATTR,
    INTERRUPT_DEPENDENCIES_ATTR,
    OUTPUT_SCHEMA_ATTR,
    PRIVATE_DATA_DEPENDENCIES_ATTR,
    TOOL_DEPENDENCIES_ATTR,
    TOOLIFY_ATTR,
    TOOLSET_ATTR,
    ExecutionControl,
    ExecutionInstanceControl,
    ExecutionTiming,
    InputHandler,
    InputType,
    MethodToolifyOptions,
    ToolDependency,
    ToolReference,
    ToolsetOptions,
    depends_on_agent,
    depends_on_await_for,
    depends_on_interrupt,
    depends_on_private_data,
    depends_on_reevaluate,
    depends_on_tool,
)
from maivn._internal.compat.permissions import DESTRUCTIVE_FLAG_NAMES
from maivn._internal.models import JsonObject, ToolMetadata
from maivn._internal.private_model_inputs import PrivateInputJsonSchema
from maivn._internal.schema_references import prune_unused_definitions

_SNAKE_CASE_ACRONYM = re.compile(r'([A-Z]{2,})([A-Z][a-z])')
_SNAKE_CASE_WORD_BOUNDARY = re.compile(r'([a-z0-9])([A-Z])')

P = ParamSpec('P')
R = TypeVar('R')
TModel = TypeVar('TModel', bound=BaseModel)
ToolTarget: TypeAlias = Callable[..., object] | type[BaseModel]
ToolDecorator: TypeAlias = Callable[[ToolTarget], ToolTarget]


class _ToolRegistrationScope(Protocol):
    """Scope surface required by the fluent toolify builder."""

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
    ) -> ToolTarget: ...


@dataclass(frozen=True, slots=True)
class ToolRegistrationOptions:
    """Common v1 tool registration options carried by Agent and Swarm."""

    name: str | None = None
    description: str | None = None
    permissions: object | None = None
    destructive: bool = False
    always_execute: bool = False
    final_tool: bool = False
    metadata: Mapping[str, object] | None = None
    tags: list[str] | tuple[str, ...] | None = None
    before_execute: Callable[..., object] | None = None
    after_execute: Callable[..., object] | None = None


class ScopeToolifyBuilder:
    """Callable decorator with v1 fluent dependency configuration methods."""

    def __init__(self, scope: object, options: ToolRegistrationOptions) -> None:
        """Create a builder bound to one Agent or Swarm-like scope."""
        self._scope = scope
        self._options = options
        self._decorators: list[ToolDecorator] = []

    def depends_on_agent(self, agent_ref: object, arg_name: str) -> ScopeToolifyBuilder:
        """Declare a dependency on another agent."""
        self._decorators.append(cast('ToolDecorator', depends_on_agent(agent_ref, arg_name)))
        return self

    def depends_on_tool(
        self,
        tool_ref: ToolReference,
        arg_name: str,
        *,
        revision_policy: Literal['on_validation_error'] | None = None,
    ) -> ScopeToolifyBuilder:
        """Declare a dependency on another tool output."""
        self._decorators.append(
            cast(
                'ToolDecorator',
                depends_on_tool(tool_ref, arg_name, revision_policy=revision_policy),
            )
        )
        return self

    def depends_on_private_data(
        self,
        data_key: str,
        arg_name: str,
    ) -> ScopeToolifyBuilder:
        """Declare a dependency on caller-provided private data."""
        self._decorators.append(cast('ToolDecorator', depends_on_private_data(data_key, arg_name)))
        return self

    def depends_on_await_for(
        self,
        tool_ref: ToolReference,
        *,
        timing: ExecutionTiming = 'after',
        instance_control: ExecutionInstanceControl = 'each',
    ) -> ScopeToolifyBuilder:
        """Block the decorated tool until the referenced tool finishes."""
        self._decorators.append(
            cast(
                'ToolDecorator',
                depends_on_await_for(
                    tool_ref,
                    timing=timing,
                    instance_control=instance_control,
                ),
            )
        )
        return self

    def depends_on_reevaluate(
        self,
        tool_ref: ToolReference,
        *,
        timing: ExecutionTiming = 'after',
        instance_control: ExecutionInstanceControl = 'each',
    ) -> ScopeToolifyBuilder:
        """Trigger replanning relative to the referenced tool."""
        self._decorators.append(
            cast(
                'ToolDecorator',
                depends_on_reevaluate(
                    tool_ref,
                    timing=timing,
                    instance_control=instance_control,
                ),
            )
        )
        return self

    def depends_on_interrupt(
        self,
        arg_name: str,
        prompt: str,
        input_handler: InputHandler,
        *,
        input_type: InputType | None = None,
        choices: list[str] | None = None,
    ) -> ScopeToolifyBuilder:
        """Declare a dependency that pauses for user input or approval."""
        self._decorators.append(
            cast(
                'ToolDecorator',
                depends_on_interrupt(
                    arg_name,
                    input_handler,
                    prompt=prompt,
                    input_type=input_type,
                    choices=choices,
                ),
            )
        )
        return self

    @overload
    def __call__(self, target: type[TModel]) -> type[TModel]: ...

    @overload
    def __call__(self, target: Callable[P, R]) -> Callable[P, R]: ...

    def __call__(self, target: Callable[P, R] | type[TModel]) -> Callable[P, R] | type[TModel]:
        """Apply dependency metadata decorators, register the tool, and return it."""
        decorated: ToolTarget = target
        for decorator in self._decorators:
            decorated = decorator(decorated)
        _copy_builder_metadata(self, decorated)
        registered = cast('Callable[P, R] | type[TModel]', decorated)
        _register_tool_on_scope(self._scope, registered, self._options)
        return registered


class ToolingScopeMixin:
    """Common v1 tool registration helpers shared by Agent and Swarm."""

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
        """Register local callable metadata for scopes without a native add_tool."""
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
        _scope_tools(self).append(metadata_handle)
        assign_tool_id(tool, metadata_handle.name)
        _set_tools_dirty(self)
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

    def add_toolset(
        self,
        connector: object,
        *,
        include: list[str] | tuple[str, ...] | None = None,
        exclude: list[str] | tuple[str, ...] | None = None,
        include_tags: list[str] | tuple[str, ...] | None = None,
        exclude_tags: list[str] | tuple[str, ...] | None = None,
    ) -> list[ToolMetadata]:
        """Register decorated public methods on ``connector`` as scope tools."""
        cls = type(connector)
        toolset_options = _toolset_options(cls)
        if toolset_options is None:
            message = (
                f'{cls.__name__} is not a toolset. Decorate the class with '
                '@maivn.toolset(...) before passing instances to add_toolset().'
            )
            raise TypeError(message)

        prefix = toolset_options.prefix or _derive_default_prefix(cls)
        include_set = frozenset(include) if include is not None else None
        exclude_set = frozenset(exclude) if exclude is not None else None
        include_tag_set = frozenset(include_tags) if include_tags is not None else None
        exclude_tag_set = frozenset(exclude_tags) if exclude_tags is not None else None

        registered: list[ToolMetadata] = []
        seen_names: set[str] = set()
        for method_name, bound_method, method_options in _discover_toolset_methods(
            connector,
            cls,
            toolset_options,
        ):
            base_name = method_options.name or method_name
            # Prefix with an underscore separator, not a dotted namespace, so the
            # resulting tool name stays inside provider tool-name regexes.
            full_name = f'{prefix.upper()}_{base_name}' if prefix else base_name

            if not _passes_toolset_filter(
                base_name=base_name,
                method_options=method_options,
                toolset_options=toolset_options,
                include=include_set,
                exclude=exclude_set,
                include_tags=include_tag_set,
                exclude_tags=exclude_tag_set,
            ):
                continue

            if full_name in seen_names:
                message = (
                    f'Toolset {cls.__name__!r} produced a duplicate tool name {full_name!r}; '
                    'rename the method or pass an explicit name= to @toolify.'
                )
                raise ValueError(message)
            seen_names.add(full_name)

            registered.append(
                _register_toolset_method(
                    self,
                    full_name=full_name,
                    bound_method=bound_method,
                    method_options=method_options,
                    toolset_options=toolset_options,
                )
            )

        if not registered and toolset_options.require_marker:
            filter_active = any(
                item is not None
                for item in (include_set, exclude_set, include_tag_set, exclude_tag_set)
            )
            hint = (
                'All matching methods were filtered out by include/exclude rules; '
                'loosen the filter or set @toolset(require_marker=False).'
                if filter_active
                else 'Decorate methods with @maivn.toolify or set '
                '@toolset(require_marker=False) for grandfathered classes.'
            )
            message = f'Toolset {cls.__name__!r} has no @toolify-marked methods to register. {hint}'
            raise ValueError(message)

        return registered

    def list_tools(self) -> list[ToolMetadata]:
        """Return registered local tool handles for this scope."""
        return list(_scope_tools(self))


def toolify_builder_for_scope(
    scope: object,
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
) -> ScopeToolifyBuilder:
    """Create a fluent toolify decorator builder for a scope."""
    return ScopeToolifyBuilder(
        scope,
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


def _register_tool_on_scope(
    scope: object,
    tool: ToolTarget,
    options: ToolRegistrationOptions,
) -> None:
    typed_scope = cast('_ToolRegistrationScope', scope)
    _ = typed_scope.add_tool(
        tool,
        name=options.name,
        description=options.description,
        permissions=options.permissions,
        destructive=options.destructive,
        always_execute=options.always_execute,
        final_tool=options.final_tool,
        metadata=options.metadata,
        tags=options.tags,
        before_execute=options.before_execute,
        after_execute=options.after_execute,
    )


def assign_tool_id(tool: object, tool_id: str) -> None:
    """Best-effort v1 tool_id attachment; dynamic targets may reject assignment."""
    with contextlib.suppress(AttributeError, TypeError):
        setattr(tool, 'tool_id', tool_id)  # noqa: B010 - v1 tool handles carry tool_id.


def tool_metadata_for_callable(
    tool: ToolTarget,
    options: ToolRegistrationOptions,
) -> ToolMetadata:
    """Build a ToolMetadata handle from v1 registration options."""
    name = options.name or _callable_name(tool)
    return ToolMetadata(
        name=name,
        description=options.description or getdoc(tool) or f'Execute {name}.',
        always_execute=options.always_execute,
        final_tool=options.final_tool,
        input_schema=_input_schema_for(tool),
        metadata=_metadata_for_registration(options),
        tags=_tags_for_registration(options),
        target=tool,
        before_execute=options.before_execute,
        after_execute=options.after_execute,
    )


def compile_tool_metadata(tool: ToolMetadata) -> ToolMetadata:
    """Refresh schema metadata after every decorator has finished applying."""
    metadata = dict(tool.metadata)
    execution_controls = _execution_controls_for(tool.target)
    if execution_controls:
        metadata['execution_controls'] = execution_controls
    return tool.model_copy(
        update={
            'input_schema': _input_schema_for(tool.target),
            'output_schema': _output_schema_for(tool.target),
            'metadata': metadata,
        }
    )


def compile_tool_metadata_graph(tools: Sequence[ToolMetadata]) -> list[ToolMetadata]:
    """Expand nested Pydantic models into executable dependency tools.

    Keep every nested model as a separate runtime unit, compile dependencies
    before their parents, and reuse explicitly registered metadata when a nested
    class was also toolified.
    """
    explicit_by_target = {id(tool.target): tool for tool in tools}
    explicit_by_name = {tool.name: tool for tool in tools}
    compiled: list[ToolMetadata] = []
    visited: set[int] = set()
    visiting: set[int] = set()
    direct_reference_counts = _direct_model_reference_counts(tools)

    def visit(tool: ToolMetadata) -> None:
        target_key = id(tool.target)
        if target_key in visited or target_key in visiting:
            return
        visiting.add(target_key)
        if _is_model_tool(tool.target):
            model = cast('type[BaseModel]', tool.target)
            for arg_name, nested_model, inject_directly in _nested_model_fields(model):
                dependency_tool = explicit_by_target.get(id(nested_model))
                if dependency_tool is None:
                    dependency_tool = tool_metadata_for_callable(
                        nested_model,
                        ToolRegistrationOptions(tags=tool.tags),
                    )
                    explicit_by_target[id(nested_model)] = dependency_tool
                    explicit_by_name[dependency_tool.name] = dependency_tool
                    assign_tool_id(nested_model, dependency_tool.name)
                # PARALLEL-BENCHMARK-NOTE(2026-08-16): Complex Types lost union
                # alternatives and injected one model into list/map fields. Register every
                # referenced model, but only a direct model-valued field is one dependency.
                if inject_directly and direct_reference_counts.get(id(nested_model)) == 1:
                    _append_model_dependency(model, arg_name, dependency_tool.name)
                visit(dependency_tool)
        for producer in _registered_tool_dependencies(tool, explicit_by_name):
            visit(producer)
        visiting.remove(target_key)
        visited.add(target_key)
        compiled.append(compile_tool_metadata(tool))

    for registered_tool in tools:
        visit(registered_tool)
    return compiled


def _registered_tool_dependencies(
    tool: ToolMetadata,
    tools_by_name: Mapping[str, ToolMetadata],
) -> list[ToolMetadata]:
    """Resolve the declared producers that participate in this compiled graph."""
    raw = getattr(tool.target, TOOL_DEPENDENCIES_ATTR, None)
    if not isinstance(raw, list):
        return []
    producers: list[ToolMetadata] = []
    for dependency in cast('list[object]', raw):
        if not isinstance(dependency, ToolDependency):
            continue
        producer = tools_by_name.get(dependency.tool_id) or tools_by_name.get(dependency.tool_name)
        if producer is not None:
            producers.append(producer)
    return producers


def _direct_model_reference_counts(tools: Sequence[ToolMetadata]) -> dict[int, int]:
    """Count direct model-valued fields across the reachable schema graph.

    The current runtime owns one dependency result per tool name. A model type used by
    several direct fields therefore cannot be inferred as a dependency: those fields
    represent distinct values even though they share one Pydantic class.
    """
    counts: dict[int, int] = {}
    visited: set[int] = set()
    pending = [tool.target for tool in tools if _is_model_tool(tool.target)]
    while pending:
        model = cast('type[BaseModel]', pending.pop())
        model_key = id(model)
        if model_key in visited:
            continue
        visited.add(model_key)
        for _arg_name, nested_model, inject_directly in _nested_model_fields(model):
            if inject_directly:
                nested_key = id(nested_model)
                counts[nested_key] = counts.get(nested_key, 0) + 1
            pending.append(nested_model)
    return counts


def _is_model_tool(target: object) -> bool:
    return isinstance(target, type) and issubclass(target, BaseModel)


def _nested_model_fields(model: type[BaseModel]) -> list[tuple[str, type[BaseModel], bool]]:
    nested: list[tuple[str, type[BaseModel], bool]] = []
    for arg_name, field_info in model.model_fields.items():
        candidates = _models_in_annotation(field_info.annotation)
        direct_model = (
            cast('type[BaseModel]', field_info.annotation)
            if _is_model_tool(field_info.annotation)
            else None
        )
        nested.extend((arg_name, candidate, candidate is direct_model) for candidate in candidates)
    return nested


def _models_in_annotation(annotation: object) -> list[type[BaseModel]]:
    if _is_model_tool(annotation):
        return [cast('type[BaseModel]', annotation)]
    found: list[type[BaseModel]] = []
    for argument in get_args(annotation):
        for model in _models_in_annotation(argument):
            if model not in found:
                found.append(model)
    return found


def _append_model_dependency(target: type[BaseModel], arg_name: str, dependency_name: str) -> None:
    raw = getattr(target, TOOL_DEPENDENCIES_ATTR, None)
    if raw is None:
        dependencies: list[object] = []
        setattr(target, TOOL_DEPENDENCIES_ATTR, dependencies)
    elif isinstance(raw, list):
        dependencies = cast('list[object]', raw)
    else:
        message = f'{TOOL_DEPENDENCIES_ATTR} metadata must be a list'
        raise TypeError(message)
    if any(isinstance(item, ToolDependency) and item.arg_name == arg_name for item in dependencies):
        return
    dependencies.append(ToolDependency(arg_name, dependency_name, dependency_name))


def _scope_tools(scope: object) -> list[ToolMetadata]:
    tools = getattr(scope, 'tools', None)
    if tools is None:
        new_tools: list[ToolMetadata] = []
        setattr(scope, 'tools', new_tools)  # noqa: B010 - dynamic scope registry.
        return new_tools
    if not isinstance(tools, list):
        message = f'scope tools registry must be a list, got {type(tools).__name__}'
        raise TypeError(message)
    return cast('list[ToolMetadata]', tools)


def _callable_name(tool: ToolTarget) -> str:
    return str(getattr(tool, '__name__', type(tool).__name__))


def _set_tools_dirty(scope: object) -> None:
    if hasattr(scope, '_compiled_tools_cache'):
        setattr(scope, '_compiled_tools_cache', None)  # noqa: B010 - dynamic scope cache shim.
    if hasattr(scope, '_tools_dirty'):
        setattr(scope, '_tools_dirty', True)  # noqa: B010 - dynamic scope cache shim.


def _toolset_options(cls: type[object]) -> ToolsetOptions | None:
    return cast('ToolsetOptions | None', getattr(cls, TOOLSET_ATTR, None))


def _toolify_options(method: Callable[..., object]) -> MethodToolifyOptions | None:
    return cast('MethodToolifyOptions | None', getattr(method, TOOLIFY_ATTR, None))


def toolset_options(cls: type[object]) -> ToolsetOptions | None:
    """Return the options ``@maivn.toolset`` recorded on ``cls``, or ``None``.

    Reading back what the decorators wrote is part of the toolset contract:
    a caller that publishes toolsets has to be able to ask whether a class is
    one, and under what prefix, without knowing the attribute name it is
    stored under.
    """
    return _toolset_options(cls)


def toolify_options(method: Callable[..., object]) -> MethodToolifyOptions | None:
    """Return the options ``@maivn.toolify`` recorded on ``method``, or ``None``."""
    return _toolify_options(method)


def _discover_toolset_methods(
    connector: object,
    cls: type[object],
    toolset_options: ToolsetOptions,
) -> list[tuple[str, Callable[..., object], MethodToolifyOptions]]:
    discovered: list[tuple[str, Callable[..., object], MethodToolifyOptions]] = []
    for attr_name in dir(cls):
        if attr_name.startswith('_'):
            continue
        class_attr = getattr(cls, attr_name, None)
        if isinstance(class_attr, type):
            continue
        if isinstance(class_attr, (property, classmethod, staticmethod)):
            continue
        if not callable(class_attr):
            continue

        bound = getattr(connector, attr_name, None)
        if not callable(bound):
            continue

        method_options = _toolify_options(bound)
        if method_options is None:
            if toolset_options.require_marker:
                continue
            method_options = MethodToolifyOptions()
        discovered.append((attr_name, bound, method_options))
    return discovered


def _passes_toolset_filter(
    *,
    base_name: str,
    method_options: MethodToolifyOptions,
    toolset_options: ToolsetOptions,
    include: frozenset[str] | None,
    exclude: frozenset[str] | None,
    include_tags: frozenset[str] | None,
    exclude_tags: frozenset[str] | None,
) -> bool:
    if include is not None and base_name not in include:
        return False
    if exclude is not None and base_name in exclude:
        return False
    if include_tags is None and exclude_tags is None:
        return True

    tags = _filter_tags(method_options, toolset_options)
    if include_tags is not None and not (include_tags & tags):
        return False
    return exclude_tags is None or not exclude_tags & tags


def _register_toolset_method(
    scope: object,
    *,
    full_name: str,
    bound_method: Callable[..., object],
    method_options: MethodToolifyOptions,
    toolset_options: ToolsetOptions,
) -> ToolMetadata:
    # `scope` is typed `object` because any scope-like host may register a tool,
    # so the attribute has to be reached dynamically rather than by name.
    add_tool = cast('Callable[..., object]', getattr(scope, 'add_tool'))  # noqa: B009
    tools = _scope_tools(scope)
    before_count = len(tools)
    _ = add_tool(
        bound_method,
        name=full_name,
        description=_description_for(bound_method, full_name, method_options),
        always_execute=method_options.always_execute,
        final_tool=method_options.final_tool,
        metadata=_metadata_for(method_options, toolset_options),
        tags=tuple(sorted(_filter_tags(method_options, toolset_options))),
        before_execute=method_options.before_execute,
        after_execute=method_options.after_execute,
    )
    tools = _scope_tools(scope)
    if len(tools) <= before_count:
        message = 'add_tool did not append a registered tool handle'
        raise RuntimeError(message)
    registered = tools[-1]
    enriched = registered.model_copy(
        update={
            'input_schema': _input_schema_for(bound_method),
            'output_schema': getattr(bound_method, OUTPUT_SCHEMA_ATTR, None),
            'metadata': _metadata_for(method_options, toolset_options),
            'tags': tuple(sorted(_filter_tags(method_options, toolset_options))),
        }
    )
    tools[-1] = enriched
    return enriched


def _description_for(
    method: Callable[..., object],
    full_name: str,
    method_options: MethodToolifyOptions,
) -> str:
    if method_options.description is not None:
        return method_options.description
    doc = getdoc(method)
    if doc:
        return doc
    return f'Invoke {full_name}.'


def _metadata_for(
    method_options: MethodToolifyOptions,
    toolset_options: ToolsetOptions,
) -> JsonObject:
    metadata: JsonObject = {
        **toolset_options.metadata,
        **method_options.metadata,
    }
    permission_names = _permission_name_list(method_options.permissions)
    if permission_names:
        metadata['permissions'] = permission_names
    if method_options.destructive:
        metadata['destructive'] = True
    return metadata


def _filter_tags(
    method_options: MethodToolifyOptions,
    toolset_options: ToolsetOptions,
) -> frozenset[str]:
    tags: set[str] = set(toolset_options.tags)
    tags.update(method_options.tags)

    permission_names = _permission_names(method_options.permissions)
    tags.update(permission_names)
    if method_options.destructive or permission_names & DESTRUCTIVE_FLAG_NAMES:
        tags.add('destructive')
    return frozenset(tags)


def _permission_names(permissions: object | None) -> frozenset[str]:
    return frozenset(_permission_name_list(permissions))


def _permission_name_list(permissions: object | None) -> list[str]:
    if permissions is None:
        return []
    to_list = getattr(permissions, 'to_list', None)
    if callable(to_list):
        values = cast('Callable[[], object]', to_list)()
        if isinstance(values, list):
            return [str(value).lower() for value in cast('list[object]', values)]
    if isinstance(permissions, str):
        return [permissions.lower()]
    return []


def _metadata_for_registration(options: ToolRegistrationOptions) -> JsonObject:
    metadata = dict(options.metadata or {})
    permission_names = _permission_name_list(options.permissions)
    if permission_names:
        metadata['permissions'] = permission_names
    if options.destructive:
        metadata['destructive'] = True
    return metadata


def _execution_controls_for(target: ToolTarget) -> JsonObject:
    """Serialize decorator controls into the invocation-local tool metadata contract."""
    raw_controls = getattr(target, EXECUTION_CONTROLS_ATTR, ())
    if not isinstance(raw_controls, list):
        return {}

    controls: dict[str, list[JsonObject]] = {}
    for control in cast('list[object]', raw_controls):
        if not isinstance(control, ExecutionControl):
            continue
        controls.setdefault(control.kind, []).append(
            {
                'tool_id': control.tool_id,
                'tool_name': control.tool_name,
                'timing': control.timing,
                'instance_control': control.instance_control,
            }
        )
    return controls


def _tags_for_registration(options: ToolRegistrationOptions) -> tuple[str, ...]:
    tags: list[str] = []
    if options.tags is not None:
        tags.extend(str(tag) for tag in options.tags)
    tags.extend(_permission_name_list(options.permissions))
    if options.destructive or _permission_names(options.permissions) & DESTRUCTIVE_FLAG_NAMES:
        tags.append('destructive')
    return tuple(dict.fromkeys(tag for tag in tags if tag))


def _derive_default_prefix(cls: type[object]) -> str:
    intermediate = _SNAKE_CASE_ACRONYM.sub(r'\1_\2', cls.__name__)
    return _SNAKE_CASE_WORD_BOUNDARY.sub(r'\1_\2', intermediate).lower()


def _input_schema_for(func: ToolTarget) -> JsonObject:
    if isinstance(func, type) and issubclass(func, BaseModel):
        schema = func.model_json_schema(schema_generator=PrivateInputJsonSchema)
        dependency_args = _dependency_arg_names(func)
        model_properties = schema.get('properties')
        if isinstance(model_properties, dict):
            typed_properties = cast('Mapping[str, object]', model_properties)
            schema['properties'] = {
                name: value
                for name, value in typed_properties.items()
                if name not in dependency_args
            }
        model_required = schema.get('required')
        if isinstance(model_required, list):
            schema['required'] = [
                name
                for name in cast('list[object]', model_required)
                if isinstance(name, str) and name not in dependency_args
            ]
        preserve_public_tool_dependency_guidance(func, schema)
        return prune_unused_definitions(schema)
    try:
        func_signature = signature(cast('Callable[..., object]', func))
    except (TypeError, ValueError):
        return {'type': 'object', 'properties': {}}

    callable_func = cast('Callable[..., object]', func)
    dependency_args = _dependency_arg_names(callable_func)
    resolved_hints = _resolved_type_hints(callable_func)
    properties: dict[str, object] = {}
    definition_adapters: dict[str, TypeAdapter[object]] = {}
    required: list[str] = []
    for name, parameter in func_signature.parameters.items():
        if (
            name == 'self'
            or name in dependency_args
            or parameter.kind in (Parameter.VAR_KEYWORD, Parameter.VAR_POSITIONAL)
        ):
            continue
        properties[name] = _schema_for_parameter(
            parameter,
            resolved_hints.get(name),
            definition_adapters=definition_adapters,
        )
        if parameter.default is Signature.empty:
            required.append(name)

    schema: JsonObject = {
        'type': 'object',
        'properties': properties,
        'additionalProperties': False,
    }
    if required:
        schema['required'] = required
    if definition_adapters:
        # Generate these parameters together: Pydantic resolves recursive links
        # and distinguishes different models that happen to share a name.
        # Individual parameter schemas use root refs, so nesting their $defs
        # beneath properties would leave those references dangling.
        parameter_schemas, definitions = TypeAdapter.json_schemas(
            [(name, 'validation', adapter) for name, adapter in definition_adapters.items()],
            schema_generator=PrivateInputJsonSchema,
        )
        properties.update({name: value for (name, _mode), value in parameter_schemas.items()})
        schema.update(definitions)
    preserve_public_tool_dependency_guidance(callable_func, schema)
    return schema


def _output_schema_for(target: ToolTarget) -> object | None:
    raw = cast('object | None', getattr(target, OUTPUT_SCHEMA_ATTR, None))
    if raw is None:
        try:
            annotation = get_type_hints(target).get('return')
        except (TypeError, NameError):
            annotation = None
        if annotation in (GeneratedFile, GeneratedFiles):
            raw = annotation
    if isinstance(raw, type) and issubclass(raw, BaseModel):
        return raw.model_json_schema()
    return cast('object | None', raw)


def _schema_for_parameter(
    parameter: Parameter,
    resolved_annotation: object | None,
    *,
    definition_adapters: dict[str, TypeAdapter[object]],
) -> JsonObject:
    annotation = resolved_annotation or parameter.annotation
    if annotation is not Signature.empty:
        try:
            adapter: TypeAdapter[object] = TypeAdapter(annotation)
            schema = adapter.json_schema()
        except (TypeError, ValueError):
            pass
        else:
            if '$defs' in schema:
                definition_adapters[parameter.name] = adapter
            return schema
    schema_by_annotation: dict[object, JsonObject] = {
        bool: {'type': 'boolean'},
        int: {'type': 'integer'},
        float: {'type': 'number'},
        str: {'type': 'string'},
        list: {'type': 'array'},
        tuple: {'type': 'array'},
        dict: {'type': 'object'},
    }
    return schema_by_annotation.get(annotation, {'description': parameter.name})


def _resolved_type_hints(func: Callable[..., object]) -> dict[str, object]:
    try:
        return cast('dict[str, object]', get_type_hints(func, include_extras=True))
    except Exception:  # noqa: BLE001 - third-party annotations may not resolve locally.
        return {}


def _dependency_arg_names(func: object) -> frozenset[str]:
    # Every dependency kind fills its argument from outside the model, so none of them may
    # appear in the model-facing schema. Interrupt args were missing from this list, so the
    # model still saw an argument only a human could supply - and looped trying to invent it.
    dependencies: list[object] = []
    for attr_name in (
        PRIVATE_DATA_DEPENDENCIES_ATTR,
        TOOL_DEPENDENCIES_ATTR,
        INTERRUPT_DEPENDENCIES_ATTR,
    ):
        raw = getattr(func, attr_name, ())
        if isinstance(raw, list):
            dependencies.extend(cast('list[object]', raw))
    return frozenset(
        arg_name
        for dependency in dependencies
        if isinstance((arg_name := getattr(dependency, 'arg_name', None)), str)
    )


def preserve_public_tool_dependency_guidance(
    target: ToolTarget,
    schema: JsonObject,
) -> None:
    """Retain public prerequisites and field guidance after bound inputs are hidden.

    Dependency values are runtime-bound, so their properties cannot remain provider
    inputs. Their explicit descriptions may still tell a model how to use the public
    result. Private-data dependencies never contribute guidance here.
    """
    private_args = {
        arg_name
        for dependency in _metadata_values(target, PRIVATE_DATA_DEPENDENCIES_ATTR)
        if isinstance(arg_name := getattr(dependency, 'arg_name', None), str)
    }
    guidance: list[str] = []
    dependencies = _metadata_values(target, TOOL_DEPENDENCIES_ATTR)
    model_fields = (
        target.model_fields if isinstance(target, type) and issubclass(target, BaseModel) else {}
    )
    for dependency in dependencies:
        if not isinstance(dependency, ToolDependency) or dependency.arg_name in private_args:
            continue
        guidance.append(
            f'- {dependency.arg_name}: supplied by {dependency.tool_name}; '
            'this tool can execute only after that producer succeeds. '
            'The runtime supplies its result; do not copy it into arguments.'
        )
        field_info = model_fields.get(dependency.arg_name)
        description = None if field_info is None else field_info.description
        if isinstance(description, str) and description.strip():
            guidance.append(f'- {dependency.arg_name}: {description.strip()}')
    if not guidance:
        return
    marker = 'Public dependency guidance:'
    existing = schema.get('description')
    # The schema is freshly generated on each compilation. Authored prose that
    # happens to contain our heading must not suppress dependency descriptions.
    prefix = existing.strip() if isinstance(existing, str) else ''
    parts = (prefix, marker, '\n'.join(dict.fromkeys(guidance)))
    schema['description'] = '\n\n'.join(part for part in parts if part)


def _metadata_values(target: object, attr_name: str) -> list[object]:
    """Return owned decorator metadata without treating arbitrary values as records."""
    raw = getattr(target, attr_name, ())
    return list(cast('list[object]', raw)) if isinstance(raw, list) else []


def _copy_builder_metadata(builder: ScopeToolifyBuilder, target: ToolTarget) -> None:
    """Move outer compatibility decorators from a toolify builder onto its target."""
    for attr_name in (
        EXECUTION_CONTROLS_ATTR,
        INTERRUPT_DEPENDENCIES_ATTR,
        PRIVATE_DATA_DEPENDENCIES_ATTR,
        TOOL_DEPENDENCIES_ATTR,
    ):
        values = getattr(builder, attr_name, None)
        if not isinstance(values, list):
            continue
        _extend_metadata(target, attr_name, cast('list[object]', values))

    output_schema = getattr(builder, OUTPUT_SCHEMA_ATTR, None)
    if output_schema is not None:
        setattr(target, OUTPUT_SCHEMA_ATTR, output_schema)


def _extend_metadata(target: object, attr_name: str, values: list[object]) -> None:
    """Append metadata without duplicating declarations already present on a target."""
    current = getattr(target, attr_name, None)
    if current is None:
        stored: list[object] = []
        setattr(target, attr_name, stored)
    elif isinstance(current, list):
        stored = cast('list[object]', current)
    else:
        message = f'{attr_name} metadata must be a list'
        raise TypeError(message)

    for value in values:
        if value not in stored:
            stored.append(value)


__all__ = [
    'ScopeToolifyBuilder',
    'ToolRegistrationOptions',
    'ToolingScopeMixin',
    'compile_tool_metadata',
    'compile_tool_metadata_graph',
    'tool_metadata_for_callable',
    'toolify_builder_for_scope',
    'toolify_options',
    'toolset_options',
]
