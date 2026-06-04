"""Class-instance toolify pattern: ``@toolify`` and ``@toolset`` decorators.

This module implements the opt-in pattern for exposing methods of a Python
class instance as Agent/Swarm tools:

* :func:`toolify` — method-level marker. Attaches
  ``__maivn_toolify__`` to the underlying function so that
  ``scope.add_toolset(instance)`` knows which methods to register as tools.
* :func:`toolset` — class-level marker. Attaches
  ``__maivn_toolset__`` to the class so registration knows the prefix, the
  shared tags, and whether to require the per-method marker.

Both decorators support bare and parameterized call sites:

.. code-block:: python

    @toolify
    def some_method(self, ...): ...

    @toolify(permissions=PermissionSet(PermissionFlag.WRITE), destructive=False)
    def write_method(self, ...): ...

    @toolset
    class MyToolset: ...

    @toolset(prefix="github", tags=["developer"])
    class GitHubToolset: ...

The actual registration logic that walks ``@toolset``-decorated instances and
produces :class:`MethodTool` objects lives in
:mod:`maivn._internal.core.services.toolify_instance`.
"""

# pyright: strict
from __future__ import annotations

import re
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from typing import TypeAlias, TypeVar, cast, overload

from pydantic import JsonValue

# MARK: - Types

JsonObject: TypeAlias = dict[str, JsonValue]
ToolExecutionCallback: TypeAlias = Callable[[dict[str, object]], object]

# MARK: - Attribute keys

TOOLIFY_ATTR = "__maivn_toolify__"
"""Attribute key set by :func:`toolify` on the underlying function."""

TOOLSET_ATTR = "__maivn_toolset__"
"""Attribute key set by :func:`toolset` on the decorated class."""


# MARK: - Options dataclasses


@dataclass(frozen=True)
class MethodToolifyOptions:
    """Per-method options collected by :func:`toolify`.

    Mirrors the kwargs of the existing free-callable ``@scope.toolify(...)``
    decorator, with two connector-aware additions:

    * ``permissions`` — opaque permission payload (e.g. a
      ``PermissionSet`` from ``maivn-shared``). At registration time the
      SDK flattens it to the canonical list of flag-name strings on the
      tool's ``metadata["permissions"]`` so the metadata dict stays
      JSON-serializable for transport / persistence. Hosts that need
      the rich type can reconstruct one with ``PermissionSet.from_names``.
    * ``destructive`` — a boolean fast-path mirror of the same intent.
      Convenience for hosts that only need to gate destructive tools.

    Hosts that supply their own permission model can ignore both fields and
    use ``metadata`` directly.
    """

    name: str | None = None
    description: str | None = None
    permissions: object | None = None
    destructive: bool = False
    always_execute: bool = False
    final_tool: bool = False
    metadata: JsonObject = field(default_factory=dict)
    tags: tuple[str, ...] = ()
    before_execute: ToolExecutionCallback | None = None
    after_execute: ToolExecutionCallback | None = None


@dataclass(frozen=True)
class ToolsetOptions:
    """Class-level options collected by :func:`toolset`.

    Attributes:
        prefix: Prefix prepended to each method-tool's resolved name. When
            ``None`` the class name is converted to ``snake_case`` and used
            (``GitHubToolset`` -> ``"git_hub_toolset"``).
        tags: Tags applied to every method-tool the toolset produces. Merged
            with per-method tags supplied via :func:`toolify`.
        require_marker: When ``True`` (the default) only methods decorated
            with :func:`toolify` are registered. When ``False`` every
            non-underscore public method is registered — useful for
            grandfathering legacy classes; discouraged for new code.
        metadata: Free-form, JSON-serializable extras attached to every
            method-tool the toolset produces.
    """

    prefix: str | None = None
    tags: tuple[str, ...] = ()
    require_marker: bool = True
    metadata: JsonObject = field(default_factory=dict)


# MARK: - @toolify


T_Method = TypeVar("T_Method", bound=Callable[..., object])


@overload
def toolify(func: T_Method) -> T_Method: ...


@overload
def toolify(
    *,
    name: str | None = None,
    description: str | None = None,
    permissions: object | None = None,
    destructive: bool = False,
    always_execute: bool = False,
    final_tool: bool = False,
    metadata: Mapping[str, JsonValue] | None = None,
    tags: list[str] | tuple[str, ...] | None = None,
    before_execute: ToolExecutionCallback | None = None,
    after_execute: ToolExecutionCallback | None = None,
) -> Callable[[T_Method], T_Method]: ...


def toolify(
    func: T_Method | None = None,
    *,
    name: str | None = None,
    description: str | None = None,
    permissions: object | None = None,
    destructive: bool = False,
    always_execute: bool = False,
    final_tool: bool = False,
    metadata: Mapping[str, JsonValue] | None = None,
    tags: list[str] | tuple[str, ...] | None = None,
    before_execute: ToolExecutionCallback | None = None,
    after_execute: ToolExecutionCallback | None = None,
) -> T_Method | Callable[[T_Method], T_Method]:
    """Mark an instance method as a tool that ``scope.add_toolset`` should register.

    Use the bare form ``@toolify`` to accept the defaults, or the
    parameterized form ``@toolify(...)`` to override one or more options.
    The decorator is a no-op at runtime; it only attaches
    :data:`TOOLIFY_ATTR` to the underlying function so the toolset walker
    can find it.

    The decorator coexists with the existing dependency decorators
    (``@depends_on_tool``, ``@depends_on_agent``, ``@compose_artifact_policy``,
    etc.). Stack them in any order — each attaches its own attribute and the
    eventual ``MethodTool`` reads all of them via the existing
    :class:`DependencyCollector`.

    Example:

    .. code-block:: python

        class GitHubToolset:
            @toolify
            def get_user(self) -> dict:
                "Return the authenticated user."
                ...

            @toolify(permissions=PermissionSet(PermissionFlag.WRITE))
            def create_issue(self, owner: str, repo: str, title: str) -> dict:
                "Open a new issue."
                ...
    """
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

    def apply(target: T_Method) -> T_Method:
        _validate_toolify_target(target)
        setattr(target, TOOLIFY_ATTR, options)
        return target

    if func is None:
        # Parameterized form: @toolify(...)
        return apply
    # Bare form: @toolify
    return apply(func)


# MARK: - @toolset


T_Class = TypeVar("T_Class", bound=type[object])


@overload
def toolset(cls: T_Class) -> T_Class: ...


@overload
def toolset(
    *,
    prefix: str | None = None,
    tags: list[str] | tuple[str, ...] | None = None,
    require_marker: bool = True,
    metadata: Mapping[str, JsonValue] | None = None,
) -> Callable[[T_Class], T_Class]: ...


def toolset(
    cls: T_Class | None = None,
    *,
    prefix: str | None = None,
    tags: list[str] | tuple[str, ...] | None = None,
    require_marker: bool = True,
    metadata: Mapping[str, JsonValue] | None = None,
) -> T_Class | Callable[[T_Class], T_Class]:
    """Mark a class as a toolset for :meth:`BaseScope.add_toolset`.

    The decorator is declarative — it attaches :data:`TOOLSET_ATTR` to the
    class but does **not** register any tools at class-definition time
    (no instance exists yet). Registration happens later via
    ``scope.add_toolset(instance)``.

    Args:
        prefix: Tool-name prefix. Defaults to the class name converted to
            ``snake_case``.
        tags: Tags applied to every method-tool the toolset produces.
        require_marker: When ``True`` (default), only methods decorated
            with :func:`toolify` are registered. When ``False``, every
            non-underscore public method is registered.
        metadata: Free-form metadata merged into every method-tool.

    Example:

    .. code-block:: python

        @toolset(prefix="github")
        class GitHubToolset:
            def __init__(self, token: str): ...

            @toolify
            def get_user(self) -> dict: ...

            @toolify(permissions=PermissionSet(PermissionFlag.WRITE))
            def create_issue(self, ...) -> dict: ...

        agent.add_toolset(GitHubToolset(token=...))
    """
    options = ToolsetOptions(
        prefix=prefix,
        tags=tuple(tags or ()),
        require_marker=require_marker,
        metadata=dict(metadata or {}),
    )

    def apply(target: T_Class) -> T_Class:
        _validate_toolset_target(target)
        setattr(target, TOOLSET_ATTR, options)
        return target

    if cls is None:
        # Parameterized form: @toolset(...)
        return apply
    # Bare form: @toolset
    return apply(cls)


# MARK: - Helpers


_SNAKE_CASE_ACRONYM = re.compile(r"([A-Z]{2,})([A-Z][a-z])")
_SNAKE_CASE_WORD_BOUNDARY = re.compile(r"([a-z0-9])([A-Z])")


def derive_default_prefix(cls: type) -> str:
    """Convert a class name to ``snake_case`` for use as a default prefix.

    ``GitHubToolset`` -> ``"git_hub_toolset"``,
    ``OAuth2Provider`` -> ``"oauth2_provider"``,
    ``SmtpSender`` -> ``"smtp_sender"``.
    """
    name = cls.__name__
    intermediate = _SNAKE_CASE_ACRONYM.sub(r"\1_\2", name)
    return _SNAKE_CASE_WORD_BOUNDARY.sub(r"\1_\2", intermediate).lower()


def get_toolify_options(method: Callable[..., object]) -> MethodToolifyOptions | None:
    """Return the :class:`MethodToolifyOptions` set by :func:`toolify`, if any.

    Bound methods proxy attribute lookups to the underlying function, so
    callers can pass either a bound method or a plain function.
    """
    return cast(MethodToolifyOptions | None, getattr(method, TOOLIFY_ATTR, None))


def get_toolset_options(cls: type) -> ToolsetOptions | None:
    """Return the :class:`ToolsetOptions` set by :func:`toolset`, if any."""
    return cast(ToolsetOptions | None, getattr(cls, TOOLSET_ATTR, None))


def _validate_toolify_target(target: object) -> None:
    if not callable(target):
        raise TypeError("@toolify can only decorate callables")


def _validate_toolset_target(target: object) -> None:
    if not isinstance(target, type):
        raise TypeError("@toolset can only decorate classes")


__all__ = [
    "MethodToolifyOptions",
    "TOOLIFY_ATTR",
    "TOOLSET_ATTR",
    "ToolsetOptions",
    "derive_default_prefix",
    "get_toolify_options",
    "get_toolset_options",
    "toolify",
    "toolset",
]
