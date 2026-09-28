"""Raw server-sent event as it arrives off the wire."""

from __future__ import annotations

from pydantic import BaseModel, Field, JsonValue


class SSEEvent(BaseModel):
    """One decoded server-sent event, before any normalization.

    This is the wire DTO the event normalizers consume. It stays a plain BaseModel so an
    event can still be mutated after construction; the normalized `AppEvent` is the type
    that carries a contract.
    """

    name: str = Field(..., description='Event name from the SSE stream.')
    payload: JsonValue = Field(default_factory=dict, description='Decoded event payload.')


__all__ = ['SSEEvent']
