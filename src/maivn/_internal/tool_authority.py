"""Interpret legacy registration bags consistently without granting privilege."""

from __future__ import annotations

from typing import TYPE_CHECKING

from maivn_contracts.tools.authorization import ToolSafetyLabels
from pydantic import ValidationError

if TYPE_CHECKING:
    from collections.abc import Mapping


def registration_safety_labels(metadata: Mapping[str, object]) -> ToolSafetyLabels:
    """Unknown labels become an explicit denied tool, preserving the rest of the invocation."""
    try:
        return ToolSafetyLabels.model_validate(
            {key: metadata[key] for key in ('permissions', 'destructive') if key in metadata}
        )
    except ValidationError:
        return ToolSafetyLabels(destructive=True)
