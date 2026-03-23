"""Bridge package exports.

Submodules:
- bridge: EventBridge, UIEvent, and BridgeRegistry implementations
- emitters: Typed payload emit helpers
- serialization: SSE-safe JSON serialization helpers
- streaming: Event replay and live SSE streaming helpers
"""

from __future__ import annotations

from .bridge import BridgeAudience, BridgeRegistry, EventBridge, EventBridgeSecurityPolicy, UIEvent
from .serialization import safe_json_dumps

__all__ = [
    "BridgeAudience",
    "BridgeRegistry",
    "EventBridge",
    "EventBridgeSecurityPolicy",
    "UIEvent",
    "safe_json_dumps",
]
