"""Protocols for orchestrator-facing API objects."""

# pyright: strict
from __future__ import annotations

from collections.abc import Sequence
from typing import Protocol

from maivn_shared import SessionClientProtocol

from maivn._internal.core.entities.tools import BaseTool

# MARK: Agent And Swarm Protocols


class OrchestratedSwarm(Protocol):
    """Minimal Swarm shape the orchestrator needs."""

    @property
    def agents(self) -> Sequence[OrchestratedAgent]: ...

    @property
    def name(self) -> str | None: ...

    def compile_tools(self) -> Sequence[BaseTool]: ...

    def list_tools(self) -> Sequence[BaseTool]: ...


class OrchestratedAgent(Protocol):
    """Minimal Agent shape the orchestrator needs."""

    @property
    def id(self) -> str: ...

    @property
    def name(self) -> str | None: ...

    @property
    def client(self) -> SessionClientProtocol | None: ...

    @property
    def api_key(self) -> str | None: ...

    @property
    def timeout(self) -> float | None: ...

    @property
    def max_results(self) -> int | None: ...

    @property
    def description(self) -> str | None: ...

    def compile_tools(self) -> Sequence[BaseTool]: ...

    def get_swarm(self) -> OrchestratedSwarm | None: ...

    def list_tools(self) -> Sequence[BaseTool]: ...


__all__ = ["OrchestratedAgent", "OrchestratedSwarm"]
