"""Interpret caller-authored tool policy without accepting model-supplied grants.

Privileged tools default to denial. Set tool metadata ``approval_policy='ask'``
and ``approval_handler`` to a boolean input callback for durable human approval.
``approval_policy='allow'`` explicitly opts this tool out of the approval guard;
the declaration crosses the wire. Child tools each retain their own policy.
"""

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
            {
                key: metadata[key]
                for key in ('permissions', 'destructive', 'approval_policy')
                if key in metadata
            }
        )
    except ValidationError:
        return ToolSafetyLabels(destructive=True)
