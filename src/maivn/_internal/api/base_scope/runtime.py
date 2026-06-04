"""Initialization helpers for ``BaseScope``."""

# pyright: strict
from __future__ import annotations

from typing import Protocol, cast

from maivn_shared import SystemMessage

from maivn._internal.adapters.repositories import DependencyRepo, ToolRepo
from maivn._internal.core.interfaces.repositories import (
    DependencyRepoInterface,
    ToolRepoInterface,
)
from maivn._internal.core.interfaces.resolvers import ScopeResolverInterface
from maivn._internal.core.registrars import ToolRegistrar
from maivn._internal.core.resolvers import NoOpScopeResolver
from maivn._internal.core.services.toolify import ToolifyService

from .mcp import McpRegistry

# MARK: Types


class _InitializableScope(Protocol):
    name: str | None
    system_prompt: str | SystemMessage | None


# MARK: Scope Initialization


class BaseScopeInitializationMixin:
    def model_post_init(self, context: object) -> None:
        """Initialize services and name after Pydantic construction."""
        _ = context
        scope = cast(_InitializableScope, cast(object, self))
        if scope.name is None:
            scope.name = self.__class__.__name__

        tool_repo = cast(ToolRepoInterface | None, getattr(self, "tool_repo", None))
        dependency_repo = cast(
            DependencyRepoInterface | None,
            getattr(self, "dependency_repo", None),
        )
        resolver = cast(ScopeResolverInterface | None, getattr(self, "resolver", None))
        resolved_tool_repo = tool_repo or ToolRepo()
        resolved_dependency_repo = dependency_repo or DependencyRepo()
        setattr(self, "_tool_repo", resolved_tool_repo)  # noqa: B010 - Pydantic PrivateAttr.
        setattr(self, "_dependency_repo", resolved_dependency_repo)  # noqa: B010 - Pydantic PrivateAttr.
        self._init_system_message(scope.system_prompt)
        self._init_services(resolved_tool_repo, resolved_dependency_repo, resolver)

    def set_system_message(self, system_prompt: str | SystemMessage | None) -> None:
        """Re-point this scope's resolved system message.

        Public API shared by :class:`Agent` and :class:`Swarm`. Normalizes
        ``system_prompt`` (``str`` -> :class:`SystemMessage`, ``None`` -> cleared)
        using the same logic applied at construction time, so callers no longer
        need to reach into the private ``_system_message`` PrivateAttr.
        """
        self._init_system_message(system_prompt)

    def _init_system_message(self, system_prompt: str | SystemMessage | None) -> None:
        if isinstance(system_prompt, str):
            system_message = SystemMessage(content=system_prompt)
            setattr(self, "_system_message", system_message)  # noqa: B010 - Pydantic PrivateAttr.
        elif isinstance(system_prompt, SystemMessage):
            setattr(self, "_system_message", system_prompt)  # noqa: B010 - Pydantic PrivateAttr.
        else:
            setattr(self, "_system_message", None)  # noqa: B010 - Pydantic PrivateAttr.

    def _init_services(
        self,
        tool_repo: ToolRepoInterface | None,
        dependency_repo: DependencyRepoInterface | None,
        resolver: ScopeResolverInterface | None,
    ) -> None:
        resolved_tool_repo = tool_repo or ToolRepo()
        resolved_dependency_repo = dependency_repo or DependencyRepo()
        resolved_resolver = resolver or NoOpScopeResolver()
        setattr(self, "_tool_repo", resolved_tool_repo)  # noqa: B010 - Pydantic PrivateAttr.
        setattr(self, "_dependency_repo", resolved_dependency_repo)  # noqa: B010 - Pydantic PrivateAttr.
        tool_registrar = ToolRegistrar(repo=resolved_tool_repo)
        setattr(self, "_tool_registrar", tool_registrar)  # noqa: B010 - Pydantic PrivateAttr.
        setattr(self, "_resolver", resolved_resolver)  # noqa: B010 - Pydantic PrivateAttr.
        setattr(self, "_toolify_service", ToolifyService())  # noqa: B010 - Pydantic PrivateAttr.
        setattr(self, "_mcp_registry", McpRegistry(self))  # noqa: B010 - Pydantic PrivateAttr.
        self._init_resolver_context()

    def _init_resolver_context(self) -> None:
        resolver = cast(
            ScopeResolverInterface,
            getattr(self, "_resolver"),  # noqa: B009 - Pydantic PrivateAttr.
        )
        try:
            resolver.set_context(scope=self)
        except (NotImplementedError, AttributeError):
            pass
