"""Progress and input methods for ``RichReporter``."""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any

from rich.progress import TaskID

# MARK: Progress and Input


class RichReporterProgressMixin:
    enabled: bool
    _terminal_lock: Any
    _progress_manager: Any
    _input_handler: Any

    @contextmanager
    def live_progress(
        self,
        description: str = "Processing...",
    ) -> Iterator[TaskID | None]:
        """Context manager for live progress display."""
        if not self.enabled:
            yield None
            return

        progress_cm = self._progress_manager.live_progress(description)
        with self._terminal_lock:
            task = progress_cm.__enter__()
        try:
            yield task
        finally:
            with self._terminal_lock:
                progress_cm.__exit__(None, None, None)

    def update_progress(
        self,
        task_id: TaskID,
        description: str | None = None,
    ) -> None:
        """Update progress description."""
        if not self.enabled or task_id is None:
            return

        with self._terminal_lock:
            self._progress_manager.update_progress(task_id, description)

    @contextmanager
    def prepare_for_user_input(self) -> Iterator[None]:
        """Pause live rendering so terminal input can be collected."""
        if not self.enabled:
            yield
            return

        input_cm = self._progress_manager.prepare_for_user_input()
        with self._terminal_lock:
            input_cm.__enter__()
        try:
            yield
        finally:
            with self._terminal_lock:
                input_cm.__exit__(None, None, None)

    def get_input(
        self,
        prompt: str,
        *,
        input_type: str = "text",
        choices: list[str] | None = None,
        data_key: str | None = None,
        arg_name: str | None = None,
    ) -> str:
        """Collect input from the terminal using prompt_toolkit."""
        _ = (input_type, choices, data_key, arg_name)
        if not self.enabled:
            return input(prompt)

        with self._terminal_lock:
            return self._input_handler.get_input(prompt, self._progress_manager.live)
