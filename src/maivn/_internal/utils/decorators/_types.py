# pyright: strict
"""Shared type aliases for dependency decorators."""

from __future__ import annotations

from collections.abc import Callable
from typing import Literal, TypeAlias, TypeVar

from maivn_shared import BaseDependency
from pydantic import BaseModel

from ...core.entities import BaseTool

# MARK: Types

DependencyT = TypeVar("DependencyT", bound=BaseDependency)
F = TypeVar("F", bound=Callable[..., object])
DecoratedObjectT = TypeVar("DecoratedObjectT", bound=Callable[..., object] | type[BaseModel])
ToolReference: TypeAlias = str | BaseTool | Callable[..., object]
InputHandler: TypeAlias = Callable[[str], object]
ModelKwargs: TypeAlias = dict[str, object]
ArgPolicy: TypeAlias = dict[str, str]
ComposeArtifactMode = Literal["forbid", "allow", "require"]
ComposeArtifactApproval = Literal["none", "explicit"]


__all__ = [
    "ArgPolicy",
    "ComposeArtifactApproval",
    "ComposeArtifactMode",
    "DecoratedObjectT",
    "DependencyT",
    "F",
    "InputHandler",
    "ModelKwargs",
    "ToolReference",
]
