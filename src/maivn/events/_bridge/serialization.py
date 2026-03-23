"""Serialization helpers for bridge SSE payloads."""

from __future__ import annotations

import json
import logging
from typing import Any

logger = logging.getLogger("maivn.events._bridge")


# MARK: JSON Helpers


def safe_json_dumps(payload: dict[str, Any]) -> str:
    """Serialize payloads without breaking the SSE stream."""
    try:
        return json.dumps(payload, default=str)
    except Exception:
        logger.exception("Failed to serialize SSE payload")
        return json.dumps({"event": "error", "message": "Failed to serialize event payload"})
