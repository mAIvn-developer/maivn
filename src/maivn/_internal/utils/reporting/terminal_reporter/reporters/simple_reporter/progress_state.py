"""Progress state tracking for SimpleReporter system tool streaming."""

from __future__ import annotations

# MARK: Progress State


class SystemToolProgressState:
    """Tracks state for system tool progress reporting."""

    def __init__(self) -> None:
        self.last_event_id: str | None = None
        self.last_emit_at: float = 0.0
        self.last_chunk_count: int = 0
        self.text_by_event_id: dict[str, str] = {}

    def reset_for_event(self, event_id: str) -> None:
        """Reset state for a new event."""
        self.last_event_id = event_id
        self.last_emit_at = 0.0
        self.last_chunk_count = 0
        self.text_by_event_id.pop(event_id, None)

    def update_emit_state(self, now: float, chunk_count: int) -> None:
        """Update state after emitting progress."""
        self.last_emit_at = now
        self.last_chunk_count = int(chunk_count) if isinstance(chunk_count, int) else 0

    def get_text_delta(self, event_id: str, text: str) -> str:
        """Get delta text since last update."""
        clean = text.replace("\r", "")
        prev = self.text_by_event_id.get(event_id, "")

        if prev and clean.startswith(prev):
            delta = clean[len(prev) :]
        else:
            delta = clean

        self.text_by_event_id[event_id] = clean
        return delta

    def should_emit_progress(
        self,
        is_new_event: bool,
        now: float,
        chunk_count: int,
    ) -> bool:
        """Determine if progress message should be emitted."""
        if is_new_event:
            return True
        if (now - self.last_emit_at) >= 2.0:
            return True
        if chunk_count >= (self.last_chunk_count + 10):
            return True
        return False
