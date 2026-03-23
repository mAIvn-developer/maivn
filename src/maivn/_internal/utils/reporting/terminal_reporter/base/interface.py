"""Abstract reporter contract for terminal reporter implementations."""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Iterator
from contextlib import contextmanager
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from maivn_shared.utils.token_models import TokenUsage


class BaseReporterInterface(ABC):
    """Contract that all terminal reporter implementations must follow."""

    # MARK: Initialization

    @abstractmethod
    def __init__(self, enabled: bool = True) -> None:
        """Initialize the reporter."""

    # MARK: Display Methods

    @abstractmethod
    def print_header(self, title: str, subtitle: str = "") -> None:
        """Print a header section."""

    @abstractmethod
    def print_section(self, title: str, style: str = "bold cyan") -> None:
        """Print a section header."""

    @abstractmethod
    def print_event(
        self,
        event_type: str,
        message: str,
        details: dict[str, Any] | None = None,
    ) -> None:
        """Print an event message."""

    @abstractmethod
    def print_summary(self, token_usage: TokenUsage | None = None) -> None:
        """Print execution summary."""

    @abstractmethod
    def print_final_result(self, result: Any) -> None:
        """Print final result."""

    @abstractmethod
    def print_final_response(self, response: str) -> None:
        """Print the final assistant response text if available."""

    @abstractmethod
    def print_error_summary(self, error: str) -> None:
        """Print error summary."""

    # MARK: Progress Management

    @abstractmethod
    @contextmanager
    def live_progress(self, description: str = "Processing...") -> Iterator[Any]:
        """Context manager for live progress display."""
        yield

    @abstractmethod
    def update_progress(self, task_id: Any, description: str | None = None) -> None:
        """Update progress description."""

    # MARK: User Input

    @abstractmethod
    @contextmanager
    def prepare_for_user_input(self) -> Iterator[None]:
        """Prepare reporter for user input collection."""
        yield

    def get_input(
        self,
        prompt: str,
        *,
        input_type: str = "text",
        choices: list[str] | None = None,
        data_key: str | None = None,
        arg_name: str | None = None,
    ) -> str:
        """Collect input from the terminal."""
        _ = (input_type, choices, data_key, arg_name)
        return input(prompt)

    # MARK: Session Reporting

    @abstractmethod
    def report_session_start(self, session_id: str, assistant_id: str) -> None:
        """Report session start."""

    @abstractmethod
    def report_private_data(self, private_data: dict[str, Any]) -> None:
        """Report private data parameters."""

    @abstractmethod
    def report_phase_change(self, phase: str) -> None:
        """Report phase change."""

    # MARK: Tool Reporting

    @abstractmethod
    def report_tool_start(
        self,
        tool_name: str,
        event_id: str,
        tool_type: str | None = None,
        agent_name: str | None = None,
        tool_args: dict[str, Any] | None = None,
        swarm_name: str | None = None,
    ) -> None:
        """Report tool execution start."""

    @abstractmethod
    def report_tool_complete(
        self,
        event_id: str,
        elapsed_ms: int | None = None,
        result: Any | None = None,
    ) -> None:
        """Report tool execution completion."""

    @abstractmethod
    def report_tool_error(
        self,
        tool_name: str,
        error: str,
        event_id: str | None = None,
        elapsed_ms: int | None = None,
    ) -> None:
        """Report tool execution error."""

    @abstractmethod
    def report_model_tool_complete(
        self,
        tool_name: str,
        event_id: str | None = None,
        agent_name: str | None = None,
        swarm_name: str | None = None,
        result: Any | None = None,
    ) -> None:
        """Report model tool execution completion."""
