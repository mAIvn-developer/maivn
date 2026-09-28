"""Tool and assistant forwarding mixins for the event router."""

from __future__ import annotations

import inspect
from typing import TYPE_CHECKING

from maivn._internal.reporting.terminal_reporter.event_categories import (
    resolve_tool_category,
    resolve_tool_category_from_event_id,
)

if TYPE_CHECKING:
    from collections.abc import Callable, Mapping

    from maivn._internal.reporting.terminal_reporter.event_router._protocols import RoutedReporter


RouterPayload = dict[str, object]


class ToolRouterMixin:
    """Route legacy tool lifecycle callbacks."""

    _reporter: RoutedReporter
    _tool_category_by_event_id: dict[str, str]

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

    def report_tool_start(  # noqa: PLR0913 - one parameter per tool-start field.
        self,
        tool_name: str,
        event_id: str,
        tool_type: str | None = None,
        agent_name: str | None = None,
        tool_args: dict[str, object] | None = None,
        swarm_name: str | None = None,
        private_data_keys: list[str] | None = None,
    ) -> None:
        """Forward a tool start and retain its category for terminal callbacks."""
        category = resolve_tool_category(tool_type)
        self._tool_category_by_event_id[str(event_id)] = category
        self._forward(
            category=category,
            event_name='tool_start',
            payload={
                'tool_name': tool_name,
                'event_id': event_id,
                'tool_type': tool_type,
                'agent_name': agent_name,
                'tool_args': tool_args,
                'swarm_name': swarm_name,
                'private_data_keys': private_data_keys,
            },
            forward=lambda: self._reporter.report_tool_start(
                tool_name,
                event_id,
                tool_type,
                agent_name,
                tool_args,
                swarm_name,
                private_data_keys,
            ),
        )

    def report_tool_complete(
        self,
        event_id: str,
        elapsed_ms: int | None = None,
        result: object | None = None,
    ) -> None:
        """Forward a tool completion using the category stored at tool start."""
        event_key = str(event_id)
        category = resolve_tool_category_from_event_id(event_key, self._tool_category_by_event_id)
        self._tool_category_by_event_id.pop(event_key, None)
        self._forward(
            category=category,
            event_name='tool_complete',
            payload={'event_id': event_id, 'elapsed_ms': elapsed_ms, 'result': result},
            forward=lambda: self._reporter.report_tool_complete(event_id, elapsed_ms, result),
        )

    def report_tool_error(
        self,
        tool_name: str,
        error: str,
        event_id: str | None = None,
        elapsed_ms: int | None = None,
    ) -> None:
        """Forward a tool error using a stored category when an id is available."""
        event_key = str(event_id).strip() if event_id is not None else ''
        category = resolve_tool_category_from_event_id(event_key, self._tool_category_by_event_id)
        if not event_key:
            category = resolve_tool_category(None)
        else:
            self._tool_category_by_event_id.pop(event_key, None)
        self._forward(
            category=category,
            event_name='tool_error',
            payload={
                'tool_name': tool_name,
                'error': error,
                'event_id': event_id,
                'elapsed_ms': elapsed_ms,
            },
            forward=lambda: self._reporter.report_tool_error(
                tool_name,
                error,
                event_id,
                elapsed_ms,
            ),
        )

    def report_model_tool_complete(
        self,
        tool_name: str,
        event_id: str | None = None,
        agent_name: str | None = None,
        swarm_name: str | None = None,
        result: object | None = None,
    ) -> None:
        """Forward a model-tool completion and clear its temporary category entry."""
        event_key = str(event_id).strip() if event_id is not None else ''
        if event_key:
            self._tool_category_by_event_id[event_key] = 'model'
        try:
            self._forward(
                category='model',
                event_name='model_tool_complete',
                payload={
                    'tool_name': tool_name,
                    'event_id': event_id,
                    'agent_name': agent_name,
                    'swarm_name': swarm_name,
                    'result': result,
                },
                forward=lambda: self._reporter.report_model_tool_complete(
                    tool_name,
                    event_id=event_id,
                    agent_name=agent_name,
                    swarm_name=swarm_name,
                    result=result,
                ),
            )
        finally:
            if event_key:
                self._tool_category_by_event_id.pop(event_key, None)

    def report_hook_fired(  # noqa: PLR0913 - one parameter per hook-fired field.
        self,
        *,
        name: str,
        stage: str,
        status: str,
        target_type: str,
        target_id: str | None = None,
        target_name: str | None = None,
        source: str | None = None,
        error: str | None = None,
        elapsed_ms: int | None = None,
    ) -> None:
        """Forward a hook notification while supporting pre-source reporter signatures."""
        self._forward(
            category='lifecycle',
            event_name='hook_fired',
            payload={
                'name': name,
                'stage': stage,
                'status': status,
                'target_type': target_type,
                'target_id': target_id,
                'target_name': target_name,
                'source': source,
                'error': error,
                'elapsed_ms': elapsed_ms,
            },
            forward=lambda: _forward_hook_fired_to_reporter(
                self._reporter,
                name=name,
                stage=stage,
                status=status,
                target_type=target_type,
                target_id=target_id,
                target_name=target_name,
                source=source,
                error=error,
                elapsed_ms=elapsed_ms,
            ),
        )


def _forward_hook_fired_to_reporter(  # noqa: PLR0913 - forwards the hook-fired fields verbatim.
    reporter: RoutedReporter,
    *,
    name: str,
    stage: str,
    status: str,
    target_type: str,
    target_id: str | None,
    target_name: str | None,
    source: str | None,
    error: str | None,
    elapsed_ms: int | None,
) -> None:
    """Forward hook metadata, retrying only when a legacy reporter rejects ``source``."""
    try:
        reporter.report_hook_fired(
            name=name,
            stage=stage,
            status=status,
            target_type=target_type,
            target_id=target_id,
            target_name=target_name,
            source=source,
            error=error,
            elapsed_ms=elapsed_ms,
        )
    except TypeError as exception:
        message = str(exception)
        if 'unexpected keyword argument' not in message or 'source' not in message:
            raise
        reporter.report_hook_fired(
            name=name,
            stage=stage,
            status=status,
            target_type=target_type,
            target_id=target_id,
            target_name=target_name,
            error=error,
            elapsed_ms=elapsed_ms,
        )


class AssistantRouterMixin:
    """Route assistant chunks and status messages."""

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

    def report_response_chunk(
        self,
        text: str,
        *,
        assistant_id: str | None = None,
        full_text: str | None = None,
        replace_content: bool = False,
        private_value_restorations: list[dict[str, object]] | None = None,
    ) -> None:
        """Forward an incremental assistant-response chunk."""
        kwargs: dict[str, object] = {
            'assistant_id': assistant_id,
            'full_text': full_text,
            'replace_content': replace_content,
        }
        if private_value_restorations:
            params: Mapping[str, inspect.Parameter]
            try:
                params = inspect.signature(self._reporter.report_response_chunk).parameters
            except (TypeError, ValueError):
                params = {}
            if 'private_value_restorations' in params or any(
                param.kind == inspect.Parameter.VAR_KEYWORD for param in params.values()
            ):
                kwargs['private_value_restorations'] = private_value_restorations
        self._forward(
            category='response',
            event_name='response_chunk',
            payload={
                'text': text,
                'assistant_id': assistant_id,
                'full_text': full_text,
                'replace_content': replace_content,
                'private_value_restorations': private_value_restorations,
            },
            forward=lambda: self._reporter.report_response_chunk(text, **kwargs),
        )

    def report_status_message(self, message: str, *, assistant_id: str | None = None) -> None:
        """Forward a standalone status message."""
        self._forward(
            category='lifecycle',
            event_name='status_message',
            payload={'message': message, 'assistant_id': assistant_id},
            forward=lambda: self._reporter.report_status_message(
                message,
                assistant_id=assistant_id,
            ),
        )


__all__ = ['AssistantRouterMixin', 'ToolRouterMixin']
