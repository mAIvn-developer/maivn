"""UI event value object for EventBridge."""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from .serialization import build_safe_event_payload

# MARK: UIEvent


@dataclass
class UIEvent:
    """A single event to be delivered to the frontend via SSE."""

    type: str
    data: dict[str, Any]
    id: str = ""
    timestamp: str = ""

    def __post_init__(self) -> None:
        if not self.id:
            self.id = str(uuid.uuid4())
        if not self.timestamp:
            self.timestamp = datetime.now(timezone.utc).isoformat()

    def to_sse(self) -> dict[str, Any]:
        """Build an ``EventSourceResponse``-compatible payload."""
        payload = {
            "id": self.id,
            "type": self.type,
            "data": self.data,
            "timestamp": self.timestamp,
        }
        return {
            "event": self.type,
            "id": self.id,
            "data": build_safe_event_payload(
                payload,
                event_id=self.id,
                event_type=self.type,
                timestamp=self.timestamp,
            ),
        }

    def to_dict(self) -> dict[str, Any]:
        """Serialize for history/snapshot APIs."""
        return {
            "id": self.id,
            "type": self.type,
            "data": self.data,
            "timestamp": self.timestamp,
        }


__all__ = ["UIEvent"]
