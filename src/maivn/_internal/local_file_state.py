"""Atomic immutable event journal and observed-file cursor for one local watcher."""

from __future__ import annotations

import os
import sqlite3
import stat
from typing import TYPE_CHECKING, cast

from maivn._internal.local_file_scan import FileIdentity
from maivn._internal.local_file_security import StateLock, is_link, private_directory

if TYPE_CHECKING:
    from collections.abc import Mapping
    from pathlib import Path


class FileJournal:
    """One private SQLite file, guarded across processes for its entire lifetime."""

    def __init__(self, path: Path, *, configuration: str) -> None:
        """Open or initialize a journal whose identity cannot silently change."""
        private_directory(path.parent)
        self._lock = StateLock(path.with_name(path.name + '.lock'))
        try:
            _prepare_file(path)
            self._db = sqlite3.connect(path, timeout=0, isolation_level=None)
            self._db.execute('PRAGMA journal_mode=DELETE')
            self._db.execute('PRAGMA synchronous=FULL')
            self._db.executescript(
                'CREATE TABLE IF NOT EXISTS configuration (value TEXT NOT NULL);'
                'CREATE TABLE IF NOT EXISTS baseline (ready INTEGER NOT NULL);'
                'CREATE TABLE IF NOT EXISTS files ('
                'path TEXT PRIMARY KEY, size INTEGER NOT NULL, '
                'mtime TEXT NOT NULL, marker TEXT NOT NULL);'
                'CREATE TABLE IF NOT EXISTS pending ('
                'sequence INTEGER PRIMARY KEY AUTOINCREMENT, '
                'event_id TEXT UNIQUE NOT NULL, body BLOB NOT NULL);'
            )
            self._check_configuration(configuration)
        except BaseException:
            if hasattr(self, '_db'):
                self._db.close()
            self._lock.close()
            raise

    def _check_configuration(self, configuration: str) -> None:
        prior = self._db.execute('SELECT value FROM configuration').fetchone()
        if prior is None:
            self._db.execute('INSERT INTO configuration VALUES (?)', (configuration,))
        elif prior[0] != configuration:
            message = 'watcher state belongs to a different root, receiver or watch configuration'
            raise ValueError(message)

    @property
    def pending_count(self) -> int:
        """Count durable unsatisfied deliveries."""
        return int(self._db.execute('SELECT COUNT(*) FROM pending').fetchone()[0])

    @property
    def initialized(self) -> bool:
        """Whether the first complete baseline transaction has committed."""
        return self._db.execute('SELECT 1 FROM baseline LIMIT 1').fetchone() is not None

    def files(self) -> dict[str, FileIdentity]:
        """Read committed observations; pending quiet candidates never advance this cursor."""
        return {
            cast('str', row[0]): FileIdentity(int(row[1]), int(row[2]), cast('str', row[3]))
            for row in self._db.execute('SELECT path,size,mtime,marker FROM files')
        }

    def baseline(self, files: Mapping[str, FileIdentity]) -> None:
        """Persist initial observations and the initialized flag in one transaction."""
        self._db.execute('BEGIN IMMEDIATE')
        try:
            for path, identity in files.items():
                self._cursor(path, identity)
            self._db.execute('INSERT INTO baseline VALUES (1)')
            self._db.commit()
        except BaseException:
            self._db.rollback()
            raise

    def forget(self, paths: set[str]) -> None:
        """Forget confirmed disappearance without emitting a deletion event."""
        self._db.execute('BEGIN IMMEDIATE')
        try:
            self._db.executemany('DELETE FROM files WHERE path=?', ((path,) for path in paths))
            self._db.commit()
        except BaseException:
            self._db.rollback()
            raise

    def append(self, path: str, identity: FileIdentity, *, event_id: str, body: bytes) -> None:
        """Commit UUID, immutable bytes and cursor atomically; roll back every failed write."""
        self._db.execute('BEGIN IMMEDIATE')
        try:
            self._db.execute('INSERT INTO pending(event_id,body) VALUES (?,?)', (event_id, body))
            self._cursor(path, identity)
            self._db.commit()
        except BaseException:
            self._db.rollback()
            raise

    def first(self) -> tuple[str, bytes] | None:
        """Return the oldest immutable pending body."""
        row = self._db.execute(
            'SELECT event_id,body FROM pending ORDER BY sequence LIMIT 1'
        ).fetchone()
        return (cast('str', row[0]), cast('bytes', row[1])) if row is not None else None

    def settle(self, event_id: str) -> None:
        """Delete pending state only after an authoritative accepted response."""
        self._db.execute('DELETE FROM pending WHERE event_id=?', (event_id,))

    def _cursor(self, path: str, identity: FileIdentity) -> None:
        self._db.execute(
            'INSERT INTO files(path,size,mtime,marker) VALUES (?,?,?,?) '
            'ON CONFLICT(path) DO UPDATE SET '
            'size=excluded.size,mtime=excluded.mtime,marker=excluded.marker',
            (path, identity.size, str(identity.mtime_ns), identity.marker),
        )

    def close(self) -> None:
        """Close SQLite before releasing the writer lock."""
        self._db.close()
        self._lock.close()


def _prepare_file(path: Path) -> None:
    try:
        descriptor = os.open(path, os.O_CREAT | os.O_EXCL | os.O_RDWR, 0o600)
        os.close(descriptor)
    except FileExistsError:
        details = path.lstat()
        if is_link(details) or not stat.S_ISREG(details.st_mode) or details.st_nlink != 1:
            message = 'watcher state must be a private regular file without links'
            raise ValueError(message) from None
        if os.name != 'nt' and (
            details.st_uid != os.getuid() or stat.S_IMODE(details.st_mode) & 0o077
        ):
            message = 'watcher state file must be owned by you with permissions 0600'
            raise ValueError(message) from None
