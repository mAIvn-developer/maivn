"""Normalization configuration shared across event handlers."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

# MARK: Options


@dataclass(frozen=True)
class NormalizationOptions:
    default_agent_name: str | None = None
    default_swarm_name: str | None = None
    default_participant_key: str | None = None
    default_participant_name: str | None = None
    default_participant_role: str | None = None
    assignment_name_map: dict[str, str] | None = None
    tool_name_map: dict[str, str] | None = None
    tool_metadata_map: dict[str, dict[str, Any]] | None = None

    def participant_kwargs(self) -> dict[str, str | None]:
        return {
            "participant_key": self.default_participant_key,
            "participant_name": self.default_participant_name,
            "participant_role": self.default_participant_role,
        }
