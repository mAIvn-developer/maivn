"""Progress and input methods for ``RichReporter``."""

# pyright: strict
from __future__ import annotations

from abc import ABC
from collections.abc import Generator
from contextlib import AbstractContextManager, contextmanager
from typing import TYPE_CHECKING, cast

from rich.progress import TaskID
from typing_extensions import override

from ...base.interface import BaseReporterInterface
from .progress import ProgressManager
from .terminal_setup import InputHandler

# MARK: Progress and Input


class RichReporterProgressMixin(BaseReporterInterface, ABC):
    if TYPE_CHECKING:
        enabled: bool
        _terminal_lock: AbstractContextManager[bool]
        _progress_manager: ProgressManager
        _input_handler: InputHandler

    @contextmanager
    @override
    def live_progress(
        self,
        description: str = "Processing...",
    ) -> Generator[TaskID | None, None, None]:
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
                _ = progress_cm.__exit__(None, None, None)

    @override
    def update_progress(
        self,
        task_id: object,
        description: str | None = None,
    ) -> None:
        """Update progress description."""
        if not self.enabled or task_id is None:
            return

        with self._terminal_lock:
            self._progress_manager.update_progress(cast(TaskID, task_id), description)

    @contextmanager
    @override
    def prepare_for_user_input(self) -> Generator[None, None, None]:
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
                _ = input_cm.__exit__(None, None, None)

    @override
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
