"""Characterization tests for the category-filtering event router reporter.

Studio wraps its own reporter in ``EventRouterReporter`` to filter legacy
callbacks by category and, optionally, mirror them to a payload sink. These
tests pin which callbacks reach the wrapped reporter, with which arguments, and
which payloads reach the sink, for every category the router recognizes.
"""

from __future__ import annotations

import logging
from contextlib import contextmanager
from typing import TYPE_CHECKING

import pytest
from maivn_contracts.messages import Message

from maivn._internal.models import InvokeResponse, StreamEvent
from maivn._internal.reporting.terminal_reporter import BaseReporter, event_router

if TYPE_CHECKING:
    from collections.abc import Callable, Generator

EventRouterReporter = event_router.EventRouterReporter

Call = tuple[str, tuple[object, ...], dict[str, object]]

_VALID_TOKENS = (
    'agent, agent_assignment, agent_tool, all, assignment, assistant, enrichment, func, '
    'func_tool, function, lifecycle, mcp, mcp_tool, model, model_tool, response, stream, '
    'system, system_tool, tool, tools'
)


# Section: Fakes.


class _RecordingReporter(BaseReporter):
    """Record every callback the router forwards, whatever its name."""

    def __init__(self, *, enabled: bool = True) -> None:
        """Start with an empty call log."""
        super().__init__(enabled=enabled)
        self.calls: list[Call] = []

    def report_event(self, event: StreamEvent) -> None:
        """Record a forwarded stream event."""
        self.calls.append(('report_event', (event,), {}))

    def report_response(self, response: InvokeResponse) -> None:
        """Record a forwarded response."""
        self.calls.append(('report_response', (response,), {}))

    @contextmanager
    def live_progress(self, description: str = 'Processing...') -> Generator[object, None, None]:
        """Record progress entry and yield a recognizable task handle."""
        self.calls.append(('live_progress', (description,), {}))
        yield f'task:{description}'

    @contextmanager
    def prepare_for_user_input(self) -> Generator[None, None, None]:
        """Record input preparation."""
        self.calls.append(('prepare_for_user_input', (), {}))
        yield

    def get_input(  # noqa: PLR0913 - mirrors the reporter input surface.
        self,
        prompt: str,
        *,
        input_type: str = 'text',
        choices: list[str] | None = None,
        data_key: str | None = None,
        arg_name: str | None = None,
        tool_name: str | None = None,
    ) -> str:
        """Record the metadata-bearing input call and answer it."""
        self.calls.append(
            (
                'get_input',
                (prompt,),
                {
                    'input_type': input_type,
                    'choices': choices,
                    'data_key': data_key,
                    'arg_name': arg_name,
                    'tool_name': tool_name,
                },
            )
        )
        return 'answer'

    def __getattr__(self, name: str) -> Callable[..., None]:
        """Return a recorder for any other legacy callback name."""
        if name.startswith('_'):
            raise AttributeError(name)

        def record(*args: object, **kwargs: object) -> None:
            self.calls.append((name, args, kwargs))

        return record


class _MinimalReporter(BaseReporter):
    """Implement only the abstract reporter surface."""

    def __init__(self) -> None:
        """Start with an empty call log."""
        super().__init__(enabled=False)
        self.calls: list[str] = []

    def report_event(self, event: StreamEvent) -> None:
        """Record a forwarded stream event."""
        _ = event
        self.calls.append('report_event')

    def report_response(self, response: InvokeResponse) -> None:
        """Record a forwarded response."""
        _ = response
        self.calls.append('report_response')


class _LegacyInputReporter(_MinimalReporter):
    """Expose the pre-metadata ``get_input(prompt)`` signature."""

    def get_input(self, prompt: str) -> str:
        """Answer with the prompt echoed back."""
        return f'legacy:{prompt}'


class _BrokenInputReporter(_MinimalReporter):
    """Raise an unrelated ``TypeError`` from input collection."""

    def get_input(self, prompt: str, **kwargs: object) -> str:
        """Fail the way a buggy reporter would."""
        _ = (prompt, kwargs)
        message = 'prompt must be str'
        raise TypeError(message)


class _LegacyHookReporter(_MinimalReporter):
    """Expose the hook callback signature that predates ``source``."""

    def __init__(self) -> None:
        """Start with an empty hook log."""
        super().__init__()
        self.hooks: list[dict[str, object]] = []

    def report_hook_fired(  # noqa: PLR0913 - mirrors the legacy hook signature.
        self,
        *,
        name: str,
        stage: str,
        status: str,
        target_type: str,
        target_id: str | None = None,
        target_name: str | None = None,
        error: str | None = None,
        elapsed_ms: int | None = None,
    ) -> None:
        """Record the hook fields this legacy signature accepts."""
        self.hooks.append(
            {
                'name': name,
                'stage': stage,
                'status': status,
                'target_type': target_type,
                'target_id': target_id,
                'target_name': target_name,
                'error': error,
                'elapsed_ms': elapsed_ms,
            }
        )


class _BrokenHookReporter(_MinimalReporter):
    """Raise an unrelated ``TypeError`` from the hook callback."""

    def report_hook_fired(self, **kwargs: object) -> None:
        """Fail the way a buggy reporter would."""
        _ = kwargs
        message = 'unexpected keyword argument in hook bookkeeping'
        raise TypeError(message)


class _ChunkReporter(_MinimalReporter):
    """Accept response chunks with an explicit restoration parameter."""

    def __init__(self) -> None:
        """Start with an empty chunk log."""
        super().__init__()
        self.chunks: list[dict[str, object]] = []

    def report_response_chunk(
        self,
        text: str,
        *,
        assistant_id: str | None = None,
        full_text: str | None = None,
        replace_content: bool = False,
        private_value_restorations: list[dict[str, object]] | None = None,
    ) -> None:
        """Record every chunk field."""
        self.chunks.append(
            {
                'text': text,
                'assistant_id': assistant_id,
                'full_text': full_text,
                'replace_content': replace_content,
                'private_value_restorations': private_value_restorations,
            }
        )


class _KwargsChunkReporter(_MinimalReporter):
    """Accept response chunks through a catch-all keyword parameter."""

    def __init__(self) -> None:
        """Start with an empty chunk log."""
        super().__init__()
        self.chunks: list[dict[str, object]] = []

    def report_response_chunk(self, text: str, **kwargs: object) -> None:
        """Record the text and every keyword received."""
        self.chunks.append({'text': text, **kwargs})


class _LegacyChunkReporter(_MinimalReporter):
    """Accept response chunks without restoration metadata."""

    def __init__(self) -> None:
        """Start with an empty chunk log."""
        super().__init__()
        self.chunks: list[dict[str, object]] = []

    def report_response_chunk(
        self,
        text: str,
        *,
        assistant_id: str | None = None,
        full_text: str | None = None,
        replace_content: bool = False,
    ) -> None:
        """Record the fields this legacy signature accepts."""
        self.chunks.append(
            {
                'text': text,
                'assistant_id': assistant_id,
                'full_text': full_text,
                'replace_content': replace_content,
            }
        )


class _AssignmentReporter(_MinimalReporter):
    """Accept agent-assignment callbacks."""

    def __init__(self) -> None:
        """Start with an empty assignment log."""
        super().__init__()
        self.assignments: list[dict[str, object]] = []

    def report_agent_assignment(self, **kwargs: object) -> None:
        """Record the assignment keywords."""
        self.assignments.append(kwargs)


# Section: Enrichment signature generations.


class _FullEnrichmentReporter(_MinimalReporter):
    """Accept every enrichment field."""

    def __init__(self) -> None:
        """Start with an empty enrichment log."""
        super().__init__()
        self.enrichments: list[dict[str, object]] = []

    def report_enrichment(self, **kwargs: object) -> None:
        """Record the enrichment keywords."""
        self.enrichments.append(kwargs)


class _NoReevaluateEnrichmentReporter(_MinimalReporter):
    """Accept enrichment fields up to ``redaction``."""

    def __init__(self) -> None:
        """Start with an empty enrichment log."""
        super().__init__()
        self.enrichments: list[dict[str, object]] = []

    def report_enrichment(  # noqa: PLR0913 - mirrors one legacy signature generation.
        self,
        *,
        phase: str,
        message: str,
        scope_id: str | None = None,
        scope_name: str | None = None,
        scope_type: str | None = None,
        memory: dict[str, object] | None = None,
        redaction: dict[str, object] | None = None,
    ) -> None:
        """Record the fields this signature accepts."""
        self.enrichments.append(
            {
                'phase': phase,
                'message': message,
                'scope_id': scope_id,
                'scope_name': scope_name,
                'scope_type': scope_type,
                'memory': memory,
                'redaction': redaction,
            }
        )


class _NoRedactionEnrichmentReporter(_MinimalReporter):
    """Accept enrichment fields up to ``memory``."""

    def __init__(self) -> None:
        """Start with an empty enrichment log."""
        super().__init__()
        self.enrichments: list[dict[str, object]] = []

    def report_enrichment(  # noqa: PLR0913 - mirrors one legacy signature generation.
        self,
        *,
        phase: str,
        message: str,
        scope_id: str | None = None,
        scope_name: str | None = None,
        scope_type: str | None = None,
        memory: dict[str, object] | None = None,
    ) -> None:
        """Record the fields this signature accepts."""
        self.enrichments.append(
            {
                'phase': phase,
                'message': message,
                'scope_id': scope_id,
                'scope_name': scope_name,
                'scope_type': scope_type,
                'memory': memory,
            }
        )


class _NoMemoryEnrichmentReporter(_MinimalReporter):
    """Accept scope and redaction fields but not ``memory``."""

    def __init__(self) -> None:
        """Start with an empty enrichment log."""
        super().__init__()
        self.enrichments: list[dict[str, object]] = []

    def report_enrichment(  # noqa: PLR0913 - mirrors one legacy signature generation.
        self,
        *,
        phase: str,
        message: str,
        scope_id: str | None = None,
        scope_name: str | None = None,
        scope_type: str | None = None,
        redaction: dict[str, object] | None = None,
    ) -> None:
        """Record the fields this signature accepts."""
        self.enrichments.append(
            {
                'phase': phase,
                'message': message,
                'scope_id': scope_id,
                'scope_name': scope_name,
                'scope_type': scope_type,
                'redaction': redaction,
            }
        )


class _ScopeOnlyEnrichmentReporter(_MinimalReporter):
    """Accept phase, message, and scope fields only."""

    def __init__(self) -> None:
        """Start with an empty enrichment log."""
        super().__init__()
        self.enrichments: list[dict[str, object]] = []

    def report_enrichment(
        self,
        *,
        phase: str,
        message: str,
        scope_id: str | None = None,
        scope_name: str | None = None,
        scope_type: str | None = None,
    ) -> None:
        """Record the fields this signature accepts."""
        self.enrichments.append(
            {
                'phase': phase,
                'message': message,
                'scope_id': scope_id,
                'scope_name': scope_name,
                'scope_type': scope_type,
            }
        )


class _PhaseOnlyEnrichmentReporter(_MinimalReporter):
    """Accept only the original phase and message fields."""

    def __init__(self) -> None:
        """Start with an empty enrichment log."""
        super().__init__()
        self.enrichments: list[dict[str, object]] = []

    def report_enrichment(self, *, phase: str, message: str) -> None:
        """Record the fields this signature accepts."""
        self.enrichments.append({'phase': phase, 'message': message})


class _BrokenEnrichmentReporter(_MinimalReporter):
    """Raise an unrelated ``TypeError`` from enrichment."""

    def report_enrichment(self, **kwargs: object) -> None:
        """Fail the way a buggy reporter would."""
        _ = kwargs
        message = 'memory must be a mapping'
        raise TypeError(message)


# Section: Helpers.


def _router(
    reporter: BaseReporter,
    *,
    include: list[str] | str | None = None,
    exclude: list[str] | str | None = None,
) -> tuple[EventRouterReporter, list[dict[str, object]]]:
    """Wrap ``reporter`` with a router whose sink collects payloads."""
    sunk: list[dict[str, object]] = []
    router = EventRouterReporter(reporter, include=include, exclude=exclude, event_sink=sunk.append)
    return router, sunk


def _event(name: str, position: int = 1) -> StreamEvent:
    """Build a stream event whose v1-visible name is ``name``."""
    return StreamEvent(position=position, event_type=name, data={'payload': {'k': name}})


def _response() -> InvokeResponse:
    """Build a minimal invocation response."""
    return InvokeResponse(
        final_message=Message(
            message_id='msg-1',
            role='assistant',
            content='Done.',
            ts='2026-07-12T00:00:00Z',
        ),
        session_id='ses-1',
        root_event_id='evt-1',
        event_positions=[1],
        response='Done.',
    )


def _names(reporter: _RecordingReporter) -> list[str]:
    """Return the forwarded callback names in order."""
    return [name for name, _, _ in reporter.calls]


_ENRICHMENT_FIELDS: dict[str, object] = {
    'phase': 'reevaluate_accrued',
    'message': 'Collected two results',
    'scope_id': 'agent-1',
    'scope_name': 'Planner',
    'scope_type': 'agent',
    'memory': {'hits': 2},
    'redaction': {'masked': 1},
    'source': 'tool',
    'trigger_tool': 'search',
    'target_tool': 'summarize',
    'reevaluate_count': 3,
    'collected_count': 2,
}


def _report_enrichment(router: EventRouterReporter) -> None:
    """Send the full enrichment field set through ``router``."""
    router.report_enrichment(
        phase='reevaluate_accrued',
        message='Collected two results',
        scope_id='agent-1',
        scope_name='Planner',
        scope_type='agent',
        memory={'hits': 2},
        redaction={'masked': 1},
        source='tool',
        trigger_tool='search',
        target_tool='summarize',
        reevaluate_count=3,
        collected_count=2,
    )


# Section: Category configuration.


def test_router_mirrors_the_wrapped_reporter_enabled_state() -> None:
    """The router reports enabled exactly when the wrapped reporter does."""
    assert EventRouterReporter(_RecordingReporter(enabled=True)).enabled is True
    assert EventRouterReporter(_RecordingReporter(enabled=False)).enabled is False


def test_unknown_category_token_lists_every_valid_token() -> None:
    """An unknown include token fails with the sorted list of accepted tokens."""
    with pytest.raises(ValueError, match='Unknown event category') as raised:
        _ = EventRouterReporter(_RecordingReporter(), include=['lifecycle', 'Bogus'])

    assert str(raised.value) == f"Unknown event category 'Bogus'. Valid values: {_VALID_TOKENS}"


def test_non_string_category_token_is_a_type_error() -> None:
    """Category tokens must be strings."""
    tokens: list[object] = ['lifecycle', 7]
    with pytest.raises(TypeError) as raised:
        _ = EventRouterReporter(_RecordingReporter(), exclude=tokens)  # pyright: ignore[reportArgumentType]

    assert str(raised.value) == 'event category values must be strings'


def test_category_tokens_are_trimmed_case_folded_and_blank_tokens_skipped() -> None:
    """Whitespace and case do not matter, and blank tokens are ignored."""
    reporter = _RecordingReporter()
    router, _ = _router(reporter, include=['  LIFECYCLE ', '', '   '])

    router.print_header('Title')
    router.report_status_message('ready')
    router.report_response_chunk('hello')

    assert _names(reporter) == ['print_header', 'report_status_message']


def test_empty_include_list_enables_no_category() -> None:
    """An explicit empty include list filters every callback out."""
    reporter = _RecordingReporter()
    router, sunk = _router(reporter, include=[])

    router.print_header('Title')
    router.report_response(_response())

    assert reporter.calls == []
    assert sunk == []


@pytest.mark.parametrize(
    ('token', 'expected'),
    [
        (
            'all',
            [
                'lifecycle',
                'enrichment',
                'response',
                'func',
                'model',
                'mcp',
                'agent',
                'system',
                'assignment',
            ],
        ),
        ('tool', ['func', 'model', 'mcp', 'agent', 'system']),
        ('tools', ['func', 'model', 'mcp', 'agent', 'system']),
        ('assistant', ['response']),
        ('stream', ['response']),
        ('response', ['response']),
        ('enrichment', ['enrichment']),
        ('function', ['func']),
        ('func_tool', ['func']),
        ('func', ['func']),
        ('model_tool', ['model']),
        ('model', ['model']),
        ('mcp_tool', ['mcp']),
        ('mcp', ['mcp']),
        ('agent_tool', ['agent']),
        ('agent', ['agent']),
        ('system_tool', ['system']),
        ('system', ['system']),
        ('agent_assignment', ['assignment']),
        ('assignment', ['assignment']),
        ('lifecycle', ['lifecycle']),
    ],
)
def test_include_token_enables_exactly_its_categories(token: str, expected: list[str]) -> None:
    """Each user-facing token expands to its documented canonical categories."""
    reporter = _RecordingReporter()
    router, sunk = _router(reporter, include=token)

    router.print_header('Title')
    router.report_enrichment(phase='planning', message='Planning')
    router.report_response_chunk('Hi')
    for index, tool_type in enumerate(('func', 'model', 'mcp', 'agent', 'system')):
        router.report_tool_start('tool', f'e-{index}', tool_type)
    router.report_agent_assignment(agent_name='Planner')

    assert [entry['category'] for entry in sunk] == expected


def test_exclude_wins_over_include() -> None:
    """A category both included and excluded is filtered out."""
    reporter = _RecordingReporter()
    router, sunk = _router(reporter, include='all', exclude=['tools'])

    router.report_tool_start('search', 'e-1', 'mcp')
    router.report_system_tool_start('think', 'a-1')
    router.print_header('Title')

    assert _names(reporter) == ['print_header']
    assert [entry['category'] for entry in sunk] == ['lifecycle']


# Section: Stream events and responses.


@pytest.mark.parametrize(
    ('event_name', 'category'),
    [
        ('update', 'response'),
        ('assistant_chunk', 'response'),
        ('response_chunk', 'response'),
        ('system_tool_start', 'system'),
        ('system_tool_chunk', 'system'),
        ('tool_event', 'func'),
        ('tool_complete', 'func'),
        ('enrichment', 'enrichment'),
        ('agent_assignment', 'assignment'),
        ('final', 'lifecycle'),
        ('status_message', 'lifecycle'),
    ],
)
def test_stream_event_category_follows_its_v1_name(event_name: str, category: str) -> None:
    """Stream events are filtered by the category their v1-visible name maps to."""
    reporter = _RecordingReporter()
    router, sunk = _router(reporter, include=category)
    event = _event(event_name, position=4)

    router.report_event(event)

    assert reporter.calls == [('report_event', (event,), {})]
    assert sunk == [
        {
            'category': category,
            'event': 'report_event',
            'payload': {
                'event_type': event_name,
                'name': event_name,
                'payload': {'k': event_name},
                'data': {'payload': {'k': event_name}},
                'position': 4,
            },
        }
    ]

    other = 'lifecycle' if category != 'lifecycle' else 'response'
    filtered_reporter = _RecordingReporter()
    filtered, filtered_sink = _router(filtered_reporter, include=other)
    filtered.report_event(event)
    assert filtered_reporter.calls == []
    assert filtered_sink == []


def test_projected_stream_event_routes_by_its_compat_name() -> None:
    """A projected status chunk routes as a response ``update`` event."""
    reporter = _RecordingReporter()
    router, sunk = _router(reporter, include='response')
    event = StreamEvent(
        position=2,
        event_type='status_message_chunk',
        data={'payload': {'content_delta': 'Hi'}},
        compat_name='update',
        compat_payload={'streaming_content': 'Hi'},
    )

    router.report_event(event)

    assert sunk == [
        {
            'category': 'response',
            'event': 'report_event',
            'payload': {
                'event_type': 'status_message_chunk',
                'name': 'update',
                'payload': {'streaming_content': 'Hi'},
                'data': {'payload': {'content_delta': 'Hi'}},
                'position': 2,
            },
        }
    ]


def test_invoke_response_is_a_response_category_event() -> None:
    """The terminal response is forwarded and sunk under ``response``."""
    reporter = _RecordingReporter()
    router, sunk = _router(reporter)
    response = _response()

    router.report_response(response)

    assert reporter.calls == [('report_response', (response,), {})]
    assert sunk == [
        {'category': 'response', 'event': 'report_response', 'payload': {'response': response}}
    ]


def test_router_without_sink_still_forwards() -> None:
    """A router built without a sink forwards callbacks unchanged."""
    reporter = _RecordingReporter()
    router = EventRouterReporter(reporter)

    router.print_error_summary('failed')

    assert reporter.calls == [('print_error_summary', ('failed',), {})]


def test_sink_failure_is_logged_and_does_not_interrupt_reporting(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """A raising sink is logged once per event and the wrapped reporter still runs."""
    reporter = _RecordingReporter()

    def failing_sink(payload: dict[str, object]) -> None:
        _ = payload
        message = 'sink offline'
        raise RuntimeError(message)

    router = EventRouterReporter(reporter, event_sink=failing_sink)

    with caplog.at_level(logging.ERROR):
        router.print_section('Section')
        router.print_section('Next')

    assert _names(reporter) == ['print_section', 'print_section']
    messages = [record.getMessage() for record in caplog.records]
    assert messages == [
        'Event payload sink raised an exception',
        'Event payload sink raised an exception',
    ]
    assert all(record.exc_info is not None for record in caplog.records)


# Section: Display callbacks.


def test_display_callbacks_forward_arguments_and_sink_payloads() -> None:
    """Each display callback forwards its arguments and sinks a lifecycle payload."""
    reporter = _RecordingReporter()
    router, sunk = _router(reporter)
    usage: dict[str, object] = {'total_tokens': 3}

    router.print_header('Run', 'sub')
    router.print_header('Bare')
    router.print_section('Plan')
    router.print_section('Act', 'bold red')
    router.print_summary(usage)  # pyright: ignore[reportArgumentType]
    router.print_summary()
    router.print_final_result({'ok': True})
    router.print_final_response('All done')
    router.print_error_summary('boom')

    assert reporter.calls == [
        ('print_header', ('Run', 'sub'), {}),
        ('print_header', ('Bare', ''), {}),
        ('print_section', ('Plan', 'bold cyan'), {}),
        ('print_section', ('Act', 'bold red'), {}),
        ('print_summary', (usage,), {}),
        ('print_summary', (None,), {}),
        ('print_final_result', ({'ok': True},), {}),
        ('print_final_response', ('All done',), {}),
        ('print_error_summary', ('boom',), {}),
    ]
    assert sunk == [
        {
            'category': 'lifecycle',
            'event': 'print_header',
            'payload': {'title': 'Run', 'subtitle': 'sub'},
        },
        {
            'category': 'lifecycle',
            'event': 'print_header',
            'payload': {'title': 'Bare', 'subtitle': ''},
        },
        {
            'category': 'lifecycle',
            'event': 'print_section',
            'payload': {'title': 'Plan', 'style': 'bold cyan'},
        },
        {
            'category': 'lifecycle',
            'event': 'print_section',
            'payload': {'title': 'Act', 'style': 'bold red'},
        },
        {'category': 'lifecycle', 'event': 'summary', 'payload': {'token_usage': usage}},
        {'category': 'lifecycle', 'event': 'summary', 'payload': {'token_usage': None}},
        {'category': 'lifecycle', 'event': 'final_result', 'payload': {'result': {'ok': True}}},
        {'category': 'lifecycle', 'event': 'final_response', 'payload': {'response': 'All done'}},
        {'category': 'lifecycle', 'event': 'error_summary', 'payload': {'error': 'boom'}},
    ]


@pytest.mark.parametrize(
    ('event_type', 'category'),
    [
        ('func', 'func'),
        (' MODEL ', 'model'),
        ('mcp', 'mcp'),
        ('agent', 'agent'),
        ('system', 'system'),
        ('Enrichment', 'enrichment'),
        ('stream', 'response'),
        ('raw', 'response'),
        ('assistant', 'response'),
        ('response', 'response'),
        ('error', 'lifecycle'),
        ('success', 'lifecycle'),
        ('', 'lifecycle'),
    ],
)
def test_print_event_category_follows_its_event_type(event_type: str, category: str) -> None:
    """``print_event`` routes by its normalized event type."""
    reporter = _RecordingReporter()
    router, sunk = _router(reporter, include=category)

    router.print_event(event_type, 'message', {'detail': 1})

    assert reporter.calls == [('print_event', (event_type, 'message', {'detail': 1}), {})]
    assert sunk == [
        {
            'category': category,
            'event': 'print_event',
            'payload': {'event_type': event_type, 'message': 'message', 'details': {'detail': 1}},
        }
    ]


# Section: Progress and input.


def test_live_progress_yields_the_wrapped_task_when_lifecycle_is_enabled() -> None:
    """Progress contexts and updates reach the wrapped reporter for lifecycle routing."""
    reporter = _RecordingReporter()
    router, sunk = _router(reporter, include='lifecycle')

    with router.live_progress('Loading') as task:
        router.update_progress(task, 'Halfway')
    with router.live_progress() as default_task:
        pass

    assert task == 'task:Loading'
    assert default_task == 'task:Processing...'
    assert reporter.calls == [
        ('live_progress', ('Loading',), {}),
        ('update_progress', ('task:Loading', 'Halfway'), {}),
        ('live_progress', ('Processing...',), {}),
    ]
    assert sunk == []


def test_live_progress_is_silent_when_lifecycle_is_filtered() -> None:
    """Without lifecycle routing, progress yields ``None`` and updates are dropped."""
    reporter = _RecordingReporter()
    router, _ = _router(reporter, exclude='lifecycle')

    with router.live_progress('Loading') as task:
        router.update_progress(task, 'Halfway')

    assert task is None
    assert reporter.calls == []


def test_user_input_is_never_filtered() -> None:
    """Input preparation and collection bypass category filtering."""
    reporter = _RecordingReporter()
    router, sunk = _router(reporter, include=[])

    with router.prepare_for_user_input():
        answer = router.get_input(
            'Account?',
            input_type='choice',
            choices=['a', 'b'],
            data_key='acct',
            arg_name='account',
            tool_name='lookup',
        )
    default_answer = router.get_input('Name?')

    assert (answer, default_answer) == ('answer', 'answer')
    assert reporter.calls == [
        ('prepare_for_user_input', (), {}),
        (
            'get_input',
            ('Account?',),
            {
                'input_type': 'choice',
                'choices': ['a', 'b'],
                'data_key': 'acct',
                'arg_name': 'account',
                'tool_name': 'lookup',
            },
        ),
        (
            'get_input',
            ('Name?',),
            {
                'input_type': 'text',
                'choices': None,
                'data_key': None,
                'arg_name': None,
                'tool_name': None,
            },
        ),
    ]
    assert sunk == []


def test_get_input_falls_back_to_a_prompt_only_reporter() -> None:
    """A reporter without input metadata keywords is asked with the prompt alone."""
    router = EventRouterReporter(_LegacyInputReporter())

    assert router.get_input('Account?', input_type='choice') == 'legacy:Account?'


def test_get_input_reraises_unrelated_type_errors() -> None:
    """A ``TypeError`` that is not a keyword rejection propagates unchanged."""
    router = EventRouterReporter(_BrokenInputReporter())

    with pytest.raises(TypeError) as raised:
        _ = router.get_input('Account?')

    assert str(raised.value) == 'prompt must be str'


# Section: Session and system callbacks.


def test_session_callbacks_forward_as_lifecycle_events() -> None:
    """Session start and phase changes are lifecycle callbacks."""
    reporter = _RecordingReporter()
    router, sunk = _router(reporter, include='lifecycle')

    router.report_session_start('ses-1', 'asst-1')
    router.report_phase_change('planning')
    router.report_private_data({'account': 'placeholder'})

    assert reporter.calls == [
        ('report_session_start', ('ses-1', 'asst-1'), {}),
        ('report_phase_change', ('planning',), {}),
        ('report_private_data', ({'account': 'placeholder'},), {}),
    ]
    assert [(entry['category'], entry['event']) for entry in sunk] == [
        ('lifecycle', 'session_start'),
        ('lifecycle', 'phase_change'),
        ('lifecycle', 'private_data'),
    ]
    assert sunk[0]['payload'] == {'session_id': 'ses-1', 'assistant_id': 'asst-1'}
    assert sunk[1]['payload'] == {'phase': 'planning'}


def test_private_data_sink_receives_keys_and_placeholders_only() -> None:
    """The event sink never receives a raw private value (U05-D4)."""
    reporter = _RecordingReporter()
    router, sunk = _router(reporter, include='lifecycle')

    router.report_private_data({'ssn': '123-45-6789'})

    assert reporter.calls == [('report_private_data', ({'ssn': '123-45-6789'},), {})]
    assert sunk == [
        {
            'category': 'lifecycle',
            'event': 'private_data',
            'payload': {'private_data': {'ssn': '{_{ssn}_}'}},
        }
    ]


def test_system_tool_callbacks_forward_as_system_events() -> None:
    """System-tool start, progress, and completion route under ``system``."""
    reporter = _RecordingReporter()
    router, sunk = _router(reporter, include='system_tool')

    router.report_system_tool_start('think', 'a-1')
    router.report_system_tool_progress('e-1', 'think', 3, 1.5, 'partial')
    router.report_system_tool_progress('e-1', 'think', 4, 2.0)
    router.report_system_tool_complete('think', 'a-1')

    assert reporter.calls == [
        ('report_system_tool_start', ('think', 'a-1'), {}),
        (
            'report_system_tool_progress',
            (),
            {
                'event_id': 'e-1',
                'tool_name': 'think',
                'chunk_count': 3,
                'elapsed_seconds': 1.5,
                'text': 'partial',
            },
        ),
        (
            'report_system_tool_progress',
            (),
            {
                'event_id': 'e-1',
                'tool_name': 'think',
                'chunk_count': 4,
                'elapsed_seconds': 2.0,
                'text': None,
            },
        ),
        ('report_system_tool_complete', ('think', 'a-1'), {}),
    ]
    assert sunk == [
        {
            'category': 'system',
            'event': 'system_tool_start',
            'payload': {'tool_name': 'think', 'assignment_id': 'a-1'},
        },
        {
            'category': 'system',
            'event': 'system_tool_progress',
            'payload': {
                'event_id': 'e-1',
                'tool_name': 'think',
                'chunk_count': 3,
                'elapsed_seconds': 1.5,
                'text': 'partial',
            },
        },
        {
            'category': 'system',
            'event': 'system_tool_progress',
            'payload': {
                'event_id': 'e-1',
                'tool_name': 'think',
                'chunk_count': 4,
                'elapsed_seconds': 2.0,
                'text': None,
            },
        },
        {
            'category': 'system',
            'event': 'system_tool_complete',
            'payload': {'tool_name': 'think', 'assignment_id': 'a-1'},
        },
    ]


# Section: Tool callbacks.


@pytest.mark.parametrize(
    ('tool_type', 'category'),
    [
        ('func', 'func'),
        (' MCP ', 'mcp'),
        ('agent', 'agent'),
        ('model', 'model'),
        ('system', 'system'),
        ('webhook', 'func'),
        (None, 'func'),
    ],
)
def test_tool_start_routes_by_its_tool_type(tool_type: str | None, category: str) -> None:
    """Tool starts route by normalized tool type, defaulting to ``func``."""
    reporter = _RecordingReporter()
    router, sunk = _router(reporter, include=category)

    router.report_tool_start('search', 'e-1', tool_type, 'Planner', {'q': 'x'}, 'Swarm', ['acct'])

    assert reporter.calls == [
        (
            'report_tool_start',
            ('search', 'e-1', tool_type, 'Planner', {'q': 'x'}, 'Swarm', ['acct']),
            {},
        )
    ]
    assert sunk == [
        {
            'category': category,
            'event': 'tool_start',
            'payload': {
                'tool_name': 'search',
                'event_id': 'e-1',
                'tool_type': tool_type,
                'agent_name': 'Planner',
                'tool_args': {'q': 'x'},
                'swarm_name': 'Swarm',
                'private_data_keys': ['acct'],
            },
        }
    ]


def test_tool_completion_reuses_the_category_recorded_at_start_once() -> None:
    """A completion routes like its start; a repeated completion falls back to ``func``."""
    reporter = _RecordingReporter()
    router, sunk = _router(reporter, include='mcp')

    router.report_tool_start('search', 'e-1', 'mcp')
    router.report_tool_complete('e-1', 12, {'rows': 1})
    router.report_tool_complete('e-1', 13, None)

    assert _names(reporter) == ['report_tool_start', 'report_tool_complete']
    assert reporter.calls[1] == ('report_tool_complete', ('e-1', 12, {'rows': 1}), {})
    assert sunk[1] == {
        'category': 'mcp',
        'event': 'tool_complete',
        'payload': {'event_id': 'e-1', 'elapsed_ms': 12, 'result': {'rows': 1}},
    }


@pytest.mark.parametrize(
    ('event_id', 'category'),
    [
        ('system-tool:think-1', 'system'),
        ('model-tool:route-1', 'model'),
        ('untracked', 'func'),
        ('', 'func'),
    ],
)
def test_untracked_tool_completion_routes_by_event_id_prefix(event_id: str, category: str) -> None:
    """Without a recorded start, legacy id prefixes pick the category."""
    reporter = _RecordingReporter()
    router, sunk = _router(reporter, include=category)

    router.report_tool_complete(event_id)

    assert reporter.calls == [('report_tool_complete', (event_id, None, None), {})]
    assert sunk == [
        {
            'category': category,
            'event': 'tool_complete',
            'payload': {'event_id': event_id, 'elapsed_ms': None, 'result': None},
        }
    ]


def test_tool_error_reuses_the_recorded_category_once() -> None:
    """An error routes like its start, trims its id, and clears the record."""
    reporter = _RecordingReporter()
    router, sunk = _router(reporter, include='agent')

    router.report_tool_start('delegate', 'e-7', 'agent')
    router.report_tool_error('delegate', 'refused', ' e-7 ', 40)
    router.report_tool_error('delegate', 'refused again', 'e-7', 41)

    assert reporter.calls[1:] == [
        ('report_tool_error', ('delegate', 'refused', ' e-7 ', 40), {}),
    ]
    assert sunk[1] == {
        'category': 'agent',
        'event': 'tool_error',
        'payload': {
            'tool_name': 'delegate',
            'error': 'refused',
            'event_id': ' e-7 ',
            'elapsed_ms': 40,
        },
    }
    assert [entry['event'] for entry in sunk] == ['tool_start', 'tool_error']


@pytest.mark.parametrize('event_id', [None, '   '])
def test_tool_error_without_an_id_routes_as_func(event_id: str | None) -> None:
    """A missing or blank id routes the error as a ``func`` tool error."""
    reporter = _RecordingReporter()
    router, sunk = _router(reporter, include='func')

    router.report_tool_error('search', 'timeout', event_id)

    assert reporter.calls == [('report_tool_error', ('search', 'timeout', event_id, None), {})]
    assert sunk == [
        {
            'category': 'func',
            'event': 'tool_error',
            'payload': {
                'tool_name': 'search',
                'error': 'timeout',
                'event_id': event_id,
                'elapsed_ms': None,
            },
        }
    ]


def test_tool_error_routes_untracked_prefixed_ids() -> None:
    """An untracked system-tool id routes its error under ``system``."""
    reporter = _RecordingReporter()
    router, sunk = _router(reporter, include='system')

    router.report_tool_error('think', 'failed', 'system-tool:9')

    assert [entry['category'] for entry in sunk] == ['system']


def test_model_tool_completion_routes_as_model_and_clears_the_id() -> None:
    """Model-tool completions are ``model`` events and consume the id's record."""
    reporter = _RecordingReporter()
    router, sunk = _router(reporter, include=['model', 'mcp'])

    router.report_tool_start('route', 'e-3', 'mcp')
    router.report_model_tool_complete('route', 'e-3', 'Planner', 'Swarm', {'ok': 1})
    router.report_model_tool_complete('route')
    router.report_tool_complete('e-3')

    assert reporter.calls[1:] == [
        (
            'report_model_tool_complete',
            ('route',),
            {
                'event_id': 'e-3',
                'agent_name': 'Planner',
                'swarm_name': 'Swarm',
                'result': {'ok': 1},
            },
        ),
        (
            'report_model_tool_complete',
            ('route',),
            {'event_id': None, 'agent_name': None, 'swarm_name': None, 'result': None},
        ),
    ]
    assert sunk[1:] == [
        {
            'category': 'model',
            'event': 'model_tool_complete',
            'payload': {
                'tool_name': 'route',
                'event_id': 'e-3',
                'agent_name': 'Planner',
                'swarm_name': 'Swarm',
                'result': {'ok': 1},
            },
        },
        {
            'category': 'model',
            'event': 'model_tool_complete',
            'payload': {
                'tool_name': 'route',
                'event_id': None,
                'agent_name': None,
                'swarm_name': None,
                'result': None,
            },
        },
    ]


def test_model_tool_completion_clears_the_id_even_when_filtered() -> None:
    """A filtered model completion still consumes the id's recorded category."""
    reporter = _RecordingReporter()
    router, sunk = _router(reporter, include='mcp')

    router.report_tool_start('route', 'e-3', 'mcp')
    router.report_model_tool_complete('route', 'e-3')
    router.report_tool_complete('e-3')

    assert _names(reporter) == ['report_tool_start']
    assert len(sunk) == 1


# Section: Hooks.


def test_hook_fired_forwards_every_field() -> None:
    """Hook notifications are lifecycle events carrying every hook field."""
    reporter = _RecordingReporter()
    router, sunk = _router(reporter, include='lifecycle')

    router.report_hook_fired(
        name='audit',
        stage='after',
        status='failed',
        target_type='tool',
        target_id='e-1',
        target_name='search',
        source='scope',
        error='hook raised',
        elapsed_ms=5,
    )

    fields: dict[str, object] = {
        'name': 'audit',
        'stage': 'after',
        'status': 'failed',
        'target_type': 'tool',
        'target_id': 'e-1',
        'target_name': 'search',
        'source': 'scope',
        'error': 'hook raised',
        'elapsed_ms': 5,
    }
    assert reporter.calls == [('report_hook_fired', (), fields)]
    assert sunk == [{'category': 'lifecycle', 'event': 'hook_fired', 'payload': fields}]


def test_hook_fired_retries_without_source_for_a_legacy_reporter() -> None:
    """A reporter that rejects ``source`` receives every other hook field."""
    reporter = _LegacyHookReporter()
    router = EventRouterReporter(reporter)

    router.report_hook_fired(
        name='audit', stage='before', status='completed', target_type='agent', source='swarm'
    )

    assert reporter.hooks == [
        {
            'name': 'audit',
            'stage': 'before',
            'status': 'completed',
            'target_type': 'agent',
            'target_id': None,
            'target_name': None,
            'error': None,
            'elapsed_ms': None,
        }
    ]


def test_hook_fired_reraises_unrelated_type_errors() -> None:
    """A ``TypeError`` that does not reject ``source`` propagates unchanged."""
    router = EventRouterReporter(_BrokenHookReporter())

    with pytest.raises(TypeError) as raised:
        router.report_hook_fired(
            name='audit', stage='before', status='completed', target_type='tool'
        )

    assert str(raised.value) == 'unexpected keyword argument in hook bookkeeping'


# Section: Assistant callbacks.


_RESTORATIONS: list[dict[str, object]] = [{'path': '/text', 'start': 0, 'end': 4}]


def test_response_chunk_passes_restorations_to_a_reporter_that_names_them() -> None:
    """Restoration spans reach a reporter whose signature declares them."""
    reporter = _ChunkReporter()
    router, sunk = _router(reporter, include='response')

    router.report_response_chunk(
        'Hi',
        assistant_id='asst-1',
        full_text='Hi',
        replace_content=True,
        private_value_restorations=_RESTORATIONS,
    )

    assert reporter.chunks == [
        {
            'text': 'Hi',
            'assistant_id': 'asst-1',
            'full_text': 'Hi',
            'replace_content': True,
            'private_value_restorations': _RESTORATIONS,
        }
    ]
    assert sunk == [
        {
            'category': 'response',
            'event': 'response_chunk',
            'payload': {
                'text': 'Hi',
                'assistant_id': 'asst-1',
                'full_text': 'Hi',
                'replace_content': True,
                'private_value_restorations': _RESTORATIONS,
            },
        }
    ]


def test_response_chunk_passes_restorations_to_a_catch_all_reporter() -> None:
    """Restoration spans reach a reporter that accepts arbitrary keywords."""
    reporter = _KwargsChunkReporter()
    router = EventRouterReporter(reporter)

    router.report_response_chunk('Hi', private_value_restorations=_RESTORATIONS)
    router.report_response_chunk('Yo', private_value_restorations=[])

    assert reporter.chunks == [
        {
            'text': 'Hi',
            'assistant_id': None,
            'full_text': None,
            'replace_content': False,
            'private_value_restorations': _RESTORATIONS,
        },
        {'text': 'Yo', 'assistant_id': None, 'full_text': None, 'replace_content': False},
    ]


def test_response_chunk_omits_restorations_for_a_legacy_reporter() -> None:
    """A reporter without the restoration parameter still receives the chunk."""
    reporter = _LegacyChunkReporter()
    router, sunk = _router(reporter)

    router.report_response_chunk(
        'Hi', assistant_id='asst-1', private_value_restorations=_RESTORATIONS
    )

    assert reporter.chunks == [
        {'text': 'Hi', 'assistant_id': 'asst-1', 'full_text': None, 'replace_content': False}
    ]
    assert sunk[0]['payload'] == {
        'text': 'Hi',
        'assistant_id': 'asst-1',
        'full_text': None,
        'replace_content': False,
        'private_value_restorations': _RESTORATIONS,
    }


def test_response_chunk_is_filtered_outside_response_routing() -> None:
    """Chunks are dropped when the response category is not enabled."""
    reporter = _ChunkReporter()
    router, sunk = _router(reporter, include='lifecycle')

    router.report_response_chunk('Hi')

    assert reporter.chunks == []
    assert sunk == []


def test_status_message_forwards_as_lifecycle() -> None:
    """Status messages are lifecycle callbacks carrying the assistant id."""
    reporter = _RecordingReporter()
    router, sunk = _router(reporter, include='lifecycle')

    router.report_status_message('Searching', assistant_id='asst-1')
    router.report_status_message('Waiting')

    assert reporter.calls == [
        ('report_status_message', ('Searching',), {'assistant_id': 'asst-1'}),
        ('report_status_message', ('Waiting',), {'assistant_id': None}),
    ]
    assert sunk == [
        {
            'category': 'lifecycle',
            'event': 'status_message',
            'payload': {'message': 'Searching', 'assistant_id': 'asst-1'},
        },
        {
            'category': 'lifecycle',
            'event': 'status_message',
            'payload': {'message': 'Waiting', 'assistant_id': None},
        },
    ]


# Section: Enrichment and assignment.


def test_enrichment_forwards_every_field_to_a_current_reporter() -> None:
    """A reporter that accepts every enrichment field receives all of them."""
    reporter = _FullEnrichmentReporter()
    router, sunk = _router(reporter, include='enrichment')

    _report_enrichment(router)

    assert reporter.enrichments == [_ENRICHMENT_FIELDS]
    assert sunk == [
        {'category': 'enrichment', 'event': 'enrichment', 'payload': _ENRICHMENT_FIELDS}
    ]


def test_enrichment_defaults_unset_fields_to_none() -> None:
    """Only phase and message are required; the rest default to ``None``."""
    reporter = _FullEnrichmentReporter()
    router = EventRouterReporter(reporter)

    router.report_enrichment(phase='planning', message='Planning')

    assert reporter.enrichments == [
        {
            'phase': 'planning',
            'message': 'Planning',
            'scope_id': None,
            'scope_name': None,
            'scope_type': None,
            'memory': None,
            'redaction': None,
            'source': None,
            'trigger_tool': None,
            'target_tool': None,
            'reevaluate_count': None,
            'collected_count': None,
        }
    ]


def test_enrichment_drops_reevaluate_fields_for_an_older_reporter() -> None:
    """A reporter without reevaluate fields receives the scope, memory, and redaction set."""
    reporter = _NoReevaluateEnrichmentReporter()
    router = EventRouterReporter(reporter)

    _report_enrichment(router)

    assert reporter.enrichments == [
        {
            'phase': 'reevaluate_accrued',
            'message': 'Collected two results',
            'scope_id': 'agent-1',
            'scope_name': 'Planner',
            'scope_type': 'agent',
            'memory': {'hits': 2},
            'redaction': {'masked': 1},
        }
    ]


def test_enrichment_drops_redaction_for_a_reporter_without_it() -> None:
    """A reporter without ``redaction`` receives scope and memory fields."""
    reporter = _NoRedactionEnrichmentReporter()
    router = EventRouterReporter(reporter)

    _report_enrichment(router)

    assert reporter.enrichments == [
        {
            'phase': 'reevaluate_accrued',
            'message': 'Collected two results',
            'scope_id': 'agent-1',
            'scope_name': 'Planner',
            'scope_type': 'agent',
            'memory': {'hits': 2},
        }
    ]


def test_enrichment_drops_memory_for_a_reporter_without_it() -> None:
    """A reporter without ``memory`` receives scope and redaction fields."""
    reporter = _NoMemoryEnrichmentReporter()
    router = EventRouterReporter(reporter)

    _report_enrichment(router)

    assert reporter.enrichments == [
        {
            'phase': 'reevaluate_accrued',
            'message': 'Collected two results',
            'scope_id': 'agent-1',
            'scope_name': 'Planner',
            'scope_type': 'agent',
            'redaction': {'masked': 1},
        }
    ]


def test_enrichment_falls_back_to_phase_and_message_for_scope_only_reporters() -> None:
    """When dropping memory still fails, only phase and message are forwarded."""
    reporter = _ScopeOnlyEnrichmentReporter()
    router = EventRouterReporter(reporter)

    _report_enrichment(router)

    assert reporter.enrichments == [
        {
            'phase': 'reevaluate_accrued',
            'message': 'Collected two results',
            'scope_id': None,
            'scope_name': None,
            'scope_type': None,
        }
    ]


def test_enrichment_falls_back_to_phase_and_message_for_the_oldest_reporter() -> None:
    """The original two-field signature receives phase and message only."""
    reporter = _PhaseOnlyEnrichmentReporter()
    router = EventRouterReporter(reporter)

    _report_enrichment(router)

    assert reporter.enrichments == [
        {'phase': 'reevaluate_accrued', 'message': 'Collected two results'}
    ]


def test_enrichment_reraises_unrelated_type_errors() -> None:
    """A ``TypeError`` that is not a keyword rejection propagates unchanged."""
    router = EventRouterReporter(_BrokenEnrichmentReporter())

    with pytest.raises(TypeError) as raised:
        _report_enrichment(router)

    assert str(raised.value) == 'memory must be a mapping'


def test_enrichment_is_filtered_outside_enrichment_routing() -> None:
    """Enrichment never reaches the reporter or sink when its category is off."""
    reporter = _FullEnrichmentReporter()
    router, sunk = _router(reporter, exclude='enrichment')

    _report_enrichment(router)

    assert reporter.enrichments == []
    assert sunk == []


def test_agent_assignment_forwards_keywords_and_sinks_them() -> None:
    """Assignment keywords reach a supporting reporter and the sink unchanged."""
    reporter = _AssignmentReporter()
    router, sunk = _router(reporter, include='assignment')

    router.report_agent_assignment(agent_name='Planner', status='completed')

    assert reporter.assignments == [{'agent_name': 'Planner', 'status': 'completed'}]
    assert sunk == [
        {
            'category': 'assignment',
            'event': 'agent_assignment',
            'payload': {'agent_name': 'Planner', 'status': 'completed'},
        }
    ]


def test_agent_assignment_is_sunk_even_without_a_reporter_callback() -> None:
    """A reporter without the assignment callback does not stop the sink payload."""
    reporter = _MinimalReporter()
    router, sunk = _router(reporter)

    router.report_agent_assignment(agent_name='Planner')

    assert reporter.calls == []
    assert sunk == [
        {
            'category': 'assignment',
            'event': 'agent_assignment',
            'payload': {'agent_name': 'Planner'},
        }
    ]


def test_agent_assignment_is_filtered_outside_assignment_routing() -> None:
    """Assignments are dropped entirely when their category is off."""
    reporter = _AssignmentReporter()
    router, sunk = _router(reporter, include='lifecycle')

    router.report_agent_assignment(agent_name='Planner')

    assert reporter.assignments == []
    assert sunk == []
