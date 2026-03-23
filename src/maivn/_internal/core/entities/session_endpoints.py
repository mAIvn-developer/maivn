from __future__ import annotations

from pydantic import BaseModel, Field

# MARK: - Session Entities


class SessionEndpoints(BaseModel):
    """Connection data returned when starting a session."""

    session_id: str = Field(..., description="Unique identifier for the session")
    assistant_id: str | None = Field(
        default=None,
        description="Assistant ID used as the server-side entrypoint for this session.",
    )
    events_url: str = Field(..., description="SSE endpoint for session events")
    resume_url: str = Field(..., description="Endpoint for submitting tool results")
