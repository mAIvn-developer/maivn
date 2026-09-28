"""Display, progress, and input forwarding mixins for the event router."""

from __future__ import annotations

from contextlib import contextmanager
from typing import TYPE_CHECKING

from maivn._internal.reporting.terminal_reporter.event_categories import category_for_print_event

if TYPE_CHECKING:
    from collections.abc import Callable, Generator

    from maivn._internal.reporting.terminal_reporter.event_router._protocols import RoutedReporter
    from maivn._internal.token_usage import TokenUsage


RouterPayload = dict[str, object]


class DisplayRouterMixin:
    """Route legacy display callbacks by their event category."""

    _reporter: RoutedReporter

    def _forward(
        self,
        *,
        category: str,
        event_name: str,
        payload: RouterPayload,
        forward: Callable[[], None],
    ) -> None:
        """Forward a category-filtered callback through the concrete router."""
        _ = (category, event_name, payload, forward)
        message = 'EventRouterReporter must implement _forward'
        raise NotImplementedError(message)

    def print_header(self, title: str, subtitle: str = '') -> None:
        """Forward a terminal header."""
        self._forward(
            category='lifecycle',
            event_name='print_header',
            payload={'title': title, 'subtitle': subtitle},
            forward=lambda: self._reporter.print_header(title, subtitle),
        )

    def print_section(self, title: str, style: str = 'bold cyan') -> None:
        """Forward a terminal section heading."""
        self._forward(
            category='lifecycle',
            event_name='print_section',
            payload={'title': title, 'style': style},
            forward=lambda: self._reporter.print_section(title, style),
        )

    def print_event(
        self,
        event_type: str,
        message: str,
        details: dict[str, object] | None = None,
    ) -> None:
        """Forward a legacy display event using its resolved category."""
        category = category_for_print_event(event_type)
        self._forward(
            category=category,
            event_name='print_event',
            payload={'event_type': event_type, 'message': message, 'details': details},
            forward=lambda: self._reporter.print_event(event_type, message, details),
        )

    def print_summary(self, token_usage: TokenUsage | None = None) -> None:
        """Forward a terminal execution summary."""
        self._forward(
            category='lifecycle',
            event_name='summary',
            payload={'token_usage': token_usage},
            forward=lambda: self._reporter.print_summary(token_usage),
        )

    def print_final_result(self, result: object) -> None:
        """Forward a legacy final result."""
        self._forward(
            category='lifecycle',
            event_name='final_result',
            payload={'result': result},
            forward=lambda: self._reporter.print_final_result(result),
        )

    def print_final_response(self, response: str) -> None:
        """Forward a final assistant response."""
        self._forward(
            category='lifecycle',
            event_name='final_response',
            payload={'response': response},
            forward=lambda: self._reporter.print_final_response(response),
        )

    def print_error_summary(self, error: str) -> None:
        """Forward a terminal error summary."""
        self._forward(
            category='lifecycle',
            event_name='error_summary',
            payload={'error': error},
            forward=lambda: self._reporter.print_error_summary(error),
        )


class ProgressRouterMixin:
    """Route progress callbacks and transparently delegate interactive input."""

    _reporter: RoutedReporter

    def _is_enabled(self, category: str) -> bool:
        """Return whether the concrete router enables a category."""
        _ = category
        message = 'EventRouterReporter must implement _is_enabled'
        raise NotImplementedError(message)

    @contextmanager
    def live_progress(self, description: str = 'Processing...') -> Generator[object, None, None]:
        """Yield a progress task only when lifecycle events are enabled."""
        if not self._is_enabled('lifecycle'):
            yield None
            return
        with self._reporter.live_progress(description) as task:
            yield task

    def update_progress(self, task_id: object, description: str | None = None) -> None:
        """Forward a progress update only when lifecycle events are enabled."""
        if self._is_enabled('lifecycle'):
            self._reporter.update_progress(task_id, description)

    @contextmanager
    def prepare_for_user_input(self) -> Generator[None, None, None]:
        """Delegate input preparation without filtering user interaction."""
        with self._reporter.prepare_for_user_input():
            yield

    def get_input(  # noqa: PLR0913 - forwards the established reporter input metadata.
        self,
        prompt: str,
        *,
        input_type: str = 'text',
        choices: list[str] | None = None,
        data_key: str | None = None,
        arg_name: str | None = None,
        tool_name: str | None = None,
    ) -> str:
        """Collect input while retaining compatibility with pre-metadata reporters."""
        try:
            return self._reporter.get_input(
                prompt,
                input_type=input_type,
                choices=choices,
                data_key=data_key,
                arg_name=arg_name,
                tool_name=tool_name,
            )
        except TypeError as error:
            if not _is_legacy_input_signature_error(error):
                raise
            return self._reporter.get_input(prompt)


def _is_legacy_input_signature_error(error: TypeError) -> bool:
    """Return whether a reporter rejected one of the newer input metadata keywords."""
    message = str(error)
    tokens = ('input_type', 'choices', 'data_key', 'arg_name', 'tool_name')
    return 'unexpected keyword argument' in message and any(token in message for token in tokens)


__all__ = ['DisplayRouterMixin', 'ProgressRouterMixin']
