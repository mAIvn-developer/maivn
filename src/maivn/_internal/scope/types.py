"""Shared type aliases, constants, and value objects for the scope authoring facade."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Literal, TypeAlias

from maivn._internal.compat.options import ModelChoice, ModelConfig
from maivn._internal.compat.tooling import ToolTarget
from maivn._internal.models import ToolMetadata
from maivn._internal.skills import Skill

DEFAULT_MODEL = 'auto'
MODEL_DIRECTIVES: frozenset[str] = frozenset(('auto', 'fast', 'balanced', 'max', 'ultra', 'force'))

ToolSpec: TypeAlias = ToolMetadata | ToolTarget
SkillSpec: TypeAlias = Skill | Mapping[str, object]
ResourceSpec: TypeAlias = dict[str, object]
RuntimeModelChoice: TypeAlias = str | ModelChoice | ModelConfig
HookExecutionMode: TypeAlias = Literal['tool', 'scope', 'agent']
ReasoningEffort: TypeAlias = Literal['minimal', 'low', 'medium', 'high']
ScopeResourceBindingType: TypeAlias = Literal['agent', 'swarm']


@dataclass(frozen=True, slots=True)
class AutoSkills:
    query: str
    limit: int
