"""Characterize local file observation: ``maivn watch-files``, the watcher and its journal.

Real files under ``tmp_path`` and a real loopback receiver are used throughout;
quiet periods are driven by an injected clock instead of sleeping. The CLI loop
is stopped by making its pause raise ``KeyboardInterrupt`` - exactly what Ctrl-C
does to a sleeping process.
"""

from __future__ import annotations

import hashlib
import hmac
import io
import json
import math
import os
import sys
import threading
import time
from contextlib import contextmanager
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, HTTPServer
from typing import TYPE_CHECKING

import pytest

from maivn import cli
from maivn.connection_events import FileScanLimitError, LocalFileWatcher
from maivn.files import generated_file

if TYPE_CHECKING:
    from collections.abc import Generator
    from pathlib import Path

SECRET = 'watch-characterization-secret'  # noqa: S105 - synthetic signing secret.
CONNECTION = 'conn_char'
CALLBACK = f'/v1/connections/{CONNECTION}/events'
GENERIC_FAILURE = (
    'File watcher could not continue. Check the selected root, private state directory, '
    'configuration and signing secret; pending events are retained.\n'
)
STOPPED = '{"status":"stopped","pending_preserved":true}\n'
SCAN_LIMIT = '{"status":"scan_limit","pending_preserved":true}\n'


class Clock:
    """A monotonic clock the test advances by hand."""

    def __init__(self) -> None:
        """Start at zero."""
        self.value = 0.0

    def __call__(self) -> float:
        """Return the current test time."""
        return self.value


@contextmanager
def receiver(statuses: tuple[int, ...] = (202,)) -> Generator[tuple[str, list[bytes]]]:
    """Accept signed deliveries, answering with each status in turn (the last repeats)."""
    bodies: list[bytes] = []

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self) -> None:
            body = self.rfile.read(int(self.headers['Content-Length']))
            bodies.append(body)
            timestamp, _, signature = self.headers['X-Maivn-Signature'].partition(',')
            signed = f'POST.{CALLBACK}.{timestamp.removeprefix("t=")}.'.encode() + body
            expected = hmac.new(SECRET.encode(), signed, hashlib.sha256).hexdigest()
            if not hmac.compare_digest(signature, f'v1={expected}'):
                self.send_response(HTTPStatus.UNAUTHORIZED)
                self.send_header('Content-Length', '0')
                self.end_headers()
                return
            self.send_response(statuses[min(len(bodies), len(statuses)) - 1])
            self.send_header('Content-Length', '0')
            self.end_headers()

        def log_message(self, format: str, *args: object) -> None:  # noqa: A002 - stdlib override.
            del format, args

    server = HTTPServer(('127.0.0.1', 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f'http://127.0.0.1:{server.server_port}', bodies
    finally:
        server.shutdown()
        server.server_close()
        thread.join()


def _root(tmp_path: Path) -> Path:
    root = tmp_path / 'incoming'
    root.mkdir()
    return root


def _watcher(tmp_path: Path, root: Path, **options: object) -> LocalFileWatcher:
    settings: dict[str, object] = {
        'watch_id': 'invoices',
        'connection_id': CONNECTION,
        'receiver_url': f'http://127.0.0.1:9{CALLBACK}',
        'secret': SECRET,
        'state_path': tmp_path / 'private' / 'state.sqlite3',
    }
    settings.update(options)
    return LocalFileWatcher(root, **settings)  # pyright: ignore[reportArgumentType]


# --- the watch-files command ------------------------------------------------------------


def _status(status: str, queued: int, accepted: int, pending: int, http: int | None) -> str:
    return (
        json.dumps(
            {
                'status': status,
                'queued_events': queued,
                'accepted_events': accepted,
                'pending_events': pending,
                'http_status': http,
            },
            sort_keys=True,
        )
        + '\n'
    )


def _command(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, root: Path, *extra: str
) -> int | str | None:
    argv = [
        'maivn',
        'watch-files',
        str(root),
        '--watch-id',
        'invoices',
        '--connection-id',
        CONNECTION,
        '--state-file',
        str(tmp_path / 'private' / 'state.sqlite3'),
        *extra,
    ]
    monkeypatch.setattr(sys, 'argv', argv)
    try:
        cli.main()
    except SystemExit as stopped:
        return stopped.code
    return None


def _interrupt_after(monkeypatch: pytest.MonkeyPatch, pauses: int) -> list[float]:
    """Let the loop pause `pauses` times, then deliver Ctrl-C on the next pause."""
    seen: list[float] = []

    def pause(seconds: float) -> None:
        seen.append(seconds)
        if len(seen) > pauses:
            raise KeyboardInterrupt

    monkeypatch.setattr(time, 'sleep', pause)
    return seen


def test_command_loop_prints_only_changed_progress_and_stops_on_ctrl_c(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """Progress lines repeat only on change; the default receiver follows MAIVN_BASE_URL."""
    root = _root(tmp_path)
    _ = (root / 'invoice.pdf').write_bytes(b'PRIVATE CONTENT')
    monkeypatch.setenv('WATCH_CHAR_SECRET', SECRET)
    pauses = _interrupt_after(monkeypatch, 3)
    with receiver() as (origin, bodies):
        monkeypatch.setenv('MAIVN_BASE_URL', origin)
        code = _command(
            monkeypatch,
            tmp_path,
            root,
            '--emit-existing',
            '--quiet-seconds',
            '0',
            '--scan-interval',
            '0.25',
            '--secret-env',
            'WATCH_CHAR_SECRET',
        )
    assert code is None
    assert pauses == [0.25, 0.25, 0.25, 0.25]
    assert capsys.readouterr() == (
        _status('watching', 0, 0, 0, None)
        + _status('watching', 1, 1, 0, 202)
        + _status('watching', 0, 0, 0, None)
        + STOPPED,
        '',
    )
    assert [json.loads(body)['relative_path'] for body in bodies] == ['invoice.pdf']
    assert all(b'PRIVATE CONTENT' not in body for body in bodies)


def test_command_reports_pending_events_while_the_receiver_refuses(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """A refused delivery leaves the event pending, and the line says so with the status."""
    root = _root(tmp_path)
    _ = (root / 'invoice.pdf').write_bytes(b'x')
    monkeypatch.setattr(sys, 'stdin', io.StringIO(SECRET + '\r\n'))
    _ = _interrupt_after(monkeypatch, 1)
    with receiver(statuses=(503,)) as (origin, bodies):
        code = _command(
            monkeypatch,
            tmp_path,
            root,
            '--receiver-url',
            origin + CALLBACK,
            '--emit-existing',
            '--quiet-seconds',
            '0',
        )
    assert code is None
    assert capsys.readouterr() == (
        _status('watching', 0, 0, 0, None) + _status('pending', 1, 0, 1, 503) + STOPPED,
        '',
    )
    assert len(bodies) == 1


def test_command_scan_limit_is_its_own_status_and_exit_1(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """Exceeding --max-entries ends the command with the scan_limit status on stderr."""
    root = _root(tmp_path)
    for name in ('a.pdf', 'b.pdf'):
        _ = (root / name).write_bytes(b'x')
    monkeypatch.setattr(sys, 'stdin', io.StringIO(SECRET + '\n'))

    assert _command(monkeypatch, tmp_path, root, '--max-entries', '1', '--once') == 1
    assert capsys.readouterr() == ('', SCAN_LIMIT)


@pytest.mark.parametrize(
    ('extra', 'stdin'),
    [
        (('--scan-interval', '0'), SECRET),
        (('--scan-interval', '-1'), SECRET),
        (('--scan-interval', 'nan'), SECRET),
        (('--scan-interval', 'inf'), SECRET),
        ((), '\n'),
        (('--secret-env', 'WATCH_CHAR_UNSET'), SECRET),
        (('--glob', '../escape'), SECRET),
    ],
)
def test_command_refusals_use_one_generic_line_without_echoing_input(
    extra: tuple[str, ...],
    stdin: str,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Bad intervals, missing secrets and bad globs all print the same safe line, exit 1."""
    root = _root(tmp_path)
    monkeypatch.delenv('WATCH_CHAR_UNSET', raising=False)
    monkeypatch.setattr(sys, 'stdin', io.StringIO(stdin))

    assert _command(monkeypatch, tmp_path, root, *extra, '--once') == 1
    assert capsys.readouterr() == ('', GENERIC_FAILURE)


def test_command_argument_errors_are_a_fixed_usage_line(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """The parser's own message (which could repeat a pasted value) is replaced, exit 2."""
    monkeypatch.setattr(sys, 'argv', ['maivn', 'watch-files'])
    with pytest.raises(SystemExit) as stopped:
        cli.main()
    assert stopped.value.code == 2  # noqa: PLR2004 - argparse status
    assert capsys.readouterr() == (
        '',
        'Invalid watch-files arguments; run maivn watch-files --help.\n',
    )


# --- watcher configuration --------------------------------------------------------------


@pytest.mark.parametrize(
    ('options', 'message'),
    [
        ({'secret': ''}, 'a private nonempty signing secret is required'),
        ({'secret': b'bytes'}, 'a private nonempty signing secret is required'),
        ({'pattern': ''}, 'glob must be a bounded relative POSIX pattern'),
        ({'pattern': 7}, 'glob must be a bounded relative POSIX pattern'),
        ({'pattern': '/abs/*'}, 'glob must be a bounded relative POSIX pattern'),
        ({'pattern': 'a/../b'}, 'glob must be a bounded relative POSIX pattern'),
        ({'pattern': 'a\\b'}, 'glob must be a bounded relative POSIX pattern'),
        ({'pattern': 'x' * 1025}, 'glob must be a bounded relative POSIX pattern'),
        ({'quiet_seconds': -0.1}, 'quiet period must be finite and nonnegative'),
        ({'quiet_seconds': math.nan}, 'quiet period must be finite and nonnegative'),
        ({'quiet_seconds': math.inf}, 'quiet period must be finite and nonnegative'),
        ({'quiet_seconds': True}, 'quiet period must be finite and nonnegative'),
        ({'quiet_seconds': '1'}, 'quiet period must be finite and nonnegative'),
        (
            {'completion_suffix': 'ready'},
            'completion suffix must start with a dot and contain no path separators',
        ),
        (
            {'completion_suffix': '.a/b'},
            'completion suffix must start with a dot and contain no path separators',
        ),
        (
            {'completion_suffix': '.a:b'},
            'completion suffix must start with a dot and contain no path separators',
        ),
        (
            {'completion_suffix': '.a\\b'},
            'completion suffix must start with a dot and contain no path separators',
        ),
        (
            {'completion_suffix': '.ready '},
            'completion suffix must start with a dot and contain no path separators',
        ),
        (
            {'completion_suffix': '.' + 'r' * 64},
            'completion suffix must start with a dot and contain no path separators',
        ),
        (
            {'completion_suffix': 7},
            'completion suffix must start with a dot and contain no path separators',
        ),
        ({'max_pending': 0}, 'pending capacity must be between 1 and 10000'),
        ({'max_pending': 10_001}, 'pending capacity must be between 1 and 10000'),
        ({'max_pending': True}, 'pending capacity must be between 1 and 10000'),
        ({'max_entries': 0}, 'scan entry limit must be between 1 and 100000'),
        ({'max_entries': 100_001}, 'scan entry limit must be between 1 and 100000'),
        ({'max_entries': 1.0}, 'scan entry limit must be between 1 and 100000'),
        ({'recursive': 'yes'}, 'recursive and emit_existing must be booleans'),
        ({'emit_existing': 1}, 'recursive and emit_existing must be booleans'),
    ],
)
def test_watcher_configuration_refusals_are_exact(
    options: dict[str, object], message: str, tmp_path: Path
) -> None:
    """Every unusable option is refused at construction with its fixed message."""
    root = _root(tmp_path)
    with pytest.raises(ValueError, match=f'^{message}$'):
        _ = _watcher(tmp_path, root, **options)
    assert not (tmp_path / 'private').exists()


def test_watcher_boundary_options_are_accepted(tmp_path: Path) -> None:
    """The documented inclusive bounds are usable values."""
    root = _root(tmp_path)
    for index, options in enumerate(
        (
            {'max_pending': 1, 'max_entries': 1, 'quiet_seconds': 0},
            {'max_pending': 10_000, 'max_entries': 100_000, 'completion_suffix': '.' + 'r' * 63},
            {'pattern': 'x' * 1024, 'recursive': True, 'emit_existing': True},
        )
    ):
        state = tmp_path / 'private' / f'state-{index}.sqlite3'
        with _watcher(tmp_path, root, state_path=state, **options) as watcher:
            assert watcher.pending_count == 0


def test_watch_root_must_be_an_existing_directory(tmp_path: Path) -> None:
    """A file is not a watch root, and a missing root is a missing path."""
    afile = tmp_path / 'file.txt'
    _ = afile.write_text('x')
    with pytest.raises(ValueError, match=r'^watch root must be a directory$'):
        _ = _watcher(tmp_path, afile)
    with pytest.raises(FileNotFoundError):
        _ = _watcher(tmp_path, tmp_path / 'missing')


def test_state_inside_the_watched_root_is_refused(tmp_path: Path) -> None:
    """The journal can never become one of the files it observes."""
    root = _root(tmp_path)
    with pytest.raises(ValueError, match=r'^watcher state must be outside the watched directory$'):
        _ = _watcher(tmp_path, root, state_path=root / 'state' / 'state.sqlite3')


def test_watcher_must_be_opened_once_before_use(tmp_path: Path) -> None:
    """Use before opening and opening twice are both refused; closing is idempotent."""
    root = _root(tmp_path)
    watcher = _watcher(tmp_path, root)
    with pytest.raises(RuntimeError, match=r'^open the watcher with a context manager first$'):
        _ = watcher.pending_count
    with pytest.raises(RuntimeError, match=r'^open the watcher with a context manager first$'):
        _ = watcher.scan()
    with watcher:
        with pytest.raises(RuntimeError, match=r'^watcher state is already open$'):
            _ = watcher.__enter__()
        assert watcher.pending_count == 0
    watcher.close()
    with pytest.raises(RuntimeError, match=r'^open the watcher with a context manager first$'):
        _ = watcher.deliver_pending()


@pytest.mark.parametrize('batch', [0, 10_001, True, 1.5])
def test_delivery_batch_size_is_bounded(batch: object, tmp_path: Path) -> None:
    """A delivery batch is 1..10000 events, checked before the journal is touched."""
    watcher = _watcher(tmp_path, _root(tmp_path))
    with pytest.raises(ValueError, match=r'^delivery batch size must be between 1 and 10000$'):
        _ = watcher.deliver_pending(max_events=batch)  # pyright: ignore[reportArgumentType]


def test_delivery_stops_at_the_batch_size(tmp_path: Path) -> None:
    """With two pending events, a batch of one settles exactly the oldest."""
    root = _root(tmp_path)
    clock = Clock()
    for name in ('a.pdf', 'b.pdf'):
        _ = (root / name).write_bytes(b'x')
    with (
        receiver() as (origin, bodies),
        _watcher(
            tmp_path,
            root,
            receiver_url=origin + CALLBACK,
            emit_existing=True,
            quiet_seconds=1,
            clock=clock,
        ) as watcher,
    ):
        assert watcher.scan().queued_events == 0
        clock.value = 1
        assert watcher.scan().queued_events == 2  # noqa: PLR2004 - two files
        first = watcher.deliver_pending(max_events=1)
        assert (first.accepted_events, first.pending_events, first.status_code) == (1, 1, 202)
        second = watcher.deliver_pending()
        assert (second.accepted_events, second.pending_events, second.status_code) == (1, 0, 202)
        empty = watcher.deliver_pending()
        assert (empty.accepted_events, empty.pending_events, empty.status_code) == (0, 0, None)
    assert [json.loads(body)['relative_path'] for body in bodies] == ['a.pdf', 'b.pdf']


def test_quiet_period_is_inclusive(tmp_path: Path) -> None:
    """An event is queued once exactly `quiet_seconds` have passed since first sight."""
    root = _root(tmp_path)
    clock = Clock()
    _ = (root / 'a.pdf').write_bytes(b'x')
    with _watcher(tmp_path, root, emit_existing=True, quiet_seconds=2, clock=clock) as watcher:
        assert watcher.scan().queued_events == 0
        clock.value = 1.999
        assert watcher.scan().queued_events == 0
        clock.value = 2
        result = watcher.scan()
        assert (result.queued_events, result.pending_events, result.queue_full) == (1, 1, False)


@pytest.mark.parametrize('interval', [0, -1, math.nan, math.inf, True])
def test_run_loop_interval_is_finite_and_positive(interval: float, tmp_path: Path) -> None:
    """The polling interval is validated before the first scan."""
    with (
        _watcher(tmp_path, _root(tmp_path)) as watcher,
        pytest.raises(ValueError, match=r'^scan interval must be finite and positive$'),
    ):
        watcher.run(stop_event=threading.Event(), scan_interval=interval)


def test_run_loop_without_a_progress_callback_still_scans_until_stopped(tmp_path: Path) -> None:
    """Without a callback the loop keeps scanning and returns once the stop event is set."""
    root = _root(tmp_path)
    _ = (root / 'a.pdf').write_bytes(b'x')
    stop = threading.Event()
    timer = threading.Timer(0.3, stop.set)
    with _watcher(tmp_path, root, emit_existing=True, quiet_seconds=0) as watcher:
        timer.start()
        try:
            watcher.run(stop_event=stop, scan_interval=0.02)
        finally:
            timer.cancel()
        assert watcher.pending_count == 1


def test_replaced_root_directory_stops_the_scan(tmp_path: Path) -> None:
    """A root swapped for another directory at the same path is detected, not followed."""
    root = _root(tmp_path)
    with _watcher(tmp_path, root) as watcher:
        _ = watcher.scan()
        root.rename(tmp_path / 'moved')
        root.mkdir()
        with pytest.raises(
            RuntimeError,
            match=r'^watch root changed identity; restart with an explicitly selected root$',
        ):
            _ = watcher.scan()


def test_scan_limit_counts_every_entry_examined(tmp_path: Path) -> None:
    """Unmatched files count toward the budget; exceeding it raises, at the limit it does not."""
    root = _root(tmp_path)
    for name in ('a.pdf', 'b.txt'):
        _ = (root / name).write_bytes(b'x')
    with _watcher(tmp_path, root, pattern='*.pdf', max_entries=2) as watcher:
        assert watcher.scan().queued_events == 0
    with (
        _watcher(tmp_path, root, pattern='*.pdf', max_entries=1) as watcher,
        pytest.raises(
            FileScanLimitError,
            match=r'^scan entry limit reached; choose a smaller root or raise max_entries$',
        ),
    ):
        _ = watcher.scan()


@pytest.mark.parametrize('mtime_ns', [-1_000_000_000_000_000_000, 200_000_000_000_000_000_000])
def test_files_with_out_of_range_timestamps_are_ignored(mtime_ns: int, tmp_path: Path) -> None:
    """A modification time before 1970 or past the event bound never becomes an event."""
    root = _root(tmp_path)
    clock = Clock()
    odd = root / 'odd.pdf'
    _ = odd.write_bytes(b'x')
    os.utime(odd, ns=(0, mtime_ns))
    if odd.stat().st_mtime_ns != mtime_ns:
        pytest.skip('this filesystem clamps the timestamp into range (ext4 stops at 2446)')
    _ = (root / 'normal.pdf').write_bytes(b'x')
    with _watcher(tmp_path, root, emit_existing=True, quiet_seconds=0, clock=clock) as watcher:
        _ = watcher.scan()
        assert watcher.scan().queued_events == 1
        assert watcher.pending_count == 1


# --- journal file safety ----------------------------------------------------------------


def test_journal_refuses_a_hard_linked_state_file(tmp_path: Path) -> None:
    """A state file with a second name could be read elsewhere, so it is refused."""
    root = _root(tmp_path)
    state = tmp_path / 'private' / 'state.sqlite3'
    with _watcher(tmp_path, root):
        pass
    os.link(state, tmp_path / 'private' / 'copy.sqlite3')
    with (
        pytest.raises(
            ValueError, match=r'^watcher state must be a private regular file without links$'
        ),
        _watcher(tmp_path, root),
    ):
        pytest.fail('a linked journal cannot be opened')


def test_journal_lock_is_released_after_a_refused_open(tmp_path: Path) -> None:
    """A refused configuration releases the writer lock, so the right one opens afterwards."""
    root = _root(tmp_path)
    with _watcher(tmp_path, root):
        pass
    with (
        pytest.raises(
            ValueError,
            match=r'^watcher state belongs to a different root, receiver or watch configuration$',
        ),
        _watcher(tmp_path, root, pattern='*.pdf'),
    ):
        pytest.fail('a foreign configuration cannot be opened')
    with _watcher(tmp_path, root) as watcher:
        assert watcher.pending_count == 0


# --- generated files ----------------------------------------------------------------------


def test_oversized_editable_source_archive_is_refused_and_capture_removed(tmp_path: Path) -> None:
    """Source whose archive would pass 50 MiB is refused and the fresh capture is removed."""
    output = tmp_path / 'report.pdf'
    _ = output.write_bytes(b'%PDF-1.7 small')
    source = b's' * 40_000_000

    with pytest.raises(ValueError, match=r'^Editable source archive exceeds the custody limit$'):
        _ = generated_file(output, source=source, source_format='custom/v1')
    assert list((tmp_path / '.maivn-generated').iterdir()) == []
    assert output.read_bytes() == b'%PDF-1.7 small'
