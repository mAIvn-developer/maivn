"""Runtime helpers for EventBridge internals.

Submodules:
- helpers: shared coercion and payload merge helpers
- identity: canonical ID state and resolution logic
- normalization: known-event payload normalization for raw bridge packets
"""

# pyright: strict
from __future__ import annotations

# MARK: Public Exports

__all__: list[str] = []
