"""Explicit local watcher command with private input and safe progress output."""

from __future__ import annotations

import argparse
import getpass
import json
import math
import os
import sqlite3
import sys
import time
import warnings
from typing import TYPE_CHECKING, NoReturn

from maivn._internal.config import LOCAL_BASE_URL, local_tool_base_url
from maivn._internal.local_file_scan import MAX_SCAN_ENTRIES, FileScanLimitError
from maivn._internal.local_file_watch import LocalFileWatcher

if TYPE_CHECKING:
    from collections.abc import Sequence


class _Parser(argparse.ArgumentParser):
    def error(self, message: str) -> NoReturn:
        del message
        self.exit(2, 'Invalid watch-files arguments; run maivn watch-files --help.\n')


def run_watch_files(args: Sequence[str]) -> None:
    """Run until Ctrl-C, retaining pending identities through every uncertain response."""
    parser = _parser()
    options = parser.parse_args(args)
    try:
        interval = _interval(options.scan_interval)
        secret = _secret(options.secret_env)
        receiver = (
            options.receiver_url
            or f'{local_tool_base_url()}/v1/connections/{options.connection_id}/events'
        )
        with LocalFileWatcher(
            options.root,
            watch_id=options.watch_id,
            connection_id=options.connection_id,
            receiver_url=receiver,
            secret=secret,
            state_path=options.state_file,
            pattern=options.glob,
            recursive=options.recursive,
            quiet_seconds=options.quiet_seconds,
            completion_suffix=options.completion_suffix,
            emit_existing=options.emit_existing,
            max_entries=options.max_entries,
        ) as watcher:
            last: str | None = None
            while True:
                observed = watcher.scan()
                delivery = watcher.deliver_pending()
                status = json.dumps(
                    {
                        'status': 'queue_full'
                        if observed.queue_full
                        else 'pending'
                        if delivery.pending_events
                        else 'watching',
                        'queued_events': observed.queued_events,
                        'accepted_events': delivery.accepted_events,
                        'pending_events': delivery.pending_events,
                        'http_status': delivery.status_code,
                    },
                    sort_keys=True,
                )
                if status != last:
                    sys.stdout.write(status + '\n')
                    sys.stdout.flush()
                    last = status
                if options.once:
                    return
                time.sleep(interval)
    except KeyboardInterrupt:
        sys.stdout.write('{"status":"stopped","pending_preserved":true}\n')
    except FileScanLimitError:
        sys.stderr.write('{"status":"scan_limit","pending_preserved":true}\n')
        raise SystemExit(1) from None
    except (OSError, ValueError, RuntimeError, sqlite3.Error, getpass.GetPassWarning, EOFError):
        # Filesystem errors contain absolute paths; validation errors may contain input.
        # Neither belongs in ordinary producer logs. Pending SQLite state is retained.
        sys.stderr.write(
            'File watcher could not continue. Check the selected root, private state directory, '
            'configuration and signing secret; pending events are retained.\n'
        )
        raise SystemExit(1) from None


def _interval(value: float) -> float:
    if not math.isfinite(value) or value <= 0:
        message = 'scan interval must be finite and positive'
        raise ValueError(message)
    return value


def _secret(environment_name: str | None) -> str:
    if environment_name is not None:
        value = os.environ.get(environment_name, '')
    elif sys.stdin.isatty():
        with warnings.catch_warnings():
            warnings.simplefilter('error', getpass.GetPassWarning)
            value = getpass.getpass('Webhook signing secret (private): ')
    else:
        value = sys.stdin.readline().rstrip('\r\n')
    if not value:
        message = 'a private signing secret is required'
        raise ValueError(message)
    return value


def _parser() -> argparse.ArgumentParser:
    parser = _Parser(
        prog='maivn watch-files',
        description=(
            'Watch local regular-file metadata; never upload contents or start a tunnel. '
            'Existing files form a baseline by default. A quiet period cannot prove file close: '
            'write to a temporary name, rename to the final name, or use a completion marker. '
            'Paste the signing secret at the private prompt or supply one stdin line. '
            'Only final changes after downtime are observed, not every intermediate write.'
        ),
    )
    _ = parser.add_argument('root', help='Local folder to observe; not sent in events.')
    _ = parser.add_argument('--watch-id', required=True)
    _ = parser.add_argument('--connection-id', required=True)
    _ = parser.add_argument(
        '--receiver-url',
        help=(
            'Exact connection callback; defaults to the connection events path on '
            f'MAIVN_BASE_URL, or on {LOCAL_BASE_URL} when unset.'
        ),
    )
    _ = parser.add_argument(
        '--glob', default='*', help='Relative POSIX glob; common temporary suffixes are excluded.'
    )
    _ = parser.add_argument(
        '--recursive',
        action='store_true',
        help='Visit regular subdirectories; never follow symlinks or junctions.',
    )
    _ = parser.add_argument('--quiet-seconds', type=float, default=1.0)
    _ = parser.add_argument('--scan-interval', type=float, default=0.5)
    _ = parser.add_argument(
        '--max-entries',
        type=int,
        default=MAX_SCAN_ENTRIES,
        help='Maximum entries examined per complete scan (1..100000), including unmatched files.',
    )
    _ = parser.add_argument(
        '--completion-suffix',
        help='For example .ready: finish/rename file, then replace file.ready for each revision.',
    )
    _ = parser.add_argument(
        '--emit-existing',
        action='store_true',
        help='Queue existing files on the first scan of new state, after the quiet period.',
    )
    _ = parser.add_argument(
        '--state-file',
        help='SQLite file outside the watch root, in a new or verified owner-private directory.',
    )
    _ = parser.add_argument(
        '--secret-env',
        help='Explicit name of an existing private environment variable; never a secret value.',
    )
    _ = parser.add_argument(
        '--once', action='store_true', help='Scan and attempt pending deliveries once, then exit.'
    )
    return parser
