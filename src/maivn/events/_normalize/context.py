"""Normalization configuration shared across event handlers."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, TypedDict

if TYPE_CHECKING:
    from maivn.events._models import JsonObject

# MARK: Options


class ParticipantKwargs(TypedDict):
    participant_key: str | None
    participant_name: str | None
    participant_role: str | None


@dataclass(frozen=True)
class NormalizationOptions:
    default_agent_name: str | None = None
    default_swarm_name: str | None = None
    default_participant_key: str | None = None
    default_participant_name: str | None = None
    default_participant_role: str | None = None
    assignment_name_map: dict[str, str] | None = None
    tool_name_map: dict[str, str] | None = None
    tool_metadata_map: dict[str, JsonObject] | None = None

    def participant_kwargs(self) -> ParticipantKwargs:
        return {
            'participant_key': self.default_participant_key,
            'participant_name': self.default_participant_name,
            'participant_role': self.default_participant_role,
        }
