"""Bridge package exports.

Submodules:
- bridge: EventBridge implementation
- emitters: Typed payload emit helpers
- registry: BridgeRegistry implementation
- security: Audience-based payload sanitization
- serialization: SSE-safe JSON serialization helpers
- streaming: Event replay and live SSE streaming helpers
- ui_event: UIEvent value object
"""

from __future__ import annotations

from .bridge import (
    BackpressurePolicy,
    BridgeAudience,
    EventBridge,
    EventBridgeSecurityPolicy,
)
from .registry import BridgeRegistry
from .schema import EventSchemaError, ValidationMode, validate_event
from .serialization import build_safe_event_payload, safe_json_dumps
from .ui_event import UIEvent

__all__ = [
    "BackpressurePolicy",
    "BridgeAudience",
    "BridgeRegistry",
    "EventBridge",
    "EventBridgeSecurityPolicy",
    "EventSchemaError",
    "UIEvent",
    "ValidationMode",
    "build_safe_event_payload",
    "safe_json_dumps",
    "validate_event",
]
