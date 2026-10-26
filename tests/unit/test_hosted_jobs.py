"""SDK jobs require explicit bounds and never imply local tool continuity."""

# ruff: noqa: PLR2004 - explicit limits and established private test fixtures.
from __future__ import annotations

import asyncio
import json
from typing import Any, cast

import httpx
import pytest

from maivn import Client, HostedJobOptions, RunOptions
from maivn._internal.config import ClientConfig
from maivn._internal.hosted_jobs import HostedJobsClient

_SESSION = 'job-' + 'a' * 32
_STATUS = {
    'session_id': _SESSION,
    'state': 'running',
    'deadline_at': '2026-10-22T23:00:00Z',
    'max_steps': 4,
    'steps_used': 0,
    'max_expense_usd': '1.00',
    'progress_position': 0,
}


class Http:
    """Exercise Http."""

    def __init__(self) -> None:
        """Bind the focused test fixture."""
        self.calls: list[tuple[str, str, Any]] = []

    async def post(self, path: str, payload: Any) -> dict[str, Any]:
        """Exercise post."""
        self.calls.append(('post', path, payload))
        return _STATUS

    async def get(self, path: str) -> dict[str, Any]:
        """Exercise get."""
        self.calls.append(('get', path, None))
        return _STATUS


def test_jobs_start_read_cancel_use_dedicated_durable_paths() -> None:
    """Exercise test jobs start read cancel use dedicated durable paths."""
    http = Http()
    jobs = HostedJobsClient(
        lambda: cast('Any', http), lambda x: cast('RunOptions', x), lambda: 'offline-only'
    )
    bounds = HostedJobOptions(idempotency_key='once', max_steps=4, max_duration_seconds=30)

    async def run() -> None:
        """Exercise run."""
        assert (
            await jobs.astart(['Draft'], job=bounds, options=RunOptions(max_expense_usd='1.00'))
        ).session_id == _SESSION
        assert (await jobs.aget(_SESSION)).state == 'running'
        await jobs.acancel(_SESSION)

    asyncio.run(run())
    assert [entry[:2] for entry in http.calls] == [
        ('post', '/v1/jobs'),
        ('get', '/v1/jobs/' + _SESSION),
        ('post', '/v1/jobs/' + _SESSION + '/cancel'),
    ]
    payload = http.calls[0][2]
    assert payload['max_steps'] == 4
    assert payload['invoke']['run_config']['max_expense_usd'] == '1.00'
    assert payload['invoke'].get('tools', []) == []


def test_jobs_without_expense_cap_fail_locally_before_http() -> None:
    """Exercise test jobs without expense cap fail locally before http."""
    http = Http()
    jobs = HostedJobsClient(
        lambda: cast('Any', http), lambda x: cast('RunOptions', x), lambda: 'offline-only'
    )
    with pytest.raises(ValueError, match='max_expense_usd'):
        asyncio.run(
            jobs.astart(
                ['Draft'],
                job=HostedJobOptions(idempotency_key='once', max_steps=4, max_duration_seconds=30),
                options=RunOptions(),
            )
        )
    assert http.calls == []


def test_sync_entry_points_use_the_same_durable_paths() -> None:
    """start, get and cancel outside an event loop reach the same server paths."""
    http = Http()
    jobs = HostedJobsClient(
        lambda: cast('Any', http), lambda x: cast('RunOptions', x), lambda: 'offline-only'
    )
    bounds = HostedJobOptions(idempotency_key='once', max_steps=4, max_duration_seconds=30)
    assert jobs.start(['Draft'], job=bounds, options=RunOptions(max_expense_usd='1.00')).session_id
    assert jobs.get(_SESSION).state == 'running'
    jobs.cancel(_SESSION)
    assert [entry[:2] for entry in http.calls] == [
        ('post', '/v1/jobs'),
        ('get', '/v1/jobs/' + _SESSION),
        ('post', '/v1/jobs/' + _SESSION + '/cancel'),
    ]


def test_a_caller_chosen_job_session_fails_locally_before_http() -> None:
    """The server assigns job session identities; a caller cannot pick one."""
    http = Http()
    jobs = HostedJobsClient(
        lambda: cast('Any', http), lambda x: cast('RunOptions', x), lambda: 'offline-only'
    )
    with pytest.raises(ValueError, match='server assigned'):
        asyncio.run(
            jobs.astart(
                ['Draft'],
                job=HostedJobOptions(idempotency_key='once', max_steps=4, max_duration_seconds=30),
                options=RunOptions(max_expense_usd='1.00', session_id='job-mine'),
            )
        )
    assert http.calls == []


@pytest.mark.parametrize(
    'session_id',
    ['ses-' + 'a' * 32, 'job-' + 'a' * 31, 'job-' + 'A' * 32, 'job-' + 'a' * 31 + '/', '../jobs'],
)
def test_job_paths_accept_only_server_job_identities(session_id: str) -> None:
    """A malformed identity never becomes a request path."""
    http = Http()
    jobs = HostedJobsClient(
        lambda: cast('Any', http), lambda x: cast('RunOptions', x), lambda: 'offline-only'
    )
    with pytest.raises(ValueError, match='invalid hosted job session identity'):
        asyncio.run(jobs.aget(session_id))
    with pytest.raises(ValueError, match='invalid hosted job session identity'):
        asyncio.run(jobs.acancel(session_id))
    assert http.calls == []


def test_default_job_threads_are_credential_scoped_and_explicit_threads_win() -> None:
    """Common idempotency keys never claim another credential's global thread."""
    payloads: list[dict[str, Any]] = []

    def reply(request: httpx.Request) -> httpx.Response:
        payloads.append(json.loads(request.content))
        return httpx.Response(200, json=_STATUS)

    async def run() -> None:
        clients = [
            Client(
                config=ClientConfig.model_validate(
                    {'api_key': value, 'base_url': 'http://offline.test'}
                ),
                transport=httpx.MockTransport(reply),
                private_data_store=None,
                auto_detect_timezone=False,
            )
            for value in ('offline-tenant-a', 'offline-tenant-b')
        ]
        job = HostedJobOptions(idempotency_key='nightly', max_steps=4, max_duration_seconds=30)
        options = RunOptions(max_expense_usd='1')
        for client in (clients[0], clients[0], clients[1]):
            await client.jobs.astart(['Draft'], job=job, options=options)
        threads = [p['invoke']['run_config']['thread_id'] for p in payloads]
        assert threads[0] == threads[1]
        assert threads[0] != threads[2]
        assert all('offline-tenant' not in thread for thread in threads)
        clients[0].config = clients[0].config.model_copy(update={'thread_id': 'configured-thread'})
        await clients[0].jobs.astart(['Draft'], job=job, options=options)
        assert payloads[-1]['invoke']['run_config']['thread_id'] == 'configured-thread'
        await clients[0].jobs.astart(
            ['Draft'], job=job, options=RunOptions(max_expense_usd='1', thread_id='explicit-thread')
        )
        assert payloads[-1]['invoke']['run_config']['thread_id'] == 'explicit-thread'

    asyncio.run(run())
