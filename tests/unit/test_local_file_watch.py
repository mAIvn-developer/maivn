"""Real filesystem and HTTP proofs for metadata-only durable file observation."""

from __future__ import annotations

import hashlib
import hmac
import json
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, HTTPServer
from threading import Thread
from typing import TYPE_CHECKING, TypedDict, cast

import httpx
import pytest

from maivn import Agent, Client, ClientConfig
from maivn.connection_events import LocalFileWatcher

if TYPE_CHECKING:
    from collections.abc import Generator
    from pathlib import Path
    from typing import Literal

SECRET = 'file-watch-test-secret'  # noqa: S105 - synthetic fixture.
CALLBACK = '/v1/connections/conn_files/events'
TWO_DELIVERIES = 2


class Clock:
    """Advance quiet time without making real filesystem tests sleep."""

    value = 0.0

    def __call__(self) -> float:
        """Return elapsed test time."""
        return self.value


class WatchOptions(TypedDict):
    """Exact producer options reused by restart fixtures."""

    watch_id: str
    connection_id: str
    receiver_url: str
    secret: str
    state_path: Path
    clock: Clock
    max_pending: int


@contextmanager
def receiver(
    *, lose_ack: bool = False, statuses: tuple[int, ...] = (202,)
) -> Generator[tuple[str, list[bytes]]]:
    """Verify actual raw-byte signatures and optionally lose one accepted ACK."""
    captured: list[bytes] = []

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self) -> None:
            body = self.rfile.read(int(self.headers['Content-Length']))
            captured.append(body)
            timestamp, digest = self.headers['X-Maivn-Signature'].split(',')
            signed = b'POST.' + CALLBACK.encode() + b'.' + timestamp[2:].encode() + b'.' + body
            assert hmac.compare_digest(
                digest[3:], hmac.new(SECRET.encode(), signed, hashlib.sha256).hexdigest()
            )
            if lose_ack and len(captured) == 1:
                self.close_connection = True
                return
            self.send_response(statuses[min(len(captured) - 1, len(statuses) - 1)])
            self.end_headers()

        def log_message(self, format: str, *args: object) -> None:  # noqa: A002 - stdlib override.
            del format, args

    server = HTTPServer(('127.0.0.1', 0), Handler)
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f'http://127.0.0.1:{server.server_port}{CALLBACK}', captured
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


def test_file_declaration_is_inert_and_retains_selector() -> None:
    """A declarative helper names metadata events, never opens a local folder."""
    builder = Agent('files', api_key='fixture').on_file_change(
        'invoices', connection_id='conn_files'
    )
    assert builder.where('$.size_bytes').source.model_dump(mode='json', exclude_none=True) == {
        'kind': 'external_connection_event',
        'phase': 'phase_2',
        'connection_id': 'conn_files',
        'event_type': 'file.created',
        'selector': {'kind': 'local_file', 'watch_id': 'invoices'},
    }


@pytest.mark.parametrize('operation', ['created', 'modified'])
def test_file_registration_transport_retains_selector(operation: str) -> None:
    """The public registration boundary sends both finite selectors with no early I/O."""
    calls: list[httpx.Request] = []

    def receive(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        if request.method == 'GET':
            return httpx.Response(404)
        payload = json.loads(request.content)
        return httpx.Response(
            201,
            json={
                'scenario': {
                    'scenario': {
                        'scenario_id': payload['scenario_id'],
                        'project_id': 'prj-sdk',
                        'trigger_id': payload['trigger_id'],
                        'name': payload['name'],
                    },
                    'trigger': {
                        'trigger_id': payload['trigger_id'],
                        'name': payload['name'],
                        'status': 'enabled',
                        'source': payload['trigger_source'],
                    },
                }
            },
        )

    client = Client(
        config=ClientConfig.from_sources(
            api_key='fixture',
            base_url='https://data.example',
        ),
        transport=httpx.MockTransport(receive),
    )
    agent = Agent('files', client=client)
    builder = (
        agent.on_file_change(
            'invoices',
            connection_id='conn_files',
            operation=cast("Literal['created', 'modified']", operation),
        )
        .key('file-event')
        .target('agent', version=1)
    )
    assert calls == []
    _ = builder.invoke('Handle file metadata.')
    assert [request.method for request in calls] == ['GET', 'POST']
    assert json.loads(calls[-1].content)['trigger_source'] == {
        'kind': 'external_connection_event',
        'phase': 'phase_2',
        'connection_id': 'conn_files',
        'event_type': f'file.{operation}',
        'selector': {'kind': 'local_file', 'watch_id': 'invoices'},
    }


def test_baseline_atomic_rename_quiet_coalescing_and_metadata_only(tmp_path: Path) -> None:
    """Real writes only emit final matching files, never contents or absolute paths."""
    root = tmp_path / 'incoming'
    root.mkdir()
    (root / 'existing.pdf').write_text('baseline')
    clock = Clock()
    with (
        receiver() as (url, bodies),
        LocalFileWatcher(
            root,
            watch_id='invoices',
            connection_id='conn_files',
            receiver_url=url,
            secret=SECRET,
            state_path=tmp_path / 'private' / 'state.sqlite3',
            pattern='*.pdf',
            clock=clock,
        ) as watcher,
    ):
        assert watcher.scan().queued_events == 0
        temp = root / 'invoice.tmp'
        temp.write_text('SECRET-CONTENT-SENTINEL')
        watcher.scan()
        with temp.open('a') as stream:
            _ = stream.write(' chunk')
        temp.rename(root / 'invoice.pdf')
        assert watcher.scan().queued_events == 0
        clock.value = 0.5
        assert watcher.scan().queued_events == 0
        clock.value = 1.1
        assert watcher.scan().queued_events == 1
        assert watcher.deliver_pending().accepted_events == 1
        event = json.loads(bodies[0])
        assert set(event) == {
            'event_id',
            'watch_id',
            'operation',
            'observed_at',
            'relative_path',
            'size_bytes',
            'mtime_ns',
        }
        assert event['operation'] == 'created'
        assert event['relative_path'] == 'invoice.pdf'
        assert isinstance(event['mtime_ns'], str)
        assert b'SECRET-CONTENT-SENTINEL' not in bodies[0]
        assert str(root).encode() not in bodies[0]
        (root / 'invoice.pdf').write_text('new')
        watcher.scan()
        clock.value = 1.6
        (root / 'invoice.pdf').write_text('newer chunks')
        watcher.scan()
        clock.value = 2.7
        assert watcher.scan().queued_events == 1
        assert watcher.deliver_pending().accepted_events == 1
        assert json.loads(bodies[1])['operation'] == 'modified'


def test_journal_restart_and_lost_ack_reuse_exact_body(tmp_path: Path) -> None:
    """A lost acknowledgement and a process restart retain one signed event identity."""
    root = tmp_path / 'incoming'
    root.mkdir()
    state = tmp_path / 'private' / 'state.sqlite3'
    clock = Clock()
    with receiver(lose_ack=True) as (url, bodies):
        options: WatchOptions = {
            'watch_id': 'invoices',
            'connection_id': 'conn_files',
            'receiver_url': url,
            'secret': SECRET,
            'state_path': state,
            'clock': clock,
            'max_pending': 10000,
        }
        with LocalFileWatcher(root, **options) as watcher:
            watcher.scan()
            (root / 'invoice.pdf').write_text('private')
            watcher.scan()
            clock.value = 2.0
            assert watcher.scan().queued_events == 1
        with LocalFileWatcher(root, **options) as watcher:
            assert watcher.pending_count == 1
            assert watcher.deliver_pending().accepted_events == 0
            assert watcher.pending_count == 1
        with LocalFileWatcher(root, **options) as watcher:
            assert watcher.deliver_pending().accepted_events == 1
            assert watcher.pending_count == 0
            assert watcher.scan().queued_events == 0
        assert len(bodies) == TWO_DELIVERIES
        assert bodies[0] == bodies[1]


def test_marker_requires_new_completion_after_each_change(tmp_path: Path) -> None:
    """A stale marker cannot authorize a subsequent modified file."""
    root = tmp_path / 'incoming'
    root.mkdir()
    clock = Clock()
    with (
        receiver() as (url, bodies),
        LocalFileWatcher(
            root,
            watch_id='invoices',
            connection_id='conn_files',
            receiver_url=url,
            secret=SECRET,
            state_path=tmp_path / 'private' / 'state.sqlite3',
            completion_suffix='.ready',
            clock=clock,
        ) as watcher,
    ):
        watcher.scan()
        target = root / 'invoice.pdf'
        target.write_text('initial')
        watcher.scan()
        clock.value = 2.0
        assert watcher.scan().queued_events == 0
        marker = root / 'invoice.pdf.ready'
        marker.write_text('')
        watcher.scan()
        clock.value = 4.0
        assert watcher.scan().queued_events == 1
        watcher.deliver_pending()
        target.write_text('changed')
        clock.value = 6.0
        watcher.scan()
        clock.value = 8.0
        assert watcher.scan().queued_events == 0
        marker.unlink()
        marker.write_text('new marker')
        watcher.scan()
        clock.value = 10.0
        assert watcher.scan().queued_events == 1
        watcher.deliver_pending()
        assert [json.loads(body)['operation'] for body in bodies] == ['created', 'modified']


def test_single_writer_and_queue_backpressure(tmp_path: Path) -> None:
    """Full queues retain the unjournaled cursor and a second writer cannot open state."""
    root = tmp_path / 'incoming'
    root.mkdir()
    clock = Clock()
    with receiver() as (url, bodies):
        options: WatchOptions = {
            'watch_id': 'invoices',
            'connection_id': 'conn_files',
            'receiver_url': url,
            'secret': SECRET,
            'state_path': tmp_path / 'private' / 'state.sqlite3',
            'clock': clock,
            'max_pending': 1,
        }
        with LocalFileWatcher(root, **options) as watcher:
            with pytest.raises(RuntimeError, match='already'), LocalFileWatcher(root, **options):
                pass
            watcher.scan()
            (root / 'a.pdf').write_text('a')
            (root / 'b.pdf').write_text('b')
            watcher.scan()
            clock.value = 2.0
            result = watcher.scan()
            assert result.queued_events == 1
            assert result.queue_full is True
            watcher.deliver_pending()
            assert watcher.scan().queued_events == 1
            watcher.deliver_pending()
            assert {json.loads(body)['relative_path'] for body in bodies} == {'a.pdf', 'b.pdf'}
