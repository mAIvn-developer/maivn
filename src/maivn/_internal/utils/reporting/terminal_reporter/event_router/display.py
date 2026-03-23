"""Display and progress forwarding mixin for EventRouterReporter."""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from typing import TYPE_CHECKING, Any

from ..event_categories import category_for_print_event

if TYPE_CHECKING:
    from maivn_shared.utils.token_models import TokenUsage


# MARK: Display Forwarding


class DisplayRouterMixin:
    _forward: Any
    _reporter: Any

    def print_header(self, title: str, subtitle: str = "") -> None:
        self._forward(
            category="lifecycle",
            event_name="print_header",
            payload={"title": title, "subtitle": subtitle},
            forward=lambda: self._reporter.print_header(title, subtitle),
        )

    def print_section(self, title: str, style: str = "bold cyan") -> None:
        self._forward(
            category="lifecycle",
            event_name="print_section",
            payload={"title": title, "style": style},
            forward=lambda: self._reporter.print_section(title, style),
        )

    def print_event(
        self,
        event_type: str,
        message: str,
        details: dict[str, Any] | None = None,
    ) -> None:
        category = category_for_print_event(event_type)
        self._forward(
            category=category,
            event_name="print_event",
            payload={
                "event_type": event_type,
                "message": message,
                "details": details,
            },
            forward=lambda: self._reporter.print_event(event_type, message, details),
        )

    def print_summary(self, token_usage: TokenUsage | None = None) -> None:
        self._forward(
            category="lifecycle",
            event_name="summary",
            payload={"token_usage": token_usage},
            forward=lambda: self._reporter.print_summary(token_usage),
        )

    def print_final_result(self, result: Any) -> None:
        self._forward(
            category="lifecycle",
            event_name="final_result",
            payload={"result": result},
            forward=lambda: self._reporter.print_final_result(result),
        )

    def print_final_response(self, response: str) -> None:
        self._forward(
            category="lifecycle",
            event_name="final_response",
            payload={"response": response},
            forward=lambda: self._reporter.print_final_response(response),
        )

    def print_error_summary(self, error: str) -> None:
        self._forward(
            category="lifecycle",
            event_name="error_summary",
            payload={"error": error},
            forward=lambda: self._reporter.print_error_summary(error),
        )


# MARK: Progress and Input


class ProgressRouterMixin:
    _is_enabled: Any
    _reporter: Any

    @contextmanager
    def live_progress(self, description: str = "Processing...") -> Iterator[Any]:
        if not self._is_enabled("lifecycle"):
            yield None
            return
        with self._reporter.live_progress(description) as task:
            yield task

    def update_progress(
        self,
        task_id: Any,
        description: str | None = None,
    ) -> None:
        if not self._is_enabled("lifecycle"):
            return
        self._reporter.update_progress(task_id, description)

    @contextmanager
    def prepare_for_user_input(self) -> Iterator[None]:
        with self._reporter.prepare_for_user_input():
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
        try:
            return self._reporter.get_input(
                prompt,
                input_type=input_type,
                choices=choices,
                data_key=data_key,
                arg_name=arg_name,
            )
        except TypeError:
            return self._reporter.get_input(prompt)
