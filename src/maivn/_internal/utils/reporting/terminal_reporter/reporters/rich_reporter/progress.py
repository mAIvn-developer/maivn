"""Progress tracking for RichReporter.
Wraps rich Progress/Live for spinners and status updates.
"""

from __future__ import annotations

import time
from collections.abc import Iterator
from contextlib import contextmanager
from typing import TYPE_CHECKING

from rich.live import Live
from rich.progress import Progress, SpinnerColumn, TaskID, TextColumn

from ...config import PROGRESS_REFRESH_RATE

if TYPE_CHECKING:
    from rich.console import Console


# MARK: Progress Manager


class ProgressManager:
    """Manages live progress display with spinners."""

    def __init__(self, console: Console) -> None:
        """Initialize progress manager.

        Args:
            console: Rich console instance
        """
        self.console = console
        self.progress = Progress(
            SpinnerColumn(),
            TextColumn("[bold blue]{task.description}"),
            console=console,
            transient=True,
        )
        self._live: Live | None = None
        self._suspend_live_count = 0

    @property
    def live(self) -> Live | None:
        """Get current live display instance."""
        return self._live

    @contextmanager
    def live_progress(self, description: str = "Processing...") -> Iterator[TaskID | None]:
        """Context manager for live progress display.

        Args:
            description: Progress description

        Yields:
            Progress task ID
        """
        with Live(
            self.progress,
            console=self.console,
            refresh_per_second=PROGRESS_REFRESH_RATE,
            transient=True,  # This should auto-clear the spinner on exit
        ) as live:
            self._live = live
            task = self.progress.add_task(description, total=None)
            try:
                yield task
            finally:
                self._live = None
                self._suspend_live_count = 0
                self.progress.stop_task(task)
                self.progress.remove_task(task)
                # Print a newline to ensure the spinner line is fully cleared
                self.console.print()

    def update_progress(self, task_id: TaskID, description: str | None = None) -> None:
        """Update progress description.

        Args:
            task_id: Progress task ID
            description: New description
        """
        if task_id is None:
            return

        if description:
            self.progress.update(task_id, description=description)

    @contextmanager
    def prepare_for_user_input(self) -> Iterator[None]:
        """Pause live rendering so terminal input can be collected."""
        live = self._live
        if live is None:
            yield
            return

        # If the caller (or another subsystem) has already suspended live rendering,
        # don't flap stop/start for short-lived contexts.
        if self._suspend_live_count > 0:
            yield
            return

        live.stop()
        try:
            yield
        finally:
            # Small delay before restarting Live to avoid rapid stop/start cycling
            # which can cause terminal rendering glitches on Windows
            time.sleep(0.01)
            live.start(refresh=True)

    # MARK: - Live Suspension (System Tool Streaming)

    def suspend_live(self) -> None:
        """Suspend rich Live rendering until resumed.

        Used to avoid stop/start flapping during high-frequency streaming updates
        (e.g., system tool LLM streaming).
        """
        live = self._live
        if live is None:
            return

        if self._suspend_live_count == 0:
            live.stop()
        self._suspend_live_count += 1

    def resume_live(self) -> None:
        """Resume rich Live rendering after a prior suspend_live()."""
        live = self._live
        if live is None:
            self._suspend_live_count = 0
            return

        if self._suspend_live_count <= 0:
            self._suspend_live_count = 0
            return

        self._suspend_live_count -= 1
        if self._suspend_live_count == 0:
            live.start(refresh=True)
