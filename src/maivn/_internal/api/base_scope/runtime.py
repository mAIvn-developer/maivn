"""Initialization helpers for ``BaseScope``."""

from __future__ import annotations

from typing import Any

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

# MARK: Scope Initialization


class BaseScopeInitializationMixin:
    name: str | None
    system_prompt: str | SystemMessage | None
    _tool_repo: ToolRepoInterface
    _dependency_repo: DependencyRepoInterface
    _tool_registrar: ToolRegistrar
    _resolver: ScopeResolverInterface
    _toolify_service: ToolifyService
    _system_message: SystemMessage | None
    _mcp_registry: McpRegistry

    def model_post_init(self, context: Any) -> None:
        """Initialize services and name after Pydantic construction."""
        _ = context
        if self.name is None:
            self.name = self.__class__.__name__

        self._tool_repo = getattr(self, "tool_repo", None) or ToolRepo()
        self._dependency_repo = getattr(self, "dependency_repo", None) or DependencyRepo()
        self._init_system_message(self.system_prompt)
        self._init_services(self._tool_repo, self._dependency_repo, getattr(self, "resolver", None))

    def _init_system_message(self, system_prompt: str | SystemMessage | None) -> None:
        if isinstance(system_prompt, str):
            self._system_message = SystemMessage(content=system_prompt)
        elif isinstance(system_prompt, SystemMessage):
            self._system_message = system_prompt
        else:
            self._system_message = None

    def _init_services(
        self,
        tool_repo: ToolRepoInterface | None,
        dependency_repo: DependencyRepoInterface | None,
        resolver: ScopeResolverInterface | None,
    ) -> None:
        self._tool_repo = tool_repo or ToolRepo()
        self._dependency_repo = dependency_repo or DependencyRepo()
        self._tool_registrar = ToolRegistrar(repo=self._tool_repo)
        self._resolver = resolver or NoOpScopeResolver()
        self._toolify_service = ToolifyService()
        self._mcp_registry = McpRegistry(self)
        self._init_resolver_context()

    def _init_resolver_context(self) -> None:
        try:
            self._resolver.set_context(scope=self)
        except (NotImplementedError, AttributeError):
            pass
