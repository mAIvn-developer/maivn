"""Tool compilation for a scope: local graph, MCP brokerage, hooks, and targeting."""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, cast

from maivn._internal.compat.decorators import TOOL_DEPENDENCIES_ATTR, ToolDependency
from maivn._internal.compat.mcp import MCPServer, hide_tool_args_from_model
from maivn._internal.compat.tooling import compile_tool_metadata_graph
from maivn._internal.hooks import HookBinding, compose_hooks
from maivn._internal.models import JsonObject, ToolMetadata
from maivn._internal.private_placeholders import resolve_private_placeholders

if TYPE_CHECKING:
    from maivn._internal.scope.types import ToolSpec


@dataclass(frozen=True, slots=True)
class _MCPToolTarget:
    """Callable bridge from a brokered SDK tool call to its registered MCP server."""

    server: MCPServer
    tool_name: str
    default_args: Mapping[str, object]
    injected_args: Mapping[str, object] = field(default_factory=dict[str, object])

    def __call__(self, **arguments: object) -> JsonObject:
        merged = dict(self.default_args)
        merged.update(arguments)
        # Injected arguments win. They are already hidden from the model, but a
        # forged credential overwriting the configured one is the failure this
        # exists to prevent, so it must lose here too.
        merged.update(self.injected_args)
        return self.server.call_tool(self.tool_name, merged)


def compile_scope_tools(scope: object) -> list[ToolMetadata]:
    """Return the scope's compiled tool graph, rebuilding it when the cache is stale."""
    cache = getattr(scope, '_compiled_tools_cache', None)
    dirty = bool(getattr(scope, '_tools_dirty', True))
    hook_configuration = _hook_configuration_key(scope)
    cache_hook_configuration = getattr(scope, '_compiled_hook_configuration', None)
    if cache is None or dirty or cache_hook_configuration != hook_configuration:
        tools = getattr(scope, 'tools', [])
        cache = compile_tool_metadata_graph(
            [tool for tool in cast('Sequence[object]', tools) if isinstance(tool, ToolMetadata)]
        )
        cache.extend(compile_mcp_tools(scope))
        cache = _with_scope_execution_hooks(cache, scope)
        setattr(scope, '_compiled_tools_cache', cache)  # noqa: B010 - dynamic scope cache shim.
        setattr(scope, '_tools_dirty', False)  # noqa: B010 - dynamic scope cache shim.
        setattr(  # noqa: B010 - dynamic scope cache shim.
            scope,
            '_compiled_hook_configuration',
            hook_configuration,
        )
    return list(cast('list[ToolMetadata]', cache))


def _with_scope_execution_hooks(
    tools: list[ToolMetadata],
    scope: object,
) -> list[ToolMetadata]:
    """Chain scope-level execution hooks onto each compiled tool.

    v1 fired before/after hooks at swarm, agent, and tool level around every
    local tool call; v2 captured the callables but never fired the scope
    levels. The chain runs outside-in for before (swarm, agent, tool) and
    inside-out for after, matching v1's observed ordering. Each link keeps the
    level that registered it so a run trace can name the callback and the card
    it belongs to. A scope with no hooks compiles exactly as before.
    """
    scope_chain: list[object] = []
    current: object | None = scope
    while current is not None:
        scope_chain.append(current)
        current = getattr(current, '_parent_hook_scope', None)
    # Outermost scope first for the before chain.
    scope_chain.reverse()
    scope_before = [
        binding
        for item in scope_chain
        if _scope_hook_applies_to_tool(item, compiled_scope=scope)
        and (binding := _scope_binding(item, 'before_execute')) is not None
    ]
    scope_after = [
        binding
        for item in reversed(scope_chain)
        if _scope_hook_applies_to_tool(item, compiled_scope=scope)
        and (binding := _scope_binding(item, 'after_execute')) is not None
    ]
    if not scope_before and not scope_after:
        return tools
    wrapped: list[ToolMetadata] = []
    for tool in tools:
        before_chain = [*scope_before, *_tool_binding(tool, tool.before_execute)]
        after_chain = [*_tool_binding(tool, tool.after_execute), *scope_after]
        wrapped.append(
            tool.model_copy(
                update={
                    'before_execute': compose_hooks(before_chain),
                    'after_execute': compose_hooks(after_chain),
                }
            )
        )
    return wrapped


def _hook_configuration_key(scope: object) -> tuple[tuple[int, str, int, int], ...]:
    """Return the mutable hook attributes that affect compiled tool wrappers.

    Scope descriptors deliberately remain ordinary dataclass fields for v1
    compatibility, so assigning a new mode or callback cannot set the normal
    ``_tools_dirty`` flag. Keep that ergonomics while ensuring the cache never
    returns wrappers compiled for an earlier hook configuration.
    """
    chain: list[tuple[int, str, int, int]] = []
    current: object | None = scope
    while current is not None:
        mode = getattr(current, 'hook_execution_mode', 'tool')
        chain.append(
            (
                id(current),
                mode if isinstance(mode, str) else 'tool',
                id(getattr(current, 'before_execute', None)),
                id(getattr(current, 'after_execute', None)),
            )
        )
        current = getattr(current, '_parent_hook_scope', None)
    return tuple(chain)


def _scope_hook_applies_to_tool(item: object, *, compiled_scope: object) -> bool:
    """Return whether one scope callback surrounds an individual local tool.

    ``tool`` remains the historical default. ``scope`` belongs to the
    invocation wrapper and ``agent`` belongs to the member-assignment
    lifecycle wrapper, so neither may be repeated around a local tool.
    """
    mode = getattr(item, 'hook_execution_mode', 'tool')
    if mode == 'tool':
        return True
    _ = compiled_scope
    return False


def _scope_binding(item: object, attribute: str) -> HookBinding | None:
    """Bind one scope's hook to the card that scope owns."""
    hook = getattr(item, attribute, None)
    if not callable(hook):
        return None
    # A swarm is the only scope that holds member agents; everything else in
    # the chain renders as an agent card. Duck-typed on purpose - this module
    # compiles any scope shape and must not import the scope classes.
    is_swarm = isinstance(getattr(item, 'agents', None), list)
    name = getattr(item, 'name', None)
    return HookBinding(
        hook=hook,
        source='swarm' if is_swarm else 'scope',
        target_type='swarm' if is_swarm else 'agent',
        target_name=name if isinstance(name, str) and name else None,
    )


def _tool_binding(
    tool: ToolMetadata,
    hook: Callable[..., object] | None,
) -> list[HookBinding]:
    """Bind a tool's own hook to that tool's card."""
    if hook is None:
        return []
    return [
        HookBinding(
            hook=hook,
            source='tool',
            target_type='tool',
            target_name=tool.name,
        )
    ]


def compile_mcp_tools(scope: object) -> list[ToolMetadata]:
    """Discover registered MCP tools and expose them through the SDK-local broker."""
    servers = getattr(scope, 'mcp_servers', ())
    raw_private_data = cast('Mapping[object, object] | None', getattr(scope, 'private_data', None))
    private_data = {str(key): value for key, value in (raw_private_data or {}).items()}
    compiled: list[ToolMetadata] = []
    for server in cast('Sequence[object]', servers):
        if not isinstance(server, MCPServer):
            continue
        for raw_tool in server.list_tools():
            raw_name = raw_tool.get('name')
            if not isinstance(raw_name, str) or not raw_name:
                message = f'MCP server {server.name!r} returned a tool without a name'
                raise ValueError(message)
            raw_schema = raw_tool.get('inputSchema', raw_tool.get('input_schema'))
            schema: JsonObject = (
                dict(cast('Mapping[str, object]', raw_schema))
                if isinstance(raw_schema, Mapping)
                else {'type': 'object', 'properties': cast('object', {})}
            )
            description = raw_tool.get('description')
            # Resolve private-data placeholders HERE, not in LocalToolRuntime. These
            # arguments are developer configuration merged in at call time, downstream
            # of the runtime's resolution of the model's own arguments - so a
            # placeholder parked in `tool_defaults` reached the MCP server verbatim and
            # was rejected as a malformed URL, silently emptying every feed in
            # financial-planner-mcp.
            defaults = cast(
                'JsonObject',
                resolve_private_placeholders(server.resolve_tool_defaults(raw_name), private_data),
            )
            injected = cast(
                'JsonObject',
                resolve_private_placeholders(
                    server.resolve_injected_tool_args(raw_name), private_data
                ),
            )
            schema = hide_tool_args_from_model(
                schema, server.model_hidden_arg_names(raw_name, schema)
            )
            compiled.append(
                ToolMetadata(
                    name=server.build_tool_name(raw_name),
                    description=(
                        description
                        if isinstance(description, str) and description
                        else f'Execute MCP tool {raw_name}.'
                    ),
                    input_schema=schema,
                    metadata={'mcp_server': server.name, 'mcp_tool': raw_name},
                    target=_MCPToolTarget(
                        server=server,
                        tool_name=raw_name,
                        default_args=defaults,
                        injected_args=injected,
                    ),
                )
            )
    return compiled


def select_targeted_tools(
    tools: Sequence[ToolMetadata], targeted_tools: Sequence[str] | None
) -> list[ToolMetadata]:
    """Return requested tools plus their transitive tool dependencies.

    Studio persists both bare tool names and legacy ``agent.tool`` names. The
    latter is accepted when its final segment uniquely identifies a compiled
    tool, keeping existing Studio configurations usable on the v2 SDK. A
    targeted tool is not executable when its ``depends_on_tool`` producers are
    removed, so dependencies are inserted before their consumers.
    """
    if targeted_tools is None:
        return list(tools)

    by_name = {tool.name: tool for tool in tools}
    requested: list[ToolMetadata] = []
    requested_names: set[str] = set()
    unknown: list[str] = []
    for raw_name in targeted_tools:
        name = raw_name.strip()
        tool = by_name.get(name)
        if tool is None and '.' in name:
            tool = by_name.get(name.rsplit('.', 1)[-1])
        if tool is None:
            unknown.append(raw_name)
            continue
        if tool.name not in requested_names:
            requested.append(tool)
            requested_names.add(tool.name)

    if unknown:
        available = ', '.join(sorted(by_name)) or '(none)'
        requested_label = ', '.join(repr(name) for name in unknown)
        message = f'Unknown targeted tool(s): {requested_label}. Available tools: {available}'
        raise ValueError(message)
    if not requested:
        message = 'targeted_tools must contain at least one tool name'
        raise ValueError(message)

    return _include_target_dependencies(by_name, requested)


def _include_target_dependencies(
    by_name: Mapping[str, ToolMetadata], requested: Sequence[ToolMetadata]
) -> list[ToolMetadata]:
    """Order targeted tools after every producer in their dependency closure."""
    selected: list[ToolMetadata] = []
    selected_names: set[str] = set()
    visiting: set[str] = set()

    def include(tool: ToolMetadata) -> None:
        if tool.name in selected_names or tool.name in visiting:
            return
        visiting.add(tool.name)
        raw_dependencies = getattr(tool.target, TOOL_DEPENDENCIES_ATTR, None)
        if isinstance(raw_dependencies, list):
            for dependency in cast('list[object]', raw_dependencies):
                if not isinstance(dependency, ToolDependency):
                    continue
                producer = by_name.get(dependency.tool_name)
                if producer is None:
                    message = (
                        f'Targeted tool {tool.name!r} depends on unavailable tool '
                        f'{dependency.tool_name!r}'
                    )
                    raise ValueError(message)
                include(producer)
        visiting.remove(tool.name)
        selected.append(tool)
        selected_names.add(tool.name)

    for tool in requested:
        include(tool)
    return selected


def validate_tool_metadata(tools: Sequence[ToolSpec], *, owner: str) -> None:
    """Reject a scope that registered two tools under one name."""
    seen_names: set[str] = set()
    for tool in tools:
        if not isinstance(tool, ToolMetadata):
            continue
        if tool.name in seen_names:
            message = f'{owner} has duplicate tool name: {tool.name}'
            raise ValueError(message)
        seen_names.add(tool.name)


def resolve_force_final_tool(*, default: bool, call_value: bool | None) -> bool:
    """Use a call-time force-final value when supplied, otherwise the Agent default."""
    return default if call_value is None else call_value


def has_final_tool(tools: Sequence[ToolSpec]) -> bool:
    """Report whether any compiled tool in the sequence is marked as the final tool."""
    return any(isinstance(tool, ToolMetadata) and tool.final_tool for tool in tools)
