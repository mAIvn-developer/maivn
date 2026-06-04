"""Registry for EventBridge instances."""

# pyright: strict
from __future__ import annotations

from collections.abc import Callable
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .bridge import EventBridge


# MARK: BridgeRegistry


class BridgeRegistry:
    """Manages a collection of EventBridge instances keyed by session ID."""

    def __init__(self) -> None:
        self._bridges: dict[str, EventBridge] = {}

    def get(self, session_id: str) -> EventBridge | None:
        """Get an event bridge by session ID."""
        return self._bridges.get(session_id)

    def create(
        self,
        session_id: str,
        *,
        factory: Callable[[str], EventBridge] | None = None,
    ) -> EventBridge:
        """Create a new event bridge for a session."""
        from .bridge import EventBridge

        if session_id in self._bridges:
            self._bridges[session_id].close()
        bridge = factory(session_id) if factory else EventBridge(session_id)
        self._bridges[session_id] = bridge
        return bridge

    def remove(self, session_id: str) -> None:
        """Remove and close an event bridge."""
        bridge = self._bridges.pop(session_id, None)
        if bridge is not None:
            bridge.close()

    def clear(self) -> None:
        """Remove and close every registered event bridge.

        Iterates a snapshot of the registered session IDs and applies
        :meth:`remove` to each, so ``close()`` runs for every bridge and the
        registry ends up empty.
        """
        for session_id in list(self._bridges):
            self.remove(session_id)


__all__ = ["BridgeRegistry"]
