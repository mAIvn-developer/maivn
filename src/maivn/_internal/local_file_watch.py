"""Explicit metadata-only polling with an immutable, durable local delivery journal."""

from __future__ import annotations

import hashlib
import hmac
import http.client
import json
import math
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from http import HTTPStatus
from pathlib import Path
from typing import TYPE_CHECKING
from uuid import uuid4

from maivn_contracts.scenarios import LocalFileConnectionSelector, LocalFileEvent

from maivn._internal.connection_events import receiver_address
from maivn._internal.local_file_scan import MAX_SCAN_ENTRIES, FileIdentity, scan_files
from maivn._internal.local_file_security import is_link
from maivn._internal.local_file_state import FileJournal

if TYPE_CHECKING:
    from collections.abc import Callable
    from threading import Event
    from types import TracebackType

    from typing_extensions import Self

MAX_PENDING = 10_000
_MAX_PATTERN = 1024
_MAX_SUFFIX = 64


@dataclass(frozen=True)
class FileScanResult:
    """Observable progress and explicit backpressure, without private local paths."""

    queued_events: int
    pending_events: int
    queue_full: bool


@dataclass(frozen=True)
class FileDeliveryResult:
    """Canonical receiver acceptance, never an agent-completion claim."""

    accepted_events: int
    pending_events: int
    status_code: int | None


class LocalFileWatcher:
    """Poll a chosen root explicitly; construction and imports never start a process.

    Only relative path, size and modification time leave this machine. The quiet
    period coalesces writes but cannot prove a writer closed its file. For that
    workflow, write/rename the target then create or replace its completion marker.
    A marker must be at least as recent as the target and change for each revision.
    The first scan records a baseline unless ``emit_existing`` is explicitly true.
    """

    def __init__(  # noqa: PLR0913 - explicit independent producer configuration.
        self,
        root: str | Path,
        *,
        watch_id: str,
        connection_id: str,
        receiver_url: str,
        secret: str,
        state_path: str | Path | None = None,
        pattern: str = '*',
        recursive: bool = False,
        quiet_seconds: float = 1.0,
        completion_suffix: str | None = None,
        emit_existing: bool = False,
        max_pending: int = MAX_PENDING,
        max_entries: int = MAX_SCAN_ENTRIES,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        """Validate configuration; opening the context explicitly opens local state."""
        self._receiver = receiver_address(receiver_url, connection_id)
        self._watch_id = LocalFileConnectionSelector(kind='local_file', watch_id=watch_id).watch_id
        if type(secret) is not str or not secret:
            message = 'a private nonempty signing secret is required'
            raise ValueError(message)
        _validate_options(pattern, quiet_seconds, completion_suffix, max_pending)
        if type(max_entries) is not int or not 1 <= max_entries <= MAX_SCAN_ENTRIES:
            message = 'scan entry limit must be between 1 and 100000'
            raise ValueError(message)
        if type(recursive) is not bool or type(emit_existing) is not bool:
            message = 'recursive and emit_existing must be booleans'
            raise ValueError(message)
        self._secret = secret.encode()
        chosen_root = Path(root).absolute()
        if any(is_link(path.lstat()) for path in (chosen_root, *chosen_root.parents)):
            message = 'watch root must not traverse a symlink or junction'
            raise ValueError(message)
        self._root = chosen_root.resolve(strict=True)
        if not self._root.is_dir():
            message = 'watch root must be a directory'
            raise ValueError(message)
        details = self._root.lstat()
        self._root_identity = (details.st_dev, details.st_ino)
        self._pattern = pattern
        self._recursive = recursive
        self._quiet = quiet_seconds
        self._suffix = completion_suffix
        self._emit_existing = emit_existing
        self._max_pending = max_pending
        self._max_entries = max_entries
        self._clock = clock
        self._candidates: dict[str, tuple[FileIdentity, float]] = {}
        self._configuration = json.dumps(
            [str(self._root), receiver_url, watch_id, pattern, recursive, completion_suffix],
            separators=(',', ':'),
        )
        identity = hashlib.sha256(self._configuration.encode()).hexdigest()
        self._state_path = (
            Path(state_path).absolute()
            if state_path is not None
            else Path.home() / '.maivn' / 'watch-files' / identity / 'state.sqlite3'
        )
        if self._state_path.resolve().is_relative_to(self._root):
            message = 'watcher state must be outside the watched directory'
            raise ValueError(message)
        self._journal: FileJournal | None = None

    def __enter__(self) -> Self:
        """Acquire private SQLite state and an exclusive process-lifetime lock."""
        if self._journal is not None:
            message = 'watcher state is already open'
            raise RuntimeError(message)
        self._journal = FileJournal(self._state_path, configuration=self._configuration)
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        """Release state and lock without discarding pending delivery metadata."""
        del exc_type, exc, traceback
        self.close()

    def close(self) -> None:
        """Stop using the journal; no background thread or subprocess exists."""
        if self._journal is not None:
            self._journal.close()
            self._journal = None

    @property
    def pending_count(self) -> int:
        """Return the durable unsatisfied count."""
        return self._state().pending_count

    def _state(self) -> FileJournal:
        if self._journal is None:
            message = 'open the watcher with a context manager first'
            raise RuntimeError(message)
        return self._journal

    def scan(self) -> FileScanResult:
        """Observe one metadata snapshot and atomically queue quiet completed changes."""
        state = self._state()
        self._check_root()
        found = scan_files(
            self._root,
            pattern=self._pattern,
            recursive=self._recursive,
            completion_suffix=self._suffix,
            max_entries=self._max_entries,
        )
        self._check_root()
        if not state.initialized:
            state.baseline({} if self._emit_existing else found)
        prior = state.files()
        state.forget(set(prior) - set(found))
        self._candidates = {path: item for path, item in self._candidates.items() if path in found}
        now = self._clock()
        queued = 0
        queue_full = False
        for path, identity in sorted(found.items()):
            previous = prior.get(path)
            if not self._ready(path, identity, previous, now):
                continue
            if state.pending_count >= self._max_pending:
                queue_full = True
                continue
            event = LocalFileEvent(
                event_id=str(uuid4()),
                watch_id=self._watch_id,
                operation='modified' if previous else 'created',
                observed_at=datetime.now(timezone.utc),
                relative_path=path,
                size_bytes=identity.size,
                mtime_ns=str(identity.mtime_ns),
            )
            body = json.dumps(
                event.model_dump(mode='json'),
                ensure_ascii=False,
                sort_keys=True,
                separators=(',', ':'),
            ).encode()
            state.append(path, identity, event_id=event.event_id, body=body)
            del self._candidates[path]
            queued += 1
        return FileScanResult(queued, state.pending_count, queue_full)

    def _check_root(self) -> None:
        details = self._root.lstat()
        if is_link(details) or (details.st_dev, details.st_ino) != self._root_identity:
            message = 'watch root changed identity; restart with an explicitly selected root'
            raise RuntimeError(message)

    def _ready(
        self, path: str, identity: FileIdentity, previous: FileIdentity | None, now: float
    ) -> bool:
        if previous and (previous.size, previous.mtime_ns) == (identity.size, identity.mtime_ns):
            self._candidates.pop(path, None)
            return False
        if self._suffix and (
            not identity.marker or (previous and identity.marker == previous.marker)
        ):
            self._candidates.pop(path, None)
            return False
        candidate = self._candidates.get(path)
        if candidate is None or candidate[0] != identity:
            self._candidates[path] = (identity, now)
            return False
        return now - candidate[1] >= self._quiet

    def deliver_pending(self, *, max_events: int = 100) -> FileDeliveryResult:
        """Attempt oldest pending events once each, settling only canonical HTTP 202."""
        if type(max_events) is not int or not 1 <= max_events <= MAX_PENDING:
            message = 'delivery batch size must be between 1 and 10000'
            raise ValueError(message)
        state = self._state()
        accepted = 0
        status: int | None = None
        for _ in range(max_events):
            pending = state.first()
            if pending is None:
                break
            try:
                status = self._send(pending[1])
            except (OSError, http.client.HTTPException):
                status = None
            if status != HTTPStatus.ACCEPTED:
                break
            state.settle(pending[0])
            accepted += 1
        return FileDeliveryResult(accepted, state.pending_count, status)

    def run(
        self,
        *,
        stop_event: Event,
        scan_interval: float = 0.5,
        on_progress: Callable[[FileScanResult, FileDeliveryResult], None] | None = None,
    ) -> None:
        """Poll in the calling process until its explicit stop event is set.

        Enter the watcher context first. The caller owns stopping this loop;
        no background process or observer starts from importing a declaration.
        """
        if (
            isinstance(scan_interval, bool)
            or not math.isfinite(scan_interval)
            or scan_interval <= 0
        ):
            message = 'scan interval must be finite and positive'
            raise ValueError(message)
        while not stop_event.is_set():
            observed = self.scan()
            delivered = self.deliver_pending()
            if on_progress is not None:
                on_progress(observed, delivered)
            _ = stop_event.wait(scan_interval)

    def _send(self, body: bytes) -> int:
        scheme, host, port, path = self._receiver
        timestamp = str(int(time.time()))
        signed = b'POST.' + path.encode() + b'.' + timestamp.encode() + b'.' + body
        signature = hmac.new(self._secret, signed, hashlib.sha256).hexdigest()
        connection_type = (
            http.client.HTTPSConnection if scheme == 'https' else http.client.HTTPConnection
        )
        connection = connection_type(host, port, timeout=10)
        try:
            connection.request(
                'POST',
                path,
                body,
                {
                    'Content-Type': 'application/json',
                    'X-Maivn-Signature': f't={timestamp},v1={signature}',
                },
            )
            return connection.getresponse().status
        finally:
            connection.close()


def _validate_options(pattern: object, quiet: object, suffix: object, capacity: object) -> None:
    if (
        not isinstance(pattern, str)
        or not pattern
        or len(pattern) > _MAX_PATTERN
        or '\\' in pattern
        or '..' in pattern.split('/')
        or pattern.startswith('/')
    ):
        message = 'glob must be a bounded relative POSIX pattern'
        raise ValueError(message)
    if (
        isinstance(quiet, bool)
        or not isinstance(quiet, (int, float))
        or not math.isfinite(quiet)
        or quiet < 0
    ):
        message = 'quiet period must be finite and nonnegative'
        raise ValueError(message)
    if suffix is not None and (
        not isinstance(suffix, str)
        or not suffix.startswith('.')
        or len(suffix) > _MAX_SUFFIX
        or any(char in suffix for char in '/\\:')
        or suffix.strip() != suffix
    ):
        message = 'completion suffix must start with a dot and contain no path separators'
        raise ValueError(message)
    if type(capacity) is not int or not 1 <= capacity <= MAX_PENDING:
        message = 'pending capacity must be between 1 and 10000'
        raise ValueError(message)
