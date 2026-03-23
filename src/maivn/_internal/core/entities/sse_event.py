from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field

# MARK: - SSE Entities


class SSEEvent(BaseModel):
    """Representation of a server-sent event."""

    name: str = Field(..., description="Event name from the SSE stream")
    payload: Any = Field(default_factory=dict, description="Decoded event payload")
