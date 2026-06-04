"""Display-oriented methods for ``RichReporter``."""

# pyright: strict
from __future__ import annotations

from abc import ABC
from contextlib import AbstractContextManager
from typing import TYPE_CHECKING

from typing_extensions import override

from ...base.defaults import ReporterDefaultEventsMixin
from ...base.interface import BaseReporterInterface
from .display import DisplayManager
from .progress import ProgressManager

if TYPE_CHECKING:
    from maivn_shared.utils.token_models import TokenUsage


# MARK: Display Methods


class RichReporterDisplayMixin(ReporterDefaultEventsMixin, BaseReporterInterface, ABC):
    if TYPE_CHECKING:
        enabled: bool
        _terminal_lock: AbstractContextManager[bool]
        _display_manager: DisplayManager
        _progress_manager: ProgressManager

        def _clear_assistant_stream_state(self) -> None: ...

    @override
    def print_header(self, title: str, subtitle: str = "") -> None:
        """Print a beautiful header."""
        if not self.enabled:
            return

        with self._terminal_lock:
            self._display_manager.print_header(title, subtitle)

    @override
    def print_section(self, title: str, style: str = "bold cyan") -> None:
        """Print a section header."""
        if not self.enabled:
            return

        with self._terminal_lock:
            with self._progress_manager.prepare_for_user_input():
                self._display_manager.print_section(title, style)

    @override
    def print_event(
        self,
        event_type: str,
        message: str,
        details: dict[str, object] | None = None,
    ) -> None:
        """Print an event with color coding."""
        if not self.enabled:
            return

        with self._terminal_lock:
            self._display_manager.print_event(event_type, message, details)

    @override
    def report_phase_change(self, phase: str) -> None:
        """Report phase change."""
        if not self.enabled:
            return

        with self._terminal_lock:
            self._display_manager.print_phase_change(phase)

    @override
    def print_summary(
        self,
        token_usage: TokenUsage | None = None,
    ) -> None:
        """Print execution summary."""
        if not self.enabled:
            return

        with self._terminal_lock:
            self._display_manager.print_summary(token_usage)

    @override
    def print_final_result(self, result: object) -> None:
        """Print final result in a copyable format."""
        if not self.enabled:
            return

        with self._terminal_lock:
            self._clear_assistant_stream_state()
            self._display_manager.print_final_result(result)

    @override
    def print_error_summary(self, error: str) -> None:
        """Print error summary."""
        if not self.enabled:
            return

        with self._terminal_lock:
            self._clear_assistant_stream_state()
            self._display_manager.print_error_summary(error)

    # MARK: - Enrichment Reporting

    @override
    def report_enrichment(
        self,
        *,
        phase: str,
        message: str,
        scope_id: str | None = None,
        scope_name: str | None = None,
        scope_type: str | None = None,
        memory: dict[str, object] | None = None,
        redaction: dict[str, object] | None = None,
        source: str | None = None,
        trigger_tool: str | None = None,
        target_tool: str | None = None,
        reevaluate_count: int | None = None,
        collected_count: int | None = None,
    ) -> None:
        """Render an enrichment event.

        ``reevaluate_accrued`` is rendered as a distinct cycle chip so it does
        not look identical to a regular tool call. ``source="dependency"`` is
        called out specifically (it's the deterministic ``@depends_on_reevaluate``
        boundary firing) so users can tell when the system synthesised the
        re-plan vs when the LLM asked for one.
        """
        if not self.enabled:
            return

        if phase == "reevaluate_accrued":
            with self._terminal_lock:
                self._print_reevaluate_accrued_chip(
                    message=message,
                    source=source,
                    trigger_tool=trigger_tool,
                    target_tool=target_tool,
                    reevaluate_count=reevaluate_count,
                    collected_count=collected_count,
                )
            return

        ReporterDefaultEventsMixin.report_enrichment(
            self,
            phase=phase,
            message=message,
            scope_id=scope_id,
            scope_name=scope_name,
            scope_type=scope_type,
            memory=memory,
            redaction=redaction,
        )

    def _print_reevaluate_accrued_chip(
        self,
        *,
        message: str,
        source: str | None,
        trigger_tool: str | None,
        target_tool: str | None,
        reevaluate_count: int | None,
        collected_count: int | None,
    ) -> None:
        from rich.text import Text

        if source == "dependency" and trigger_tool and target_tool:
            text = Text()
            _ = text.append("[REEVAL] ", style="bold magenta")
            _ = text.append(
                f"@depends_on_reevaluate satisfied: {trigger_tool} -> {target_tool}",
                style="magenta",
            )
            if reevaluate_count is not None:
                _ = text.append(f"  (cycle {reevaluate_count})", style="dim")
        else:
            text = Text()
            _ = text.append("[REEVAL] ", style="bold magenta")
            _ = text.append(message or "Reevaluating with collected results", style="magenta")
            details: list[str] = []
            if reevaluate_count is not None:
                details.append(f"cycle {reevaluate_count}")
            if collected_count is not None:
                details.append(f"{collected_count} result{'s' if collected_count != 1 else ''}")
            if details:
                _ = text.append(f"  ({', '.join(details)})", style="dim")

        self._display_manager.console.print(text, overflow="fold")
