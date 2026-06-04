# pyright: strict
"""Dependency decorator package exports."""

from __future__ import annotations

from ._types import ArgPolicy, ComposeArtifactApproval, ComposeArtifactMode
from .artifact_policy import compose_artifact_policy
from .dependencies import depends_on_agent, depends_on_private_data, depends_on_tool
from .execution_control import depends_on_await_for, depends_on_reevaluate
from .interrupt import depends_on_interrupt

# MARK: Public API

__all__ = [
    "ArgPolicy",
    "ComposeArtifactApproval",
    "ComposeArtifactMode",
    "compose_artifact_policy",
    "depends_on_agent",
    "depends_on_await_for",
    "depends_on_interrupt",
    "depends_on_private_data",
    "depends_on_reevaluate",
    "depends_on_tool",
]
