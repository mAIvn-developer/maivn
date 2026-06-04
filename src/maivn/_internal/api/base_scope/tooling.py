"""Tooling and registration helpers for ``BaseScope``."""

# pyright: strict
from __future__ import annotations

from collections.abc import Callable, Iterable, Mapping, Sequence
from typing import cast

from maivn_shared import (
    DESTRUCTIVE_FLAG_NAMES as _DESTRUCTIVE_FLAG_NAMES,
)
from maivn_shared import (
    BaseDependency,
    PermissionSet,
    PrivateData,
    RedactedMessage,
    RedactionPreviewResponse,
)
from pydantic import BaseModel

from maivn._internal.api.mcp import MCPServer
from maivn._internal.core.entities.tools import (
    BaseTool,
    FunctionTool,
    McpTool,
    MethodTool,
    ModelTool,
)
from maivn._internal.core.interfaces.repositories import (
    DependencyRepoInterface,
    ToolRepoInterface,
)
from maivn._internal.core.interfaces.resolvers import ScopeResolverInterface
from maivn._internal.core.registrars import ToolRegistrar
from maivn._internal.core.services.toolify.service import (
    ToolHook,
    ToolifyOptions,
    ToolifyService,
)
from maivn._internal.utils.toolset import (
    MethodToolifyOptions,
    ToolsetOptions,
    derive_default_prefix,
    get_toolify_options,
    get_toolset_options,
)

from ..tool_override import ToolOverride, apply_override
from .builders import (
    EventInvocationBuilder,
    StructuredOutputInvocationBuilder,
    ToolifyDecoratorBuilder,
)
from .mcp import McpRegistry
from .redaction import preview_redaction as _preview_redaction
from .validation import (
    raise_validation_error,
    validate_swarm_final_output_agents,
    validate_tool_flags_per_scope,
)

# MARK: Scope Tooling


class BaseScopeToolingMixin:
    def toolify(
        self,
        name: str | None = None,
        description: str | None = None,
        *,
        always_execute: bool = False,
        final_tool: bool = False,
        tags: list[str] | None = None,
        before_execute: ToolHook | None = None,
        after_execute: ToolHook | None = None,
    ) -> ToolifyDecoratorBuilder:
        options = ToolifyOptions(
            name=name,
            description=description,
            always_execute=always_execute,
            final_tool=final_tool,
            tags=tags,
            before_execute=before_execute,
            after_execute=after_execute,
        )
        return ToolifyDecoratorBuilder(scope=self, options=options)

    def add_toolset(
        self,
        instance: object,
        *,
        include: list[str] | tuple[str, ...] | None = None,
        exclude: list[str] | tuple[str, ...] | None = None,
        include_tags: list[str] | tuple[str, ...] | None = None,
        exclude_tags: list[str] | tuple[str, ...] | None = None,
        overrides: dict[str, ToolOverride] | None = None,
    ) -> list[MethodTool]:
        """Register every ``@toolify``-marked method on ``instance`` as a tool.

        ``instance`` must be a Python object whose class was decorated with
        :func:`maivn.toolset`. Each method decorated with
        :func:`maivn.toolify` becomes one :class:`MethodTool` on this scope.
        Tool names are prefixed with the toolset's ``prefix`` (defaulting to
        the class name in snake_case).

        The four optional filters narrow which methods register as tools:

        * ``include`` — only methods whose name (the unprefixed Python
          method name, or the ``@toolify(name=...)`` override) matches one
          of these entries are registered.
        * ``exclude`` — methods whose name matches are skipped.
        * ``include_tags`` — only methods carrying at least one of these
          tags are registered. Tags are the union of the toolset-level
          ``tags``, the method-level ``@toolify(tags=...)``, and the
          auto-derived tags from ``permissions``/``destructive`` (e.g.
          ``"read"``, ``"write"``, ``"delete"``, ``"destructive"``).
        * ``exclude_tags`` — methods carrying any of these tags are
          skipped.

        Filters compose: a method must pass every active filter. Filters
        run before the duplicate-name and ``require_marker`` checks, so
        narrowing a toolset down to a single tool is a valid path.

        Args:
            instance: A toolset instance to register.
            include: Allowlist of method names (post-resolution).
            exclude: Denylist of method names.
            include_tags: Allowlist of tags; a method must carry at least
                one matching tag (user-supplied or auto-derived).
            exclude_tags: Denylist of tags.
            overrides: Per-method :class:`ToolOverride` map keyed by the
                bare method name (same keys used by ``include`` /
                ``exclude``). Each override is merged onto the generic
                ``@toolify`` configuration at registration time — set
                ``always_execute=True`` to pin a discovery tool, bake
                in ``default_args``, append app-specific
                ``dependencies``, or replace the description for the
                LLM. Keys that don't match any discovered method raise
                ``ValueError`` so typos don't silently no-op.

        Raises:
            TypeError: When ``instance``'s class was not decorated with
                :func:`maivn.toolset`.
            ValueError: When ``require_marker=True`` (the default) and no
                methods survive the filters, or when a tool name resolves
                to a duplicate within the same toolset, or when
                ``overrides`` contains a key that doesn't match any
                discovered method on the toolset.
        """
        cls = type(instance)
        toolset_options = get_toolset_options(cls)
        if toolset_options is None:
            raise TypeError(
                "".join(
                    (
                        f"{cls.__name__} is not a toolset. Decorate the class with ",
                        "@maivn.toolset(...) before passing instances to add_toolset().",
                    )
                )
            )

        prefix = toolset_options.prefix or derive_default_prefix(cls)
        include_set = frozenset(include) if include is not None else None
        exclude_set = frozenset(exclude) if exclude is not None else None
        include_tags_set = frozenset(include_tags) if include_tags is not None else None
        exclude_tags_set = frozenset(exclude_tags) if exclude_tags is not None else None
        overrides_map: dict[str, ToolOverride] = dict(overrides or {})

        methods = self._discover_toolset_methods(instance, cls, toolset_options)
        registered: list[MethodTool] = []
        seen_names: set[str] = set()
        if overrides_map:
            discovered_names = {
                (method_options.name or method_name)
                for method_name, _bound, method_options in methods
            } | {method_name for method_name, _bound, _opts in methods}
            unknown = sorted(set(overrides_map) - discovered_names)
            if unknown:
                raise ValueError(
                    f"Toolset {cls.__name__!r} has no methods matching "
                    + f"override keys: {unknown}. Override keys must match "
                    + "the bare Python method name (or the @toolify(name=...) "
                    + "override) — never the prefixed tool name."
                )

        for method_name, bound_method, method_options in methods:
            base_name = method_options.name or method_name
            # Toolset prefix is uppercased and joined with ``_`` so the
            # generated tool name (e.g. ``GMAIL_get_message``) matches the
            # OpenAI/Anthropic tool-name regex ``^[a-zA-Z0-9_-]{1,64}$``.
            # Dots in tool names trigger LLM-side auto-normalization
            # (``gmail.x`` -> ``gmail_x``), which collides with the
            # server-side strict registry lookup and surfaces as
            # "Tool not found" errors.
            full_name = f"{prefix.upper()}_{base_name}" if prefix else base_name

            if not self._passes_toolset_filter(
                base_name=base_name,
                method_options=method_options,
                toolset_options=toolset_options,
                include=include_set,
                exclude=exclude_set,
                include_tags=include_tags_set,
                exclude_tags=exclude_tags_set,
            ):
                continue

            if full_name in seen_names:
                raise ValueError(
                    f"Toolset {cls.__name__!r} produced a duplicate tool name {full_name!r}; "
                    + "rename the method or pass an explicit name= to @toolify."
                )
            seen_names.add(full_name)

            # Overrides are keyed by the bare Python method name OR the
            # @toolify(name=...) alias — same convention as include/exclude.
            method_override = overrides_map.get(method_name) or overrides_map.get(base_name)
            merged_options = self._merge_toolset_options(
                toolset_options=toolset_options,
                method_options=method_options,
                resolved_name=full_name,
                override=method_override,
            )
            qualified = f"{cls.__name__}.{method_name}"
            toolify_service = self._toolify_service_for_mixin()
            registered_tool = toolify_service.create_method_tool(
                bound_method=bound_method,
                owner=instance,
                options=merged_options,
                qualified_name=qualified,
            )
            if method_override is not None and method_override.dependencies:
                for dep in method_override.dependencies:
                    registered_tool.add_dependency(dep)
            toolify_service.register_tool(
                tool=registered_tool,
                registrar=self._tool_registrar_for_mixin(),
                dependency_repo=self._dependency_repo_for_mixin(),
            )
            # Dependency callbacks attach to the underlying function so that
            # dynamic decorators applied after registration still take effect.
            underlying = cast(object, getattr(bound_method, "__func__", bound_method))
            toolify_service.setup_dependency_callback(
                obj=underlying,
                tool=registered_tool,
                dependency_repo=self._dependency_repo_for_mixin(),
            )
            self._attach_registered_tool_id(underlying, registered_tool)
            registered.append(registered_tool)

        if not registered and toolset_options.require_marker:
            filter_active = any(
                f is not None
                for f in (include_set, exclude_set, include_tags_set, exclude_tags_set)
            )
            hint = (
                "All matching methods were filtered out by include/exclude rules; "
                + "loosen the filter or set @toolset(require_marker=False)."
                if filter_active
                else "Decorate methods with @maivn.toolify or set "
                + "@toolset(require_marker=False) for grandfathered classes."
            )
            raise ValueError(
                f"Toolset {cls.__name__!r} has no @toolify-marked methods to register. {hint}"
            )

        self._set_tools_dirty(True)
        return registered

    def add_tool(
        self,
        tool: BaseTool | Callable[..., object] | type[BaseModel],
        name: str | None = None,
        description: str | None = None,
        *,
        always_execute: bool = False,
        final_tool: bool = False,
        tags: list[str] | None = None,
        before_execute: ToolHook | None = None,
        after_execute: ToolHook | None = None,
        override: ToolOverride | None = None,
    ) -> BaseTool:
        """Register a callable, Pydantic model, or prebuilt tool on this scope.

        ``override`` is an optional :class:`ToolOverride` whose set
        fields replace / extend the corresponding kwargs above. It uses
        the same shape that ``add_toolset(overrides={...})`` and
        ``MCPServer(tool_overrides={...})`` accept, so the
        registration knobs are uniform across all three paths.
        """
        applied = apply_override(
            override,
            base_name=name,
            base_description=description,
            base_tags=tags,
            base_metadata=None,
            base_always_execute=always_execute,
            base_final_tool=final_tool,
            base_before_execute=before_execute,
            base_after_execute=after_execute,
        )
        options = ToolifyOptions(
            name=applied.name,
            description=applied.description,
            always_execute=applied.always_execute,
            final_tool=applied.final_tool,
            tags=applied.tags,
            metadata=dict(cast(Mapping[str, object], applied.metadata)) or None,
            before_execute=applied.before_execute,
            after_execute=applied.after_execute,
        )
        if applied.default_args:
            options.metadata["default_args"] = dict(
                cast(Mapping[str, object], applied.default_args)
            )
        registered_tool = self._coerce_tool_for_registration(tool, options)
        if applied.dependencies:
            for dep in applied.dependencies:
                registered_tool.add_dependency(dep)
        callback_source = self._get_tool_callback_source(tool=tool, registered_tool=registered_tool)

        toolify_service = self._toolify_service_for_mixin()
        toolify_service.register_tool(
            tool=registered_tool,
            registrar=self._tool_registrar_for_mixin(),
            dependency_repo=self._dependency_repo_for_mixin(),
        )
        toolify_service.setup_dependency_callback(
            obj=callback_source,
            tool=registered_tool,
            dependency_repo=self._dependency_repo_for_mixin(),
        )
        self._attach_registered_tool_id(callback_source, registered_tool)
        self._set_tools_dirty(True)
        return registered_tool

    def structured_output(self, model: type[BaseModel]) -> StructuredOutputInvocationBuilder:
        return StructuredOutputInvocationBuilder(scope=self, model=model)

    def events(
        self,
        *,
        include: Sequence[str] | str | None = None,
        exclude: Sequence[str] | str | None = None,
        on_event: Callable[[dict[str, object]], None] | None = None,
        auto_verbose: bool = True,
    ) -> EventInvocationBuilder:
        """Build an invocation wrapper with filtered event reporting."""
        return EventInvocationBuilder(
            scope=self,
            include=include,
            exclude=exclude,
            on_event=on_event,
            auto_verbose=auto_verbose,
        )

    def preview_redaction(
        self,
        message: RedactedMessage,
        *,
        known_pii_values: list[str | PrivateData] | None = None,
        private_data: dict[str, object] | None = None,
    ) -> RedactionPreviewResponse:
        return _preview_redaction(
            self,
            message,
            known_pii_values=known_pii_values,
            private_data=private_data,
        )

    # MARK: - MCP Server Registration

    def register_mcp_servers(self, servers: MCPServer | Sequence[MCPServer]) -> None:
        """Register MCP servers and expose their tools in this scope."""
        self._mcp_registry_for_mixin().register_servers(servers)

    def list_mcp_servers(self) -> list[MCPServer]:
        """List MCP servers registered with this scope."""
        return self._mcp_registry_for_mixin().list_servers()

    def close_mcp_servers(self) -> None:
        """Close all MCP server connections for this scope."""
        try:
            private_state: dict[str, object] | None = cast(
                dict[str, object] | None,
                getattr(self, "__pydantic_private__"),  # noqa: B009 - Pydantic state.
            )
        except AttributeError:
            return
        if private_state is None:
            return
        registry = cast(McpRegistry | None, private_state.get("_mcp_registry"))
        if registry is None:
            return
        registry.close_servers()

    # MARK: - Tool Access

    def get_tool(self, tool_id: str) -> BaseTool | None:
        return self._tool_repo_for_mixin().get_tool(tool_id)

    def list_tools(self) -> list[BaseTool]:
        return self._tool_repo_for_mixin().list_tools()

    # MARK: - Tool Compilation

    def compile_tools(self) -> list[FunctionTool | MethodTool | ModelTool | McpTool]:
        compiled_tools_cache = self._compiled_tools_cache_for_mixin()
        if not self._tools_dirty_for_mixin() and compiled_tools_cache is not None:
            return compiled_tools_cache

        compiled = self._compile_all_tools()
        setattr(self, "_compiled_tools_cache", compiled)  # noqa: B010 - Pydantic PrivateAttr.
        self._set_tools_dirty(False)
        return compiled

    def _compile_all_tools(self) -> list[FunctionTool | MethodTool | ModelTool | McpTool]:
        compiled: list[FunctionTool | MethodTool | ModelTool | McpTool] = []
        for tool in self._tool_repo_for_mixin().list_tools():
            if isinstance(tool, (FunctionTool, MethodTool, ModelTool)):
                self._resolve_tool_dependencies(tool)
                compiled.append(tool)
            elif isinstance(tool, McpTool):
                compiled.append(tool)
        return compiled

    def _resolve_tool_dependencies(self, tool: FunctionTool | MethodTool | ModelTool) -> None:
        raw_deps = list(cast(Iterable[BaseDependency], getattr(tool, "dependencies", []) or []))
        if not raw_deps:
            repo_tool_id = cast(str, getattr(tool, "tool_id", ""))
            raw_deps = list(self._dependency_repo_for_mixin().list_dependencies(repo_tool_id))
        resolver = self._resolver_for_mixin()
        tool.dependencies = [resolver.resolve(dep) for dep in raw_deps]
        self._tool_repo_for_mixin().update_tool(tool)

    # MARK: - Validation

    def validate_tool_configuration(self) -> None:
        errors = validate_tool_flags_per_scope(self)
        errors.extend(validate_swarm_final_output_agents(self))

        if errors:
            raise_validation_error(errors)

    # MARK: - Mixin Attribute Access

    def _tool_repo_for_mixin(self) -> ToolRepoInterface:
        return cast(ToolRepoInterface, getattr(self, "_tool_repo"))  # noqa: B009

    def _dependency_repo_for_mixin(self) -> DependencyRepoInterface:
        return cast(
            DependencyRepoInterface,
            getattr(self, "_dependency_repo"),  # noqa: B009 - Pydantic PrivateAttr.
        )

    def _resolver_for_mixin(self) -> ScopeResolverInterface:
        return cast(
            ScopeResolverInterface,
            getattr(self, "_resolver"),  # noqa: B009 - Pydantic PrivateAttr.
        )

    def _tool_registrar_for_mixin(self) -> ToolRegistrar:
        return cast(ToolRegistrar, getattr(self, "_tool_registrar"))  # noqa: B009

    def _toolify_service_for_mixin(self) -> ToolifyService:
        return cast(ToolifyService, getattr(self, "_toolify_service"))  # noqa: B009

    def _mcp_registry_for_mixin(self) -> McpRegistry:
        return cast(McpRegistry, getattr(self, "_mcp_registry"))  # noqa: B009

    def _compiled_tools_cache_for_mixin(
        self,
    ) -> list[FunctionTool | MethodTool | ModelTool | McpTool] | None:
        return cast(
            list[FunctionTool | MethodTool | ModelTool | McpTool] | None,
            getattr(self, "_compiled_tools_cache"),  # noqa: B009 - Pydantic PrivateAttr.
        )

    def _tools_dirty_for_mixin(self) -> bool:
        return cast(bool, getattr(self, "_tools_dirty"))  # noqa: B009 - Pydantic PrivateAttr.

    def _set_tools_dirty(self, value: bool) -> None:
        setattr(self, "_tools_dirty", value)  # noqa: B010 - Pydantic PrivateAttr.

    # MARK: - Registration Helpers

    def _coerce_tool_for_registration(
        self,
        tool: BaseTool | Callable[..., object] | type[BaseModel],
        options: ToolifyOptions,
    ) -> BaseTool:
        if isinstance(tool, BaseTool):
            self._apply_existing_tool_options(tool, options)
            return tool

        return self._toolify_service_for_mixin().create_tool(tool, options)

    @staticmethod
    def _apply_existing_tool_options(tool: BaseTool, options: ToolifyOptions) -> None:
        if options.name is not None:
            tool.name = options.name
        if options.description is not None:
            tool.description = options.description
        if options.always_execute:
            tool.always_execute = True
        if options.final_tool:
            tool.final_tool = True
        if options.tags:
            tool.tags = list(options.tags)
        if options.before_execute is not None:
            tool.before_execute = options.before_execute
        if options.after_execute is not None:
            tool.after_execute = options.after_execute

    @staticmethod
    def _get_tool_callback_source(
        *,
        tool: BaseTool | Callable[..., object] | type[BaseModel],
        registered_tool: BaseTool,
    ) -> object:
        if not isinstance(tool, BaseTool):
            return tool
        if isinstance(registered_tool, FunctionTool):
            return registered_tool.func
        if isinstance(registered_tool, ModelTool):
            return registered_tool.model
        return registered_tool

    @staticmethod
    def _attach_registered_tool_id(obj: object, tool: BaseTool) -> None:
        tool_id = getattr(tool, "tool_id", None)
        if tool_id is None:
            return
        try:
            setattr(obj, "tool_id", tool_id)  # noqa: B010 - dynamic tool target.
        except Exception:  # noqa: BLE001 - dynamic targets may reject attribute assignment.
            pass

    # MARK: - Toolset Discovery

    @staticmethod
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
        """Return True when a method survives the registration filters.

        ``base_name`` is the post-resolution method name (the
        ``@toolify(name=...)`` override or the Python attribute name); we
        intentionally match on the unprefixed name so callers don't have
        to know the toolset prefix when narrowing.

        Tag matching uses the union of:

        * ``toolset_options.tags`` (class-level),
        * ``method_options.tags`` (method-level),
        * auto-derived permission tags (``"read"``, ``"write"``,
          ``"delete"``, ``"export"``, ``"import"``, ``"admin"``,
          ``"impersonate"``),
        * the literal ``"destructive"`` tag for any method marked
          destructive or carrying a destructive permission flag.

        That way callers can write ``include_tags=["read"]`` or
        ``exclude_tags=["destructive"]`` without manually tagging every
        method.
        """
        if include is not None and base_name not in include:
            return False
        if exclude is not None and base_name in exclude:
            return False

        if include_tags is None and exclude_tags is None:
            return True

        all_tags = _all_filter_tags(method_options, toolset_options)
        if include_tags is not None and not (include_tags & all_tags):
            return False
        if exclude_tags is not None and (exclude_tags & all_tags):
            return False
        return True

    @staticmethod
    def _discover_toolset_methods(
        instance: object,
        cls: type[object],
        toolset_options: ToolsetOptions,
    ) -> list[tuple[str, Callable[..., object], MethodToolifyOptions]]:
        """Return ``(method_name, bound_method, options)`` for every tool method.

        Walks the class (not the instance) so we only see methods that were
        declared at class definition time. Skips private names, properties,
        and nested classes. When ``require_marker`` is true (default), only
        methods carrying :func:`toolify`'s attribute are returned.
        """
        discovered: list[tuple[str, Callable[..., object], MethodToolifyOptions]] = []
        for attr_name in dir(cls):
            if attr_name.startswith("_"):
                continue

            class_attr = getattr(cls, attr_name, None)
            if isinstance(class_attr, type):
                continue
            if isinstance(class_attr, (property, classmethod, staticmethod)):
                # Could be supported behind an explicit opt-in later; skipped
                # by default to keep the toolset surface predictable.
                continue
            if not callable(class_attr):
                continue

            bound = getattr(instance, attr_name, None)
            if not callable(bound):
                continue

            method_options = get_toolify_options(bound)
            if method_options is None:
                if toolset_options.require_marker:
                    continue
                method_options = MethodToolifyOptions()
            discovered.append((attr_name, bound, method_options))
        return discovered

    @staticmethod
    def _merge_toolset_options(
        *,
        toolset_options: ToolsetOptions,
        method_options: MethodToolifyOptions,
        resolved_name: str,
        override: ToolOverride | None = None,
    ) -> ToolifyOptions:
        """Merge class-, method-, and registration-level options into one ``ToolifyOptions``.

        Method-level options take precedence over class-level ones for
        tags and metadata; class-level supplies defaults. The
        ``permissions``/``destructive`` markers from :func:`toolify` ride
        along inside ``metadata`` so hosts can introspect them.

        When a :class:`ToolOverride` is supplied (via
        ``add_toolset(overrides={...})``), its fields are applied last —
        scalars replace, tags/dependencies append, metadata/default_args
        merge — so callers can retarget a generic toolset at registration
        time without modifying the underlying class.
        """
        merged_tags = list(dict.fromkeys((*toolset_options.tags, *method_options.tags)))
        merged_metadata: dict[str, object] = {
            **toolset_options.metadata,
            **method_options.metadata,
        }
        if method_options.permissions is not None:
            # Stored as the JSON-friendly list of canonical flag names so
            # the metadata dict can round-trip through ``model_dump(mode="json")``,
            # SSE transport, and persistence without losing fidelity. The
            # rich ``PermissionSet`` is still available in-process via
            # ``method_options.permissions`` for filter-tag derivation
            # (see ``_extract_permission_tag_names``).
            permissions = cast(PermissionSet, method_options.permissions)
            merged_metadata["permissions"] = permissions.to_list()
        if method_options.destructive:
            merged_metadata["destructive"] = True

        applied = apply_override(
            override,
            base_name=resolved_name,
            base_description=method_options.description,
            base_tags=merged_tags,
            base_metadata=merged_metadata,
            base_always_execute=method_options.always_execute,
            base_final_tool=method_options.final_tool,
            base_before_execute=method_options.before_execute,
            base_after_execute=method_options.after_execute,
        )
        # default_args live in metadata under the same key MCP uses so the
        # server executor can pre-fill omitted arguments uniformly.
        applied_metadata = dict(cast(Mapping[str, object], applied.metadata))
        if applied.default_args:
            existing_args = applied_metadata.get("default_args", {})
            merged_args = dict(
                cast(Mapping[str, object], existing_args if isinstance(existing_args, dict) else {})
            )
            merged_args.update(cast(Mapping[str, object], applied.default_args))
            applied_metadata["default_args"] = merged_args

        return ToolifyOptions(
            name=applied.name or resolved_name,
            description=applied.description,
            always_execute=applied.always_execute,
            final_tool=applied.final_tool,
            metadata=applied_metadata,
            tags=applied.tags,
            before_execute=applied.before_execute,
            after_execute=applied.after_execute,
        )


# MARK: - Toolset filter helpers


def _all_filter_tags(
    method_options: MethodToolifyOptions,
    toolset_options: ToolsetOptions,
) -> frozenset[str]:
    """Build the tag set used to evaluate include/exclude filters.

    Combines toolset-level and method-level user-supplied tags with
    auto-derived tags from the method's ``permissions`` and
    ``destructive`` markers. The result is read-only; the underlying
    options are never mutated.
    """
    tags: set[str] = set()
    tags.update(toolset_options.tags)
    tags.update(method_options.tags)

    flag_names = _extract_permission_tag_names(method_options.permissions)
    tags.update(flag_names)
    if method_options.destructive or (flag_names & _DESTRUCTIVE_FLAG_NAMES):
        tags.add("destructive")

    return frozenset(tags)


def _extract_permission_tag_names(permissions: object | None) -> frozenset[str]:
    """Return canonical tag names for a ``permissions`` value.

    Accepts ``PermissionSet`` instances (the canonical shape), bare
    strings (treated as a single tag), and ``None``. Unknown shapes
    contribute no tags so filtering never raises at registration time.
    """
    if permissions is None:
        return frozenset()
    if isinstance(permissions, PermissionSet):
        return frozenset(permissions.to_list())
    if isinstance(permissions, str):
        return frozenset({permissions.lower()})
    return frozenset()
