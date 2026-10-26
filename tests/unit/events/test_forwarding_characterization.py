"""Characterization of ``forward_normalized_event`` / ``forward_normalized_stream``.

Normalized AppEvents are replayed into two kinds of targets: terminal-style
reporters (method calls, with optional keywords only passed to reporters that
declare them) and ``EventBridge`` (typed ``emit_*`` calls). These tests pin
which call each event produces, with which arguments, and when it is skipped.

No async pytest plugin is configured for this package, so each test drives its
coroutine with ``asyncio.run()``.
"""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING, cast

import pytest

from maivn.events import (
    AppEvent,
    EventBridge,
    NormalizedEventForwardingState,
    build_agent_assignment_payload,
    build_assistant_chunk_payload,
    build_enrichment_payload,
    build_error_payload,
    build_final_payload,
    build_hook_fired_payload,
    build_interrupt_required_payload,
    build_session_start_payload,
    build_status_message_payload,
    build_system_tool_chunk_payload,
    build_system_tool_complete_payload,
    build_system_tool_start_payload,
    build_tool_event_payload,
    forward_normalized_event,
    forward_normalized_stream,
)

if TYPE_CHECKING:
    from collections.abc import Mapping

Call = tuple[str, tuple[object, ...], dict[str, object]]


class _Recorder:
    """Reporter implementing every optional callback with modern signatures."""

    def __init__(self) -> None:
        self.calls: list[Call] = []

    def _record(self, call_name: str, *args: object, **kwargs: object) -> None:
        self.calls.append((call_name, args, kwargs))

    def report_session_start(self, session_id: str, assistant_id: str) -> None:
        """Record a session start."""
        self._record('session_start', session_id, assistant_id)

    def report_response_chunk(
        self,
        text: str,
        *,
        assistant_id: str | None = None,
        full_text: str | None = None,
        replace_content: bool = False,
        private_value_restorations: object = None,
    ) -> None:
        """Record a response chunk."""
        self._record(
            'response_chunk',
            text,
            assistant_id=assistant_id,
            full_text=full_text,
            replace_content=replace_content,
            private_value_restorations=private_value_restorations,
        )

    def report_status_message(self, message: str, *, assistant_id: str | None = None) -> None:
        """Record a status message."""
        self._record('status_message', message, assistant_id=assistant_id)

    def report_agent_assignment(self, **kwargs: object) -> None:
        """Record an assignment."""
        self._record('agent_assignment', **kwargs)

    def report_enrichment(self, **kwargs: object) -> None:
        """Record an enrichment phase."""
        self._record('enrichment', **kwargs)

    def report_tool_start(self, *args: object) -> None:
        """Record a tool start."""
        self._record('tool_start', *args)

    def report_tool_complete(self, event_id: str, **kwargs: object) -> None:
        """Record a tool completion."""
        self._record('tool_complete', event_id, **kwargs)

    def report_tool_error(self, tool_name: str, error: str, **kwargs: object) -> None:
        """Record a tool error."""
        self._record('tool_error', tool_name, error, **kwargs)

    def report_model_tool_complete(self, tool_name: str, **kwargs: object) -> None:
        """Record a model tool completion."""
        self._record('model_tool_complete', tool_name, **kwargs)

    def report_system_tool_progress(self, **kwargs: object) -> None:
        """Record system tool progress."""
        self._record('system_tool_progress', **kwargs)

    def report_hook_fired(self, **kwargs: object) -> None:
        """Record a hook firing."""
        self._record('hook_fired', **kwargs)

    def print_event(self, event_type: str, message: str, details: object = None) -> None:
        """Record a printed event."""
        self._record('print_event', event_type, message, details=details)

    def print_summary(self, token_usage: object = None) -> None:
        """Record a summary."""
        self._record('print_summary', token_usage=token_usage)

    def print_final_response(self, response: str) -> None:
        """Record the final response."""
        self._record('print_final_response', response)

    def print_final_result(self, result: object) -> None:
        """Record the final result."""
        self._record('print_final_result', result)


class _LegacyRecorder:
    """Reporter written before the optional keywords existed."""

    def __init__(self) -> None:
        self.calls: list[Call] = []

    def report_response_chunk(
        self,
        text: str,
        *,
        assistant_id: str | None = None,
        full_text: str | None = None,
    ) -> None:
        """Record a response chunk without replace/restoration support."""
        self.calls.append(
            ('response_chunk', (text,), {'assistant_id': assistant_id, 'full_text': full_text})
        )

    def report_agent_assignment(  # noqa: PLR0913 - mirrors a legacy reporter signature.
        self,
        *,
        agent_name: str,
        status: str,
        assignment_id: str,
        swarm_name: str | None = None,
        error: str | None = None,
        result: object = None,
    ) -> None:
        """Record an assignment without the final-output flag."""
        self.calls.append(
            (
                'agent_assignment',
                (),
                {
                    'agent_name': agent_name,
                    'status': status,
                    'assignment_id': assignment_id,
                    'swarm_name': swarm_name,
                    'error': error,
                    'result': result,
                },
            )
        )

    def report_enrichment(self, phase: str, message: str) -> None:
        """Record an enrichment phase without scope, memory or redaction."""
        self.calls.append(('enrichment', (), {'phase': phase, 'message': message}))

    def report_hook_fired(  # noqa: PLR0913 - mirrors a legacy reporter signature.
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
        """Record a hook firing without the ``source`` keyword."""
        self.calls.append(
            (
                'hook_fired',
                (),
                {
                    'name': name,
                    'stage': stage,
                    'status': status,
                    'target_type': target_type,
                    'target_id': target_id,
                    'target_name': target_name,
                    'error': error,
                    'elapsed_ms': elapsed_ms,
                },
            )
        )


class _PhaseOnlyRecorder:
    """Reporter that only understands phase changes."""

    def __init__(self) -> None:
        self.calls: list[Call] = []
        self.phases: list[str] = []

    def report_phase_change(self, phase: str) -> None:
        """Record a phase change."""
        self.phases.append(phase)


def _event(payload: Mapping[str, object]) -> AppEvent:
    return AppEvent.model_validate(payload)


def _report(*events: AppEvent, reporter: object | None = None) -> list[Call]:
    target = reporter if reporter is not None else _Recorder()
    _ = asyncio.run(forward_normalized_stream(list(events), reporter=target))
    return cast('_Recorder', target).calls


def _bridge(*events: AppEvent) -> list[tuple[str, dict[str, object]]]:
    bridge = EventBridge('s-forward', schema_validation='off')
    _ = asyncio.run(forward_normalized_stream(list(events), bridge=bridge))
    return [
        (str(event['type']), cast('dict[str, object]', event['data']))
        for event in bridge.get_history()
    ]


def _pick(data: dict[str, object], *keys: str) -> dict[str, object]:
    return {key: data[key] for key in keys}


def _tool(status: str, tool_type: str = 'func', **kwargs: object) -> AppEvent:
    return _event(
        build_tool_event_payload(
            tool_name='lookup',
            tool_id='t1',
            status=status,
            tool_type=tool_type,
            **kwargs,  # pyright: ignore[reportArgumentType]
        )
    )


# MARK: Shared Forwarding State


def test_forwarding_returns_and_reuses_state() -> None:
    """A fresh state is created when omitted and the given one is returned when passed."""
    state = NormalizedEventForwardingState()
    chunk = _event(build_assistant_chunk_payload(assistant_id='a', text='Hi'))

    async def _scenario() -> None:
        assert await forward_normalized_event(chunk, state=state) is state
        fresh = await forward_normalized_event(chunk)
        assert fresh is not state
        assert await forward_normalized_stream([chunk], state=state) is state
        assert await forward_normalized_stream([]) is not state

    asyncio.run(_scenario())


# MARK: Reporter Session And Assistant


def test_session_start_requires_both_ids() -> None:
    """Session starts reach the reporter only with a session and assistant id."""
    calls = _report(
        _event(build_session_start_payload(session_id='s1', assistant_id='a1')),
        _event({'event_name': 'session_start', 'session': {'id': 's2'}}),
        _event({'event_name': 'session_start', 'session': {'id': 's3', 'assistant_id': 'a3'}}),
    )

    assert calls == [('session_start', ('s1', 'a1'), {}), ('session_start', ('s3', 'a3'), {})]


def test_assistant_chunks_accumulate_and_reset_on_replace() -> None:
    """Reporters get each delta with the running full text; ``replace_content`` restarts it."""
    calls = _report(
        _event(build_assistant_chunk_payload(assistant_id='a', text='Hel')),
        _event(build_assistant_chunk_payload(assistant_id='a', text='lo')),
        _event(build_assistant_chunk_payload(assistant_id='b', text='Other')),
        _event(
            build_assistant_chunk_payload(
                assistant_id='a',
                text='New',
                replace_content=True,
                private_value_restorations=[{'path': '/text'}],
            )
        ),
        _event({'event_name': 'assistant_chunk', 'assistant': {'delta': 'anon'}}),
        _event({'event_name': 'assistant_chunk', 'assistant': {'id': 'a', 'delta': ''}}),
    )

    def _chunk(
        text: str,
        assistant_id: str,
        full_text: str,
        *,
        replace: bool = False,
        restorations: object = None,
    ) -> Call:
        return (
            'response_chunk',
            (text,),
            {
                'assistant_id': assistant_id,
                'full_text': full_text,
                'replace_content': replace,
                'private_value_restorations': restorations,
            },
        )

    assert calls == [
        _chunk('Hel', 'a', 'Hel'),
        _chunk('lo', 'a', 'Hello'),
        _chunk('Other', 'b', 'Other'),
        _chunk('New', 'a', 'New', replace=True, restorations=[{'path': '/text'}]),
        _chunk('anon', 'assistant', 'anon'),
    ]


def test_legacy_chunk_reporter_receives_only_declared_keywords() -> None:
    """Optional keywords are withheld from reporters that do not declare them."""
    calls = _report(
        _event(build_assistant_chunk_payload(assistant_id='a', text='Hi', replace_content=True)),
        reporter=_LegacyRecorder(),
    )

    assert calls == [('response_chunk', ('Hi',), {'assistant_id': 'a', 'full_text': 'Hi'})]


def test_status_messages_need_text_and_default_the_assistant() -> None:
    """Status messages fall back to the nested status text and the ``assistant`` id."""
    calls = _report(
        _event(build_status_message_payload(assistant_id='a1', message='Working')),
        _event({'event_name': 'status_message', 'status': {'message': 'Nested'}}),
        _event({'event_name': 'status_message', 'assistant': {'id': 'a2'}}),
    )

    assert calls == [
        ('status_message', ('Working',), {'assistant_id': 'a1'}),
        ('status_message', ('Nested',), {'assistant_id': 'assistant'}),
    ]


# MARK: Reporter Tools


def test_function_tool_lifecycle_reaches_the_reporter() -> None:
    """Executing, completed and failed function tools map to start, complete and error."""
    calls = _report(
        _tool(
            'running', args={'q': 1}, agent_name='ag', swarm_name='sw', private_data_keys=['ssn']
        ),
        _tool('success', result={'ok': True}, duration_ms=12),
        _tool('error', error='boom', duration_ms=3),
        _tool('failed'),
        _tool('paused'),
    )

    assert calls == [
        ('tool_start', ('lookup', 't1', 'func', 'ag', {'q': 1}, 'sw', ['ssn']), {}),
        ('tool_complete', ('t1',), {'elapsed_ms': 12, 'result': {'ok': True}}),
        ('tool_error', ('lookup', 'boom'), {'event_id': 't1', 'elapsed_ms': 3}),
        ('tool_error', ('lookup', 'Unknown error'), {'event_id': 't1', 'elapsed_ms': None}),
    ]


def test_system_tool_events_use_the_system_type() -> None:
    """Tool events typed ``system`` report with that type, and unknown statuses are ignored."""
    calls = _report(
        _tool('executing', tool_type='SYSTEM', args={'q': 1}),
        _tool('completed', tool_type='system', result='r', duration_ms=-1),
        _tool('failed', tool_type='system', error='bad'),
        _tool('waiting', tool_type='system'),
    )

    assert calls == [
        ('tool_start', ('lookup', 't1', 'system', None, {'q': 1}, None, []), {}),
        ('tool_complete', ('t1',), {'elapsed_ms': None, 'result': 'r'}),
        ('tool_error', ('lookup', 'bad'), {'event_id': 't1', 'elapsed_ms': None}),
    ]


def test_model_tools_only_report_their_outcome() -> None:
    """Model tools skip the start and report completion or failure."""
    calls = _report(
        _tool('executing', tool_type='model'),
        _tool('completed', tool_type='model', result={'a': 1}, agent_name='ag', swarm_name='sw'),
        _tool('failed', tool_type='model', duration_ms=5),
    )

    assert calls == [
        (
            'model_tool_complete',
            ('lookup',),
            {'event_id': 't1', 'agent_name': 'ag', 'swarm_name': 'sw', 'result': {'a': 1}},
        ),
        ('tool_error', ('lookup', 'Unknown error'), {'event_id': 't1'}),
    ]


def test_tool_events_without_identity_are_dropped() -> None:
    """A tool event missing its id or status produces no reporter call."""
    calls = _report(
        _event({'event_name': 'tool_event', 'tool': {'name': 'lookup', 'status': 'executing'}}),
        _event({'event_name': 'tool_event', 'tool': {'id': 't1', 'name': 'lookup'}}),
        _event({'event_name': 'system_tool_start', 'tool': {'name': 'web'}}),
        _event({'event_name': 'system_tool_chunk', 'chunk': {'text': 'x'}}),
        _event({'event_name': 'system_tool_complete'}),
        _event({'event_name': 'system_tool_error'}),
    )

    assert calls == []


def test_tool_identity_falls_back_to_event_id_and_scope() -> None:
    """``event_id`` stands in for the tool id and agent/swarm scopes fill attribution."""
    calls = _report(
        _event(
            {
                'event_name': 'tool_event',
                'event_id': 'ev-1',
                'tool': {'name': 'lookup', 'status': 'executing', 'args': {'q': 1}},
                'scope': {'type': 'agent', 'name': ' Researcher '},
            }
        ),
        _event(
            {
                'event_name': 'tool_event',
                'tool_id': 'ev-2',
                'tool_type': 'mcp',
                'status': 'executing',
                'scope': {'type': 'swarm', 'name': 'Crew'},
                'private_data_keys': ['ssn'],
            }
        ),
        _event(
            {
                'event_name': 'tool_event',
                'tool': {
                    'id': 'ev-3',
                    'name': 'lookup',
                    'status': 'completed',
                    'duration_ms': 9,
                    'private_data_keys': ['dob'],
                },
            }
        ),
    )

    assert calls == [
        ('tool_start', ('lookup', 'ev-1', 'func', 'Researcher', {'q': 1}, None, None), {}),
        ('tool_start', ('mcp', 'ev-2', 'mcp', None, None, 'Crew', ['ssn']), {}),
        ('tool_complete', ('ev-3',), {'elapsed_ms': 9, 'result': None}),
    ]


def test_system_tool_stream_events_reach_the_reporter() -> None:
    """Start, cumulative chunks, completion and error share the remembered tool name."""
    calls = _report(
        _event(build_system_tool_start_payload(tool_type='web', tool_id='s1', params={'q': 'x'})),
        _event(build_system_tool_chunk_payload(tool_id='s1', text='ab')),
        _event({'event_name': 'system_tool_chunk', 'tool_id': 's1'}),
        _event(build_system_tool_chunk_payload(tool_id='s1', text='c')),
        _event(build_system_tool_complete_payload(tool_id='s1', result={'r': 1}, duration_ms=7)),
        _event(build_system_tool_chunk_payload(tool_id='s1', text='z')),
        _event(build_system_tool_start_payload(tool_type='calc', tool_id='s2')),
        _event({'event_name': 'system_tool_error', 'tool_id': 's2', 'error': 'bad'}),
        _event({'event_name': 'system_tool_error', 'tool': {'id': 's3', 'error': 'worse'}}),
        _event({'event_name': 'system_tool_error', 'tool_id': 's4', 'tool_name': 'named'}),
    )

    def _progress(count: int, text: str | None, name: str = 'web') -> Call:
        return (
            'system_tool_progress',
            (),
            {
                'event_id': 's1',
                'tool_name': name,
                'chunk_count': count,
                'elapsed_seconds': 0.0,
                'text': text,
            },
        )

    assert calls == [
        ('tool_start', ('web', 's1', 'system', None, {'q': 'x'}, None, None), {}),
        _progress(1, 'ab'),
        _progress(2, None),
        _progress(3, 'abc'),
        ('tool_complete', ('s1',), {'elapsed_ms': 7, 'result': {'r': 1}}),
        _progress(1, 'z', name='system_tool'),
        ('tool_start', ('calc', 's2', 'system', None, {}, None, None), {}),
        ('tool_error', ('calc', 'bad'), {'event_id': 's2', 'elapsed_ms': None}),
        ('tool_error', ('system_tool', 'worse'), {'event_id': 's3', 'elapsed_ms': None}),
        ('tool_error', ('named', 'Unknown error'), {'event_id': 's4', 'elapsed_ms': None}),
    ]


# MARK: Reporter Assignment Enrichment And Hooks


def test_assignments_reach_modern_and_legacy_reporters() -> None:
    """The final-output flag is only passed to callbacks that accept it."""
    events = (
        _event(
            build_agent_assignment_payload(
                agent_name='Researcher',
                status='completed',
                swarm_name='Crew',
                result={'ok': 1},
                use_as_final_output=True,
            )
        ),
        _event({'event_name': 'agent_assignment', 'assignment': {'agent_name': 'X'}}),
        _event(
            {'event_name': 'agent_assignment', 'agent_name': 'Y', 'status': 'failed', 'error': 'e'}
        ),
    )
    base: dict[str, object] = {
        'agent_name': 'Researcher',
        'status': 'completed',
        'assignment_id': 'agent:Researcher',
        'swarm_name': 'Crew',
        'error': None,
        'result': {'ok': 1},
    }
    second: dict[str, object] = {
        'agent_name': 'Y',
        'status': 'failed',
        'assignment_id': 'agent:Y',
        'swarm_name': None,
        'error': 'e',
        'result': None,
    }

    assert _report(*events) == [
        ('agent_assignment', (), {**base, 'use_as_final_output': True}),
        ('agent_assignment', (), {**second, 'use_as_final_output': False}),
    ]
    assert _report(*events, reporter=_LegacyRecorder()) == [
        ('agent_assignment', (), base),
        ('agent_assignment', (), second),
    ]
    assert _report(*events, reporter=_PhaseOnlyRecorder()) == []


def test_enrichment_kwargs_follow_the_reporter_signature() -> None:
    """Modern reporters get scope, memory and redaction; legacy ones only phase and message."""
    events = (
        _event(
            build_enrichment_payload(
                phase='memory',
                message='Recalled',
                scope_id='sc',
                scope_name='Research',
                scope_type='agent',
                memory={'hits': 1},
                redaction={'count': 0},
            )
        ),
        _event({'event_name': 'enrichment', 'enrichment': {'phase': 'evaluating'}}),
        _event({'event_name': 'enrichment', 'enrichment': {'message': 'no phase'}}),
    )

    assert _report(*events) == [
        (
            'enrichment',
            (),
            {
                'phase': 'memory',
                'message': 'Recalled',
                'scope_id': 'sc',
                'scope_name': 'Research',
                'scope_type': 'agent',
                'memory': {'hits': 1},
                'redaction': {'count': 0},
            },
        ),
        (
            'enrichment',
            (),
            {
                'phase': 'evaluating',
                'message': 'evaluating',
                'scope_id': None,
                'scope_name': None,
                'scope_type': None,
            },
        ),
    ]
    assert _report(*events, reporter=_LegacyRecorder()) == [
        ('enrichment', (), {'phase': 'memory', 'message': 'Recalled'}),
        ('enrichment', (), {'phase': 'evaluating', 'message': 'evaluating'}),
    ]
    phase_only = _PhaseOnlyRecorder()
    _ = _report(*events, reporter=phase_only)
    assert phase_only.phases == ['memory', 'evaluating']


def test_hook_firings_pass_source_only_when_accepted() -> None:
    """Hook callbacks receive ``source`` only when they declare it or take ``**kwargs``."""
    full = _event(
        build_hook_fired_payload(
            name='audit',
            stage='before',
            status='failed',
            target_type='tool',
            target_id='t1',
            target_name='lookup',
            source='scope',
            error='nope',
            elapsed_ms=4,
        )
    )
    nested_only = _event(
        {
            'event_name': 'hook_fired',
            'hook': {
                'name': 'h',
                'stage': 'after',
                'status': 'completed',
                'target_type': 'agent',
                'elapsed_ms': 2,
            },
        }
    )
    incomplete = _event({'event_name': 'hook_fired', 'hook': {'name': 'h', 'stage': 'after'}})
    expected: dict[str, object] = {
        'name': 'audit',
        'stage': 'before',
        'status': 'failed',
        'target_type': 'tool',
        'target_id': 't1',
        'target_name': 'lookup',
        'error': 'nope',
        'elapsed_ms': 4,
    }
    nested: dict[str, object] = {
        'name': 'h',
        'stage': 'after',
        'status': 'completed',
        'target_type': 'agent',
        'target_id': None,
        'target_name': None,
        'error': None,
        'elapsed_ms': 2,
    }

    assert _report(full, nested_only, incomplete) == [
        ('hook_fired', (), {**expected, 'source': 'scope'}),
        ('hook_fired', (), nested),
    ]
    assert _report(full, nested_only, incomplete, reporter=_LegacyRecorder()) == [
        ('hook_fired', (), expected),
        ('hook_fired', (), nested),
    ]
    assert _report(full, reporter=_PhaseOnlyRecorder()) == []


# MARK: Reporter Terminal Events


def test_final_event_prints_success_summary_response_and_result() -> None:
    """A final event prints its banner, token usage, response and result in order."""
    calls = _report(
        _event(build_final_payload(response='Done.', result={'a': 1}, token_usage={'total': 5})),
        _event({'event_name': 'final', 'output': {'result': [1]}}),
    )

    assert calls == [
        ('print_event', ('success', 'Agent execution completed successfully!'), {'details': None}),
        ('print_summary', (), {'token_usage': {'total': 5}}),
        ('print_final_response', ('Done.',), {}),
        ('print_final_result', ({'a': 1},), {}),
        ('print_event', ('success', 'Agent execution completed successfully!'), {'details': None}),
        ('print_summary', (), {'token_usage': None}),
        ('print_final_result', ([1],), {}),
    ]


def test_error_event_without_text_reports_unknown_error() -> None:
    """An error with no message is printed as ``Unknown error`` with the generic hint."""
    calls = _report(_event({'event_name': 'error'}))

    assert calls == [
        (
            'print_event',
            (
                'error',
                'Agent execution failed: Unknown error. '
                'Inspect the failed session before submitting more work.',
            ),
            {'details': None},
        ),
    ]


def test_unrouted_events_do_not_reach_the_reporter() -> None:
    """Event names without a reporter route are ignored."""
    calls = _report(
        _event(build_interrupt_required_payload(interrupt_id='i', data_key='k', prompt='P')),
        _event({'event_name': ' '}),
        _event({'event_name': 'custom'}),
    )

    assert calls == []


# MARK: Bridge Forwarding


def test_assistant_status_and_interrupt_forward_to_the_bridge() -> None:
    """Assistant, status and interrupt events become typed bridge emits."""
    events = _bridge(
        _event(build_assistant_chunk_payload(assistant_id='a', text='Hi', replace_content=True)),
        _event({'event_name': 'assistant_chunk', 'assistant': {'delta': 'anon'}}),
        _event({'event_name': 'assistant_chunk'}),
        _event(build_status_message_payload(assistant_id='a', message='Working')),
        _event({'event_name': 'status_message', 'status': {'message': 'Nested'}}),
        _event({'event_name': 'status_message'}),
        _event(
            build_interrupt_required_payload(
                interrupt_id='i1',
                data_key='email',
                prompt='Email?',
                arg_name='addr',
                tool_name='signup',
                checkpoint_id='cp',
                assignment_id='as',
                interrupt_number=2,
                total_interrupts=3,
                input_type='choice',
                choices=['a'],
            )
        ),
        _event(
            {
                'event_name': 'interrupt_required',
                'interrupt': {'id': 'i2', 'data_key': 'k', 'prompt': 'P'},
            }
        ),
        _event({'event_name': 'interrupt_required', 'interrupt': {'id': 'i3', 'prompt': 'P'}}),
    )

    assert [name for name, _ in events] == [
        'assistant_chunk',
        'assistant_chunk',
        'status_message',
        'status_message',
        'interrupt_required',
        'interrupt_required',
    ]
    chunk, anon, status, nested, interrupt, minimal = (data for _, data in events)
    assert _pick(chunk, 'assistant_id', 'text', 'replace_content') == {
        'assistant_id': 'a',
        'text': 'Hi',
        'replace_content': True,
    }
    assert _pick(anon, 'assistant_id', 'text') == {'assistant_id': 'assistant', 'text': 'anon'}
    assert _pick(status, 'assistant_id', 'message') == {'assistant_id': 'a', 'message': 'Working'}
    assert _pick(nested, 'assistant_id', 'message') == {
        'assistant_id': 'assistant',
        'message': 'Nested',
    }
    assert cast('dict[str, object]', interrupt['interrupt']) == {
        'id': 'i1',
        'checkpoint_id': 'cp',
        'assignment_id': 'as',
        'data_key': 'email',
        'arg_name': 'addr',
        'prompt': 'Email?',
        'tool_name': 'signup',
        'input_type': 'choice',
        'choices': ['a'],
        'number': 2,
        'total': 3,
    }
    assert _pick(
        minimal, 'interrupt_id', 'arg_name', 'interrupt_number', 'total_interrupts', 'input_type'
    ) == {
        'interrupt_id': 'i2',
        'arg_name': 'k',
        'interrupt_number': 1,
        'total_interrupts': 1,
        'input_type': 'text',
    }


def test_assignment_and_enrichment_forward_to_the_bridge() -> None:
    """Assignments need a name and status; enrichment needs a phase."""
    events = _bridge(
        _event(
            build_agent_assignment_payload(
                agent_name='Researcher',
                status='completed',
                assignment_id='as-1',
                swarm_name='Crew',
                task='dig',
                error='e',
                result={'ok': 1},
                use_as_final_output=True,
            )
        ),
        _event({'event_name': 'agent_assignment', 'assignment': {'agent_name': 'X'}}),
        _event(
            {
                'event_name': 'enrichment',
                'enrichment': {'phase': 'memory', 'memory': {'m': 1}, 'redaction': {'r': 2}},
                'scope': {'id': 'sc', 'name': 'Research', 'type': 'agent'},
            }
        ),
        _event({'event_name': 'enrichment', 'enrichment': {'message': 'no phase'}}),
    )

    assert [name for name, _ in events] == ['agent_assignment', 'enrichment']
    assignment, enrichment = (data for _, data in events)
    assert _pick(
        assignment,
        'assignment_id',
        'agent_name',
        'status',
        'swarm_name',
        'task',
        'error',
        'result',
        'use_as_final_output',
    ) == {
        'assignment_id': 'as-1',
        'agent_name': 'Researcher',
        'status': 'completed',
        'swarm_name': 'Crew',
        'task': 'dig',
        'error': 'e',
        'result': {'ok': 1},
        'use_as_final_output': True,
    }
    assert _pick(
        enrichment,
        'phase',
        'message',
        'scope_id',
        'scope_name',
        'scope_type',
        'memory',
        'redaction',
    ) == {
        'phase': 'memory',
        'message': 'memory',
        'scope_id': 'sc',
        'scope_name': 'Research',
        'scope_type': 'agent',
        'memory': {'m': 1},
        'redaction': {'r': 2},
    }


@pytest.mark.parametrize(
    ('tool_type', 'statuses', 'expected'),
    [
        (
            'func',
            ['executing', 'completed'],
            [('tool_event', 'executing'), ('tool_event', 'completed')],
        ),
        ('model', ['executing', 'completed'], [('tool_event', 'completed')]),
        ('model', ['failed'], [('tool_event', 'failed')]),
        (
            'system',
            ['executing', 'completed'],
            [('system_tool_start', None), ('system_tool_complete', None)],
        ),
        ('system', ['failed'], [('tool_event', 'failed')]),
        ('system', ['paused'], []),
    ],
)
def test_tool_events_forward_by_type_and_status(
    tool_type: str,
    statuses: list[str],
    expected: list[tuple[str, str | None]],
) -> None:
    """Function tools forward every status; model tools only outcomes; system tools by phase."""
    events = _bridge(*(_tool(status, tool_type=tool_type, result={'r': 1}) for status in statuses))

    assert [(name, data.get('status')) for name, data in events] == expected
    for name, data in events:
        assert data['tool_id'] == 't1'
        if name == 'tool_event':
            assert data['tool_type'] == tool_type
        if name == 'system_tool_start':
            assert data['tool_type'] == 'lookup'
        if name == 'system_tool_complete':
            assert data['result'] == {'r': 1}


def test_system_tool_stream_forwards_to_the_bridge() -> None:
    """System tool start, chunks, completion and errors map to their bridge emits."""
    events = _bridge(
        _event(
            build_system_tool_start_payload(
                tool_type='web', tool_id='s1', params={'q': 'x'}, agent_name='ag'
            )
        ),
        _event(build_system_tool_chunk_payload(tool_id='s1', text='', progress=None)),
        _event(
            {
                'event_name': 'system_tool_chunk',
                'tool': {'id': 's1'},
                'chunk': {'text': 'c', 'progress': 0.5},
            }
        ),
        _event({'event_name': 'system_tool_chunk', 'tool_id': 's1'}),
        _event(build_system_tool_complete_payload(tool_id='s1', result={'r': 1})),
        _event(build_system_tool_start_payload(tool_type='calc', tool_id='s2', swarm_name='sw')),
        _event({'event_name': 'system_tool_error', 'tool_id': 's2', 'tool': {'error': 'bad'}}),
        _event(
            {'event_name': 'system_tool_error', 'tool_id': 's9', 'tool_name': 'named', 'error': 'x'}
        ),
        _event({'event_name': 'system_tool_error', 'tool_id': 's10'}),
        _event({'event_name': 'system_tool_error'}),
        _event({'event_name': 'system_tool_complete'}),
    )

    assert [name for name, _ in events] == [
        'system_tool_start',
        'system_tool_chunk',
        'system_tool_chunk',
        'system_tool_complete',
        'system_tool_start',
        'tool_event',
        'tool_event',
        'tool_event',
    ]
    start, empty, nested, complete, _, failed, named, bare = (data for _, data in events)
    assert _pick(start, 'tool_type', 'tool_id', 'params', 'agent_name') == {
        'tool_type': 'web',
        'tool_id': 's1',
        'params': {'q': 'x'},
        'agent_name': 'ag',
    }
    assert _pick(empty, 'tool_id', 'text', 'progress') == {
        'tool_id': 's1',
        'text': '',
        'progress': None,
    }
    assert _pick(nested, 'tool_id', 'text', 'progress') == {
        'tool_id': 's1',
        'text': 'c',
        'progress': 0.5,
    }
    assert _pick(complete, 'tool_id', 'result') == {'tool_id': 's1', 'result': {'r': 1}}
    assert _pick(failed, 'tool_name', 'tool_id', 'status', 'error', 'tool_type', 'swarm_name') == {
        'tool_name': 'calc',
        'tool_id': 's2',
        'status': 'failed',
        'error': 'bad',
        'tool_type': 'system',
        'swarm_name': 'sw',
    }
    assert _pick(named, 'tool_name', 'error') == {'tool_name': 'named', 'error': 'x'}
    assert _pick(bare, 'tool_name', 'tool_id', 'error') == {
        'tool_name': 'system_tool',
        'tool_id': 's10',
        'error': None,
    }


def test_other_events_are_emitted_verbatim_by_name() -> None:
    """Events without a typed bridge route are emitted under their own name."""
    events = _bridge(
        _event(build_final_payload(response='Done.', result={'a': 1})),
        _event(build_error_payload(error='boom')),
        _event({'event_name': ' custom ', 'extra': 1}),
    )

    assert [name for name, _ in events] == ['final', 'error', 'custom']
    final, error, custom = (data for _, data in events)
    assert _pick(final, 'response', 'result') == {'response': 'Done.', 'result': {'a': 1}}
    assert error['error'] == 'boom'
    assert custom['extra'] == 1
    assert custom['event_name'] == ' custom '


def test_reporter_and_bridge_are_both_fed_from_one_call() -> None:
    """Passing both targets forwards the same event to each."""
    reporter = _Recorder()
    bridge = EventBridge('s-forward')
    event = _event(build_status_message_payload(assistant_id='a', message='Both'))

    _ = asyncio.run(forward_normalized_event(event, reporter=reporter, bridge=bridge))

    assert reporter.calls == [('status_message', ('Both',), {'assistant_id': 'a'})]
    assert [entry['type'] for entry in bridge.get_history()] == ['status_message']


def test_bridge_drops_tool_events_missing_identity_and_reads_payload_progress() -> None:
    """Tool events without id/status and system starts without a name never reach the bridge."""
    events = _bridge(
        _event({'event_name': 'tool_event', 'tool': {'name': 'lookup', 'status': 'executing'}}),
        _event({'event_name': 'tool_event', 'tool': {'id': 't1', 'name': 'lookup'}}),
        _event({'event_name': 'system_tool_start', 'tool': {'id': 's1'}}),
        _event(build_system_tool_chunk_payload(tool_id='s1', text='p', progress=0.25)),
    )

    assert [(name, data.get('progress')) for name, data in events] == [('system_tool_chunk', 0.25)]


def test_reporter_without_enrichment_callbacks_ignores_enrichment() -> None:
    """A reporter with neither enrichment nor phase-change callbacks receives nothing."""

    class _Bare:
        def __init__(self) -> None:
            self.calls: list[Call] = []

    calls = _report(
        _event(build_enrichment_payload(phase='memory', message='m')),
        reporter=_Bare(),
    )

    assert calls == []


def test_system_tool_durations_must_be_non_negative_integers() -> None:
    """Negative or boolean durations are reported as unknown."""
    calls = _report(
        _event({'event_name': 'system_tool_complete', 'tool_id': 's1', 'duration_ms': -5}),
        _event({'event_name': 'system_tool_complete', 'tool': {'id': 's2', 'duration_ms': True}}),
        _event({'event_name': 'system_tool_error', 'tool': {'id': 's3', 'duration_ms': 8}}),
    )

    assert calls == [
        ('tool_complete', ('s1',), {'elapsed_ms': None, 'result': None}),
        ('tool_complete', ('s2',), {'elapsed_ms': None, 'result': None}),
        ('tool_error', ('system_tool', 'Unknown error'), {'event_id': 's3', 'elapsed_ms': 8}),
    ]
