# pyright: strict
from __future__ import annotations

from pydantic import BaseModel, Field, JsonValue

# MARK: - SSE Entities


class SSEEvent(BaseModel):
    """Representation of a server-sent event.

    This wire DTO intentionally keeps BaseModel assignment behavior; enabling
    ConfigurableMixin would add post-construction assignment validation.
    """

    name: str = Field(..., description="Event name from the SSE stream")
    payload: JsonValue = Field(default_factory=dict, description="Decoded event payload")
