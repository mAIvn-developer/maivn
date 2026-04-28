"""Bridge package exports.

Submodules:
- bridge: EventBridge, UIEvent, and BridgeRegistry implementations
- emitters: Typed payload emit helpers
- security: Audience-based payload sanitization
- serialization: SSE-safe JSON serialization helpers
- streaming: Event replay and live SSE streaming helpers
"""

from __future__ import annotations

from .bridge import (
    BackpressurePolicy,
    BridgeAudience,
    BridgeRegistry,
    EventBridge,
    EventBridgeSecurityPolicy,
    UIEvent,
)
from .schema import EventSchemaError, ValidationMode, validate_event
from .serialization import build_safe_event_payload, safe_json_dumps

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
