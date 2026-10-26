"""Characterization tests for the shared ``BaseScope`` schedule and trigger helpers.

Driven through ``maivn.Agent`` with a ``MockTransport`` client; declaring a
trigger never sends anything, and any accidental request would get a 500.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import httpx
import pytest
from maivn_contracts.scenarios import ScheduleSource
from pydantic import AnyUrl

from maivn import (
    Agent,
    Client,
    ClientConfig,
    CronInvocationBuilder,
    JitterSpec,
    TriggerInvocationBuilder,
)


def _agent() -> Agent:
    client = Client(
        config=ClientConfig(api_key='test-key', base_url=AnyUrl('https://data.example')),
        transport=httpx.MockTransport(
            lambda _request: httpx.Response(500, json={'detail': 'Unexpected.'}),
        ),
    )
    return Agent(name='scheduler', client=client)


def _schedule(builder: TriggerInvocationBuilder) -> dict[str, object]:
    source = builder.source
    assert isinstance(source, ScheduleSource)
    return source.model_dump(mode='json', exclude_none=True)


def test_remote_cron_defaults_to_utc_and_sixty_seconds_of_jitter() -> None:
    """A remote cron keeps the bare expression for UTC and jitters 60s by default."""
    agent = _agent()
    expected = {
        'kind': 'schedule',
        'phase': 'phase_1',
        'cron': '0 9 * * *',
        'jitter_seconds': 60,
    }
    assert _schedule(agent.cron('0 9 * * *')) == expected
    assert _schedule(agent.cron('0 9 * * *', tz='UTC')) == expected
    assert _schedule(agent.cron('0 9 * * *', tz=timezone.utc)) == expected


def test_remote_cron_refuses_a_fixed_offset_timezone() -> None:
    """Only UTC or an IANA name can describe a remote cron timezone."""
    with pytest.raises(ValueError, match=r'^') as excinfo:
        _ = _agent().cron('0 9 * * *', tz=timezone(timedelta(hours=2)))
    assert str(excinfo.value) == 'remote cron timezone must be UTC or an IANA timezone name'


def test_remote_cron_validates_the_expression_for_named_timezones() -> None:
    """A named timezone forces local validation of the cron expression."""
    with pytest.raises(ValueError, match=r'^') as excinfo:
        _ = _agent().cron('not a cron', tz='Europe/Paris')
    assert str(excinfo.value) == "Invalid cron expression: 'not a cron'"


def test_remote_interval_requires_whole_seconds() -> None:
    """Fractional remote intervals are refused; whole ones become integer seconds."""
    agent = _agent()
    with pytest.raises(ValueError, match=r'^') as excinfo:
        _ = agent.interval(timedelta(seconds=1.5))
    assert str(excinfo.value) == 'remote trigger intervals require whole seconds'
    assert _schedule(agent.interval(timedelta(minutes=2), jitter=timedelta(seconds=7))) == {
        'kind': 'schedule',
        'phase': 'phase_1',
        'interval_seconds': 120,
        'jitter_seconds': 7,
    }


@pytest.mark.parametrize(
    ('jitter', 'expected'),
    [
        (0, 0),
        (5.0, 5),
        (timedelta(seconds=9), 9),
        (JitterSpec(min=timedelta(seconds=-12), max=timedelta(seconds=4)), 12),
    ],
)
def test_remote_jitter_shorthands_become_whole_seconds(jitter: object, expected: int) -> None:
    """Numbers, timedeltas and specs map to one non-negative amplitude."""
    builder = _agent().interval(timedelta(hours=1), jitter=jitter)  # pyright: ignore[reportArgumentType]
    assert _schedule(builder)['jitter_seconds'] == expected


@pytest.mark.parametrize('jitter', [-1, 1.5, timedelta(milliseconds=500)])
def test_remote_jitter_must_be_non_negative_whole_seconds(jitter: object) -> None:
    """Negative or fractional jitter is refused for remote schedules."""
    with pytest.raises(ValueError, match=r'^') as excinfo:
        _ = _agent().cron('* * * * *', jitter=jitter)  # pyright: ignore[reportArgumentType]
    assert str(excinfo.value) == 'remote schedules require jitter as non-negative whole seconds'


def test_remote_schedules_refuse_local_only_options_by_name() -> None:
    """Local-only options are listed alphabetically; None values are ignored."""
    agent = _agent()
    with pytest.raises(ValueError, match=r'^') as excinfo:
        _ = agent.interval(
            timedelta(hours=1),
            max_runs=3,
            misfire='skip',
            retry=None,
            emit_events=True,
        )
    assert (
        str(excinfo.value) == 'unsupported remote schedule options: emit_events, max_runs, misfire'
    )
    with pytest.raises(ValueError, match=r'^') as cron_excinfo:
        _ = agent.cron('* * * * *', overlap_policy='queue')
    assert str(cron_excinfo.value) == 'unsupported remote schedule options: overlap_policy'
    assert _schedule(agent.cron('* * * * *', start_at=None))['cron'] == '* * * * *'


def test_local_modes_and_at_return_compatibility_builders() -> None:
    """``mode='local'`` and ``at`` build in-process builders that start nothing yet."""
    agent = _agent()
    local_cron = agent.cron('*/5 * * * *', mode='local', max_runs=1)
    local_interval = agent.interval(timedelta(milliseconds=250), mode='local')
    one_shot = agent.at(datetime(2099, 1, 1, tzinfo=timezone.utc), name='later')
    for builder in (local_cron, local_interval, one_shot):
        assert isinstance(builder, CronInvocationBuilder)
    assert agent.declared_triggers == []


@pytest.mark.parametrize(
    ('kwargs', 'message'),
    [
        (
            {'table': None, 'connection_id': 'conn', 'operation': 'insert'},
            'database changes require a table and cannot name a memory record_kind',
        ),
        (
            {
                'table': 'public.orders',
                'connection_id': 'conn',
                'operation': 'insert',
                'record_kind': 'insight',
            },
            'database changes require a table and cannot name a memory record_kind',
        ),
        (
            {'table': 'public.orders', 'connection_id': 'conn', 'operation': 'upsert'},
            'database operation must be insert, update or delete',
        ),
        (
            {'table': 'insight', 'operation': 'inserted', 'record_kind': 'insight'},
            'provide either a legacy record_kind or a database table, not both',
        ),
        (
            {'table': 'public.orders', 'operation': 'update'},
            'database record changes require connection_id',
        ),
    ],
)
def test_record_change_refuses_ambiguous_selectors(kwargs: dict[str, str], message: str) -> None:
    """Database and memory record selectors cannot be mixed or half-specified."""
    with pytest.raises(ValueError, match=r'^') as excinfo:
        _ = _agent().on_record_change(**kwargs)  # pyright: ignore[reportArgumentType]
    assert str(excinfo.value) == message


def test_legacy_positional_record_kind_becomes_a_memory_trigger() -> None:
    """A positional memory record kind keeps meaning a platform-memory trigger."""
    source = _agent().on_record_change('skill', operation='updated').source
    assert source.model_dump(mode='json') == {
        'kind': 'memory_event',
        'phase': 'phase_3',
        'record_kind': 'skill',
        'operation': 'updated',
    }


def test_declared_schedules_are_recorded_in_order() -> None:
    """Remote schedule declarations accumulate on the scope."""
    agent = _agent()
    first = agent.cron('0 1 * * *')
    second = agent.interval(timedelta(hours=2), name='Every two hours')
    assert agent.declared_triggers == [first, second]
    agent.close()
