"""Actual filesystem exclusions, SQLite atomic failures and safe CLI contracts."""

from __future__ import annotations

import getpass
import io
import json
import os
import sqlite3
import subprocess
import sys
import warnings
from pathlib import Path
from threading import Event
from typing import TYPE_CHECKING, cast

import pytest

from maivn import Agent, cli
from maivn._internal.local_file_cli import run_watch_files
from maivn.connection_events import (
    FileDeliveryResult,
    FileScanLimitError,
    FileScanResult,
    LocalFileWatcher,
)
from tests.unit.test_local_file_watch import CALLBACK, SECRET, Clock, WatchOptions, receiver

if TYPE_CHECKING:
    from typing import Literal

    from _pytest.capture import CaptureFixture

_SYMLINK_PRIVILEGE_MISSING = 1314


def options(tmp_path: Path, url: str, clock: Clock) -> WatchOptions:
    """Explicit isolated state and receiver for real filesystem fixtures."""
    return {
        'watch_id': 'invoices',
        'connection_id': 'conn_files',
        'receiver_url': url,
        'secret': SECRET,
        'state_path': tmp_path / 'private' / 'state.sqlite3',
        'clock': clock,
        'max_pending': 10000,
    }


def test_failed_journal_transaction_never_advances_cursor(tmp_path: Path) -> None:
    """A real SQLite abort between event/cursor writes rolls back the entire observation."""
    root = tmp_path / 'incoming'
    root.mkdir()
    target = root / 'invoice.pdf'
    target.write_text('old')
    clock = Clock()
    with (
        receiver() as (url, bodies),
        LocalFileWatcher(root, **options(tmp_path, url, clock)) as watcher,
    ):
        watcher.scan()
        target.write_text('newer metadata')
        watcher.scan()
        clock.value = 2
        state = sqlite3.connect(tmp_path / 'private' / 'state.sqlite3')
        try:
            state.executescript(
                'CREATE TRIGGER refuse_pending BEFORE INSERT ON pending '
                "BEGIN SELECT RAISE(ABORT, 'fixture'); END;"
            )
            with pytest.raises(sqlite3.IntegrityError):
                watcher.scan()
            assert watcher.pending_count == 0
            assert state.execute(
                'SELECT size FROM files WHERE path=?', ('invoice.pdf',)
            ).fetchone()[0] == len('old')
            state.execute('DROP TRIGGER refuse_pending')
            state.commit()
            assert watcher.scan().queued_events == 1
            watcher.deliver_pending()
            assert len(bodies) == 1
        finally:
            state.close()


def test_abrupt_process_exit_preserves_journal_and_releases_writer_lock(tmp_path: Path) -> None:
    """A real process dies after journal commit but before HTTP and replays exactly once."""
    root = tmp_path / 'incoming'
    root.mkdir()
    (root / 'invoice.pdf').write_text('SECRET-CONTENT-SENTINEL')
    clock = Clock()
    child = (
        'import os,sys\nfrom maivn.connection_events import LocalFileWatcher\n'
        'with LocalFileWatcher(sys.argv[1], watch_id="invoices", connection_id="conn_files", '
        'receiver_url=sys.argv[3], secret=sys.stdin.readline().rstrip("\\n"), '
        'state_path=sys.argv[2], quiet_seconds=0, emit_existing=True) as watcher:\n'
        ' watcher.scan()\n watcher.scan()\n os._exit(0)\n'
    )
    with receiver() as (url, bodies):
        state = tmp_path / 'private' / 'state.sqlite3'
        result = subprocess.run(  # noqa: S603 - isolated fixture process; secret only in stdin.
            [sys.executable, '-c', child, str(root), str(state), url],
            input=SECRET + '\n',
            text=True,
            capture_output=True,
            check=False,
        )
        assert result.returncode == 0, result.stderr
        assert not bodies
        with LocalFileWatcher(root, **options(tmp_path, url, clock)) as watcher:
            assert watcher.pending_count == 1
            locked = subprocess.run(  # noqa: S603 - private fixture process-lock proof.
                [
                    sys.executable,
                    '-c',
                    'from pathlib import Path; import sys; '
                    'from maivn._internal.local_file_state import FileJournal; '
                    'FileJournal(Path(sys.argv[1]), configuration="fixture")',
                    str(state),
                ],
                capture_output=True,
                text=True,
                check=False,
            )
            assert locked.returncode != 0
            assert 'already using' in locked.stderr
            assert watcher.deliver_pending().accepted_events == 1
            assert watcher.scan().queued_events == 0
        assert len(bodies) == 1
        assert b'SECRET-CONTENT-SENTINEL' not in bodies[0]


def test_only_canonical_202_settles_pending(tmp_path: Path) -> None:
    """A successful-looking 201 leaves the exact same event queued for a later 202."""
    root = tmp_path / 'incoming'
    root.mkdir()
    clock = Clock()
    with (
        receiver(statuses=(201, 202)) as (url, bodies),
        LocalFileWatcher(root, **options(tmp_path, url, clock)) as watcher,
    ):
        watcher.scan()
        (root / 'file.pdf').write_text('secret')
        watcher.scan()
        clock.value = 2
        watcher.scan()
        assert watcher.deliver_pending().accepted_events == 0
        assert watcher.pending_count == 1
        assert watcher.deliver_pending().accepted_events == 1
        assert bodies[0] == bodies[1]


def test_final_change_during_downtime_is_observed_once(tmp_path: Path) -> None:
    """A durable baseline detects the final offline write, without inventing intermediate events."""
    root = tmp_path / 'incoming'
    root.mkdir()
    target = root / 'invoice.pdf'
    target.write_text('initial')
    clock = Clock()
    with receiver() as (url, bodies):
        with LocalFileWatcher(root, **options(tmp_path, url, clock)) as watcher:
            watcher.scan()
        target.write_text('intermediate')
        target.write_text('final offline contents')
        with LocalFileWatcher(root, **options(tmp_path, url, clock)) as watcher:
            assert watcher.scan().queued_events == 0
            clock.value = 2
            assert watcher.scan().queued_events == 1
            assert watcher.deliver_pending().accepted_events == 1
            assert watcher.scan().queued_events == 0
        assert len(bodies) == 1
        event = json.loads(bodies[0])
        assert event['operation'] == 'modified'
        assert event['size_bytes'] == len('final offline contents')


@pytest.mark.parametrize('capacity', [0, 10001, True])
def test_pending_bound_refuses_before_creating_state(tmp_path: Path, capacity: int) -> None:
    """The hard pending bound cannot be disabled by an invalid constructor value."""
    setup = options(tmp_path, 'http://127.0.0.1:8200' + CALLBACK, Clock())
    setup['max_pending'] = capacity
    with pytest.raises(ValueError, match='capacity'):
        LocalFileWatcher(tmp_path, **setup)
    assert not (tmp_path / 'private').exists()


def test_scan_bound_is_explicit_and_never_commits_partial_observation(tmp_path: Path) -> None:
    """Even unmatched entries count toward the traversal bound, before cursor changes."""
    root = tmp_path / 'incoming'
    root.mkdir()
    (root / 'prior.pdf').write_text('baseline')
    clock = Clock()
    with (
        receiver() as (url, bodies),
        LocalFileWatcher(
            root, **options(tmp_path, url, clock), max_entries=2, pattern='*.pdf'
        ) as watcher,
    ):
        watcher.scan()
        (root / 'ignored.txt').write_text('private')
        (root / 'new.pdf').write_text('new')
        with pytest.raises(FileScanLimitError):
            watcher.scan()
        assert watcher.pending_count == 0
        (root / 'ignored.txt').unlink()
        watcher.scan()
        clock.value = 2
        assert watcher.scan().queued_events == 1
        watcher.deliver_pending()
        assert [json.loads(body)['relative_path'] for body in bodies] == ['new.pdf']


def test_explicit_run_loop_obeys_stop_event(tmp_path: Path) -> None:
    """The caller owns the loop lifetime; stopping leaves no background observer."""
    root = tmp_path / 'incoming'
    root.mkdir()
    stop = Event()
    observed: list[FileScanResult] = []

    def progress(scan: FileScanResult, delivery: FileDeliveryResult) -> None:
        observed.append(scan)
        assert delivery.pending_events == 0
        stop.set()

    with (
        receiver() as (url, _bodies),
        LocalFileWatcher(root, **options(tmp_path, url, Clock())) as watcher,
    ):
        watcher.run(stop_event=stop, on_progress=progress)
    assert len(observed) == 1


def test_windows_private_state_permissions_are_observed_by_os(tmp_path: Path) -> None:
    """Read the real DACL independently with the OS security API exposed by PowerShell."""
    if os.name != 'nt':
        pytest.skip('Windows DACL observation')
    root = tmp_path / 'incoming'
    root.mkdir()
    with (
        receiver() as (url, _bodies),
        LocalFileWatcher(root, **options(tmp_path, url, Clock())) as watcher,
    ):
        watcher.scan()
        parent = str(tmp_path / 'private').replace("'", "''")
        script = (
            "$ErrorActionPreference='Stop'\n"
            '$sid=[System.Security.Principal.WindowsIdentity]::GetCurrent().User.Value\n'
            f"$folder=[System.IO.Directory]::GetAccessControl('{parent}')\n"
            '$owner=([System.Security.Principal.NTAccount]::new($folder.Owner)).Translate([System.Security.Principal.SecurityIdentifier]).Value\n'
            '$rules=@($folder.Access)\n'
            '$allowed=$rules[0].IdentityReference.Translate([System.Security.Principal.SecurityIdentifier]).Value\n'
            '[bool]($folder.AreAccessRulesProtected -and $rules.Count -eq 1 '
            '-and $owner -eq $sid -and $allowed -eq $sid)\n'
        )
        executable = (
            Path(os.environ['SYSTEMROOT'])
            / 'System32'
            / 'WindowsPowerShell'
            / 'v1.0'
            / 'powershell.exe'
        )
        result = subprocess.run(  # noqa: S603 - OS ACL read, escaped fixture path via stdin.
            [str(executable), '-NoProfile', '-NonInteractive', '-Command', '-'],
            input=script,
            text=True,
            capture_output=True,
            check=False,
        )
        assert result.returncode == 0
        assert result.stdout.strip() == 'True', result.stderr


@pytest.mark.parametrize('recursive', [False, True])
def test_real_files_glob_temp_and_directories(tmp_path: Path, *, recursive: bool) -> None:
    """Only the chosen final-name metadata appears, with recursion explicitly opted in."""
    root = tmp_path / 'incoming'
    root.mkdir()
    clock = Clock()
    with (
        receiver() as (url, bodies),
        LocalFileWatcher(
            root, **options(tmp_path, url, clock), pattern='*.pdf', recursive=recursive
        ) as watcher,
    ):
        watcher.scan()
        (root / 'sub').mkdir()
        (root / 'sub' / 'nested.pdf').write_text('NESTED SECRET')
        (root / 'directory.pdf').mkdir()
        (root / 'excluded.txt').write_text('EXCLUDED SECRET')
        (root / 'final.pdf').write_text('PRIVATE FILE')
        (root / 'temp.pdf.tmp').write_text('PARTIAL FILE')
        watcher.scan()
        clock.value = 2
        watcher.scan()
        watcher.deliver_pending()
        expected = {'final.pdf', 'sub/nested.pdf'} if recursive else {'final.pdf'}
        assert {json.loads(body)['relative_path'] for body in bodies} == expected
        assert all(b'SECRET' not in body and b'PRIVATE FILE' not in body for body in bodies)


def test_windows_junction_escape_excluded(tmp_path: Path) -> None:
    """A real Windows junction never imports metadata from outside the selected root."""
    if os.name != 'nt':
        pytest.skip('Windows junction proof')
    root = tmp_path / 'incoming'
    root.mkdir()
    outside = tmp_path / 'outside'
    outside.mkdir()
    (outside / 'escaped.pdf').write_text('OUTSIDE SECRET')
    executable = Path(os.environ['SYSTEMROOT']) / 'System32' / 'cmd.exe'
    result = subprocess.run(  # noqa: S603 - OS junction creation on isolated fixture paths.
        [str(executable), '/c', 'mklink', '/J', str(root / 'junction'), str(outside)],
        capture_output=True,
        check=False,
    )
    assert result.returncode == 0
    clock = Clock()
    with (
        receiver() as (url, bodies),
        LocalFileWatcher(
            root, **options(tmp_path, url, clock), recursive=True, emit_existing=True
        ) as watcher,
    ):
        watcher.scan()
        clock.value = 2
        assert watcher.scan().queued_events == 0
        watcher.deliver_pending()
        assert not bodies


def test_symlink_file_excluded(tmp_path: Path) -> None:
    """A regular-looking symlink is excluded even when its target exists."""
    root = tmp_path / 'incoming'
    root.mkdir()
    target = tmp_path / 'outside.pdf'
    target.write_text('OUTSIDE SECRET')
    try:
        (root / 'link.pdf').symlink_to(target)
    except OSError as error:
        if getattr(error, 'winerror', None) == _SYMLINK_PRIVILEGE_MISSING:
            pytest.skip(
                'Windows process lacks symlink creation privilege; junction tested separately'
            )
        raise
    clock = Clock()
    with (
        receiver() as (url, bodies),
        LocalFileWatcher(root, **options(tmp_path, url, clock), emit_existing=True) as watcher,
    ):
        watcher.scan()
        clock.value = 2
        assert watcher.scan().queued_events == 0
        watcher.deliver_pending()
        assert not bodies


def test_disappearance_during_stat_has_no_event(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A file removed while the scanner examines it cannot create phantom metadata."""
    root = tmp_path / 'incoming'
    root.mkdir()
    target = root / 'vanishing.pdf'
    clock = Clock()
    with (
        receiver() as (url, bodies),
        LocalFileWatcher(root, **options(tmp_path, url, clock)) as watcher,
    ):
        watcher.scan()
        target.write_text('transient')
        original = Path.lstat

        def remove_on_stat(path: Path, *args: object, **kwargs: object) -> os.stat_result:
            if path == target:
                target.unlink(missing_ok=True)
                raise FileNotFoundError
            del args, kwargs
            return original(path)

        monkeypatch.setattr(Path, 'lstat', remove_on_stat)
        watcher.scan()
        clock.value = 2
        assert watcher.scan().queued_events == 0
        watcher.deliver_pending()
        assert not bodies


@pytest.mark.parametrize('denied_kind', ['file', 'directory'])
def test_temporary_permission_error_preserves_observed_cursor(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, denied_kind: str
) -> None:
    """An incomplete scan cannot forget known metadata and emit a false creation later."""
    root = tmp_path / 'incoming'
    nested = root / 'nested'
    nested.mkdir(parents=True)
    target = nested / 'invoice.pdf'
    target.write_text('unchanged')
    denied = target if denied_kind == 'file' else nested
    clock = Clock()
    with (
        receiver() as (url, bodies),
        LocalFileWatcher(root, **options(tmp_path, url, clock), recursive=True) as watcher,
    ):
        watcher.scan()
        original = Path.lstat

        def refuse(path: Path, *args: object, **kwargs: object) -> os.stat_result:
            del args, kwargs
            if path == denied:
                message = 'fixture temporarily unreadable'
                raise PermissionError(message)
            return original(path)

        with monkeypatch.context() as patch:
            patch.setattr(Path, 'lstat', refuse)
            with pytest.raises(PermissionError):
                watcher.scan()
        watcher.scan()
        clock.value = 2
        assert watcher.scan().queued_events == 0
        watcher.deliver_pending()
        assert bodies == []


def test_windows_completion_marker_case_never_becomes_an_event(tmp_path: Path) -> None:
    """Windows suffix comparisons exclude marker names regardless of filename casing."""
    if os.name != 'nt':
        pytest.skip('Windows filename casing')
    root = tmp_path / 'incoming'
    root.mkdir()
    clock = Clock()
    with (
        receiver() as (url, bodies),
        LocalFileWatcher(
            root, **options(tmp_path, url, clock), completion_suffix='.ready'
        ) as watcher,
    ):
        watcher.scan()
        (root / 'invoice.pdf').write_text('private')
        (root / 'invoice.pdf.READY').write_text('')
        watcher.scan()
        clock.value = 2
        assert watcher.scan().queued_events == 1
        watcher.deliver_pending()
        assert [json.loads(body)['relative_path'] for body in bodies] == ['invoice.pdf']


def test_emit_existing_opt_in_and_wrong_state_identity(tmp_path: Path) -> None:
    """Existing-file opt-in runs once; an old journal cannot change receiver/watch identity."""
    root = tmp_path / 'incoming'
    root.mkdir()
    (root / 'existing.pdf').write_text('private')
    clock = Clock()
    with receiver() as (url, bodies):
        setup = options(tmp_path, url, clock)
        with LocalFileWatcher(root, **setup, emit_existing=True) as watcher:
            watcher.scan()
            clock.value = 2
            assert watcher.scan().queued_events == 1
            watcher.deliver_pending()
        with LocalFileWatcher(root, **setup, emit_existing=True) as watcher:
            assert watcher.scan().queued_events == 0
        setup['watch_id'] = 'different'
        with pytest.raises(ValueError, match='different'), LocalFileWatcher(root, **setup):
            pass
        assert len(bodies) == 1


def test_existing_shared_state_directory_is_refused(tmp_path: Path) -> None:
    """Do not silently tighten permissions on an arbitrary existing caller directory."""
    root = tmp_path / 'incoming'
    root.mkdir()
    shared = tmp_path / 'shared'
    shared.mkdir(mode=0o755)
    with (
        pytest.raises(ValueError, match=r'owner|0700'),
        LocalFileWatcher(
            root,
            watch_id='files',
            connection_id='conn_files',
            receiver_url='http://127.0.0.1:8200' + CALLBACK,
            secret=SECRET,
            state_path=shared / 'state.sqlite3',
        ),
    ):
        pass
    assert not (shared / 'state.sqlite3').exists()


def test_selected_root_cannot_hide_a_junction(tmp_path: Path) -> None:
    """Reject an explicitly chosen linked root before resolving away its provenance."""
    if os.name != 'nt':
        pytest.skip('Windows junction proof')
    target = tmp_path / 'outside'
    target.mkdir()
    linked = tmp_path / 'linked'
    executable = Path(os.environ['SYSTEMROOT']) / 'System32' / 'cmd.exe'
    result = subprocess.run(  # noqa: S603 - isolated real junction fixture.
        [str(executable), '/c', 'mklink', '/J', str(linked), str(target)],
        capture_output=True,
        check=False,
    )
    assert result.returncode == 0
    with (
        pytest.raises(ValueError, match=r'link|junction'),
        LocalFileWatcher(linked, **options(tmp_path, 'http://127.0.0.1:8200' + CALLBACK, Clock())),
    ):
        pass


@pytest.mark.parametrize('failure', ['warning', 'eof'])
def test_private_prompt_refuses_echo_fallback(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: CaptureFixture[str], failure: str
) -> None:
    """Unsupported terminal echo and EOF fail before any private input is consumed."""

    class Terminal(io.StringIO):
        def isatty(self) -> bool:
            return True

    def refused_prompt(prompt: str) -> str:
        del prompt
        if failure == 'eof':
            raise EOFError
        warnings.warn('fixture terminal cannot hide input', getpass.GetPassWarning, stacklevel=1)
        pytest.fail('must fail before reading echoed fallback input')

    monkeypatch.setattr(sys, 'stdin', Terminal())
    monkeypatch.setattr(getpass, 'getpass', refused_prompt)
    with pytest.raises(SystemExit) as error:
        run_watch_files([str(tmp_path), '--watch-id', 'files', '--connection-id', 'conn_files'])
    assert error.value.code == 1
    captured = capsys.readouterr()
    assert 'could not continue' in captured.err
    assert 'fixture terminal' not in captured.err


def test_cli_one_scan_stdin_secret_and_safe_argument_refusal(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: CaptureFixture[str]
) -> None:
    """The actual CLI accepts a private stdin line and never echoes root or secret."""
    root = tmp_path / 'incoming'
    root.mkdir()
    monkeypatch.setattr(sys, 'stdin', io.StringIO(SECRET + '\n'))
    monkeypatch.setattr(
        sys,
        'argv',
        [
            'maivn',
            'watch-files',
            str(root),
            '--watch-id',
            'files',
            '--connection-id',
            'conn_files',
            '--state-file',
            str(tmp_path / 'private' / 'state.sqlite3'),
            '--once',
        ],
    )
    cli.main()
    captured = capsys.readouterr()
    assert json.loads(captured.out)['status'] == 'watching'
    assert SECRET not in captured.out + captured.err
    assert str(root) not in captured.out + captured.err
    monkeypatch.setattr(sys, 'argv', ['maivn', 'watch-files', '--secret', SECRET])
    with pytest.raises(SystemExit):
        cli.main()
    captured = capsys.readouterr()
    assert SECRET not in captured.out + captured.err


@pytest.mark.parametrize('operation', [True, False, 'deleted', '', 1, None])
def test_file_operations_are_finite_at_runtime(operation: object) -> None:
    """Unknown operations cannot turn into a generic unchecked event name."""
    with pytest.raises(ValueError, match='operation'):
        Agent('files', api_key='fixture').on_file_change(
            'files',
            connection_id='conn_files',
            operation=cast("Literal['created', 'modified']", operation),
        )
