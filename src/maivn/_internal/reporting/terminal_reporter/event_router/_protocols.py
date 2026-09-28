"""Structural typing for reporters wrapped by the legacy event router."""

from __future__ import annotations

from typing import TYPE_CHECKING, Protocol

if TYPE_CHECKING:
    from collections.abc import Callable
    from contextlib import AbstractContextManager

    from maivn._internal.models import InvokeResponse, StreamEvent


class RoutedReporter(Protocol):
    """Expose the callback surface used by legacy structured invocations."""

    enabled: bool

    def report_event(self, event: StreamEvent) -> None:
        """Render a v2 stream event."""

    def report_response(self, response: InvokeResponse) -> None:
        """Render a v2 invocation response."""

    def report_enrichment(self, **kwargs: object) -> None:
        """Render a legacy enrichment event using the supported callback signature."""

    def live_progress(self, description: str = 'Processing...') -> AbstractContextManager[object]:
        """Create a context-managed progress task."""
        ...

    def prepare_for_user_input(self) -> AbstractContextManager[None]:
        """Prepare the reporter to collect interactive input."""
        ...

    def get_input(  # noqa: PLR0913 - input metadata is an established compatibility surface.
        self,
        prompt: str,
        *,
        input_type: str = 'text',
        choices: list[str] | None = None,
        data_key: str | None = None,
        arg_name: str | None = None,
        tool_name: str | None = None,
    ) -> str:
        """Collect input for a legacy structured invocation."""
        ...

    def __getattr__(self, name: str) -> Callable[..., None]:
        """Resolve legacy notification callbacks implemented by the wrapped reporter."""
        ...


__all__ = ['RoutedReporter']
