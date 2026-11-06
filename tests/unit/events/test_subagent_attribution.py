"""Delegated child runs are attributed to a named sub-agent.

A swarm member or delegated agent runs as a child session whose events reach the
parent's stream stamped with ``parent_session_id``. The normalizer turns the child's
start and end into an ``agent_assignment`` keyed by the child session, and re-keys
the child's streamed text to that assignment, so a UI never shows sub-agent text as
the run's answer and parallel members never overwrite each other.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, cast

from maivn._internal.models import StreamEvent
from maivn._internal.reporting.stream_events import StreamEventProjector
from maivn.events import NormalizedStreamState, RawSSEEvent, normalize_stream_event

if TYPE_CHECKING:
    from maivn._internal.models import JsonObject

ROOT = 'ses-root'
CHILD_A = 'ses-child-a'
CHILD_B = 'ses-child-b'


class _Stream:
    """One parent stream: canonical events projected and normalized in order."""

    def __init__(self) -> None:
        self._projector = StreamEventProjector()
        self._state = NormalizedStreamState()
        self._position = 0

    def send(
        self,
        event_type: str,
        payload: JsonObject,
        *,
        session_id: str = ROOT,
        parent_session_id: str | None = None,
        parent_in_payload: bool = False,
    ) -> list[dict[str, object]]:
        data: JsonObject = {
            'event_id': f'evt-{self._position}',
            'type': event_type,
            'session_id': session_id,
            'job_depth': 0 if parent_session_id is None else 1,
            'payload': payload,
        }
        if parent_session_id is not None:
            if parent_in_payload:
                payload['parent_session_id'] = parent_session_id
            else:
                data['parent_session_id'] = parent_session_id
        event = self._projector.project(
            StreamEvent(position=self._position, event_type=event_type, data=data)
        )
        self._position += 1
        normalized = normalize_stream_event(cast('RawSSEEvent', event), state=self._state)
        return [item.model_dump(exclude_none=True) for item in normalized]


def _received(agent_id: str, swarm_id: str = 'Research Assistant') -> JsonObject:
    return {
        'model': 'fast',
        'stage': 'session_started',
        'run_config': {
            'agent_id': agent_id,
            'swarm_id': swarm_id,
            'job_depth': 1,
            'child_run_kind': 'swarm_member',
        },
        'message_count': 2,
    }


def _chunk(message_id: str, delta: str) -> JsonObject:
    return {'stage': 'model_stream', 'message_id': message_id, 'content_delta': delta}


def _assignment(event: dict[str, object]) -> dict[str, object]:
    assert event['event_name'] == 'agent_assignment'
    return cast('dict[str, object]', event['assignment'])


def _assistant_text(events: list[dict[str, object]]) -> list[tuple[str, str]]:
    return [
        (cast('str', event['assistant_id']), cast('str', event['text']))
        for event in events
        if event['event_name'] == 'assistant_chunk'
    ]


# MARK: Assignment Lifecycle


def test_child_start_becomes_a_named_in_progress_assignment() -> None:
    """``assignment_received`` from a child opens a sub-agent keyed by its session."""
    stream = _Stream()

    events = stream.send(
        'assignment_received',
        _received('case_law_analyst'),
        session_id=CHILD_A,
        parent_session_id=ROOT,
    )

    assert len(events) == 1
    assignment = _assignment(events[0])
    assert events[0]['is_subagent'] is True
    assert assignment['is_subagent'] is True
    assert assignment['id'] == CHILD_A
    assert assignment['agent_name'] == 'Case law analyst'
    assert assignment['status'] == 'in_progress'
    assert assignment['swarm_name'] == 'Research Assistant'


def test_child_end_reuses_the_name_from_its_start() -> None:
    """``assignment_completed`` carries no run config, so the start's name is kept."""
    stream = _Stream()
    stream.send(
        'assignment_received',
        _received('Report Writer'),
        session_id=CHILD_A,
        parent_session_id=ROOT,
    )

    events = stream.send(
        'assignment_completed',
        {'stage': 'assignment_completed', 'status': 'success', 'assignment_uuid': 'a-1'},
        session_id=CHILD_A,
        parent_session_id=ROOT,
    )

    assignment = _assignment(events[-1])
    assert assignment['id'] == CHILD_A
    assert assignment['agent_name'] == 'Report Writer'
    assert assignment['status'] == 'completed'
    assert assignment['swarm_name'] == 'Research Assistant'


def test_child_failure_closes_the_assignment_as_failed() -> None:
    """The platform reports a failed child with ``status: failure``."""
    stream = _Stream()
    stream.send(
        'assignment_received',
        _received('researcher'),
        session_id=CHILD_A,
        parent_session_id=ROOT,
    )

    events = stream.send(
        'assignment_completed',
        {'stage': 'assignment_completed', 'status': 'failure'},
        session_id=CHILD_A,
        parent_session_id=ROOT,
    )

    assert _assignment(events[-1])['status'] == 'failed'


def test_child_end_without_a_seen_start_falls_back_to_a_generic_name() -> None:
    """A stream joined mid-run still closes the sub-agent under a readable label."""
    events = _Stream().send(
        'assignment_completed',
        {'stage': 'assignment_completed', 'status': 'success'},
        session_id=CHILD_A,
        parent_session_id=ROOT,
    )

    assignment = _assignment(events[-1])
    assert assignment['agent_name'] == 'Sub-agent'
    assert assignment['status'] == 'completed'


def test_parent_session_id_inside_the_payload_marks_a_child() -> None:
    """A delegated child may stamp ``parent_session_id`` inside the payload only."""
    events = _Stream().send(
        'assignment_received',
        _received('case_law_analyst'),
        session_id=CHILD_A,
        parent_session_id=ROOT,
        parent_in_payload=True,
    )

    assert _assignment(events[0])['id'] == CHILD_A


def test_root_assignment_events_stay_silent() -> None:
    """The run's own start and end are not sub-agents."""
    stream = _Stream()

    assert stream.send('assignment_received', _received('orchestrator')) == []
    assert stream.send('assignment_completed', {'status': 'success'}) == []


# MARK: Streamed Text


def test_child_text_is_keyed_to_its_sub_agent() -> None:
    """A child's streamed text belongs to its assignment, not the run's answer."""
    stream = _Stream()
    stream.send(
        'assignment_received',
        _received('case_law_analyst'),
        session_id=CHILD_A,
        parent_session_id=ROOT,
    )

    events = [
        *stream.send(
            'status_message_chunk',
            _chunk('msg-a', 'Prece'),
            session_id=CHILD_A,
            parent_session_id=ROOT,
        ),
        *stream.send(
            'status_message_chunk',
            _chunk('msg-a', 'dent'),
            session_id=CHILD_A,
            parent_session_id=ROOT,
        ),
    ]

    assert _assistant_text(events) == [(CHILD_A, 'Prece'), (CHILD_A, 'dent')]
    assert all(cast('dict[str, object]', event['assistant'])['id'] == CHILD_A for event in events)


def test_parallel_children_stream_into_separate_sub_agents() -> None:
    """Interleaved members keep their own text instead of overwriting each other."""
    stream = _Stream()
    events: list[dict[str, object]] = []
    for session_id, message_id, delta in (
        (CHILD_A, 'msg-a', 'Case '),
        (CHILD_B, 'msg-b', 'Filing '),
        (CHILD_A, 'msg-a', 'law'),
        (CHILD_B, 'msg-b', 'trends'),
    ):
        events += stream.send(
            'status_message_chunk',
            _chunk(message_id, delta),
            session_id=session_id,
            parent_session_id=ROOT,
        )

    assert _assistant_text(events) == [
        (CHILD_A, 'Case '),
        (CHILD_B, 'Filing '),
        (CHILD_A, 'law'),
        (CHILD_B, 'trends'),
    ]


def test_root_text_keeps_its_message_key() -> None:
    """The run's own answer is projected exactly as before."""
    events = _Stream().send('status_message_chunk', _chunk('msg-root', 'Answer'))

    assert _assistant_text(events) == [('msg-root', 'Answer')]


def test_raw_sse_events_are_never_treated_as_children() -> None:
    """A wire event without a session envelope has no child identity to attribute."""
    events = normalize_stream_event(
        RawSSEEvent(
            name='update',
            payload={
                'assistant_id': 'msg-1',
                'streaming_content': 'Hi',
                'parent_session_id': ROOT,
            },
        )
    )

    dumped = [event.model_dump(exclude_none=True) for event in events]
    assert _assistant_text(dumped) == [('msg-1', 'Hi')]
