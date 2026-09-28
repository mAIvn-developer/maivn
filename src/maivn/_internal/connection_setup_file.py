"""Exclusive owner-private destination reserved before a connection is created."""

from __future__ import annotations

import json
import os
import stat
from pathlib import Path
from typing import TYPE_CHECKING

from maivn._internal.local_file_security import is_link, private_directory, private_file

if TYPE_CHECKING:
    from typing import BinaryIO


def _no_links(path: Path) -> None:
    for part in (path, *path.parents):
        try:
            details = part.lstat()
        except FileNotFoundError:
            continue
        if is_link(details):
            message = 'private destination cannot contain symlinks or junctions'
            raise ValueError(message)


class PrivateSetupFile:
    """Hold an exclusively created file; refuse any preexisting destination."""

    def __init__(self, destination: str) -> None:
        """Reserve an empty private file and verify permissions before any HTTP call."""
        self.path = Path(destination).expanduser().absolute()
        self._written = False
        _no_links(self.path)
        private_directory(self.path.parent)
        _no_links(self.path.parent)
        flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, 'O_NOFOLLOW', 0)
        self._stream: BinaryIO = os.fdopen(os.open(self.path, flags, 0o600), 'wb')
        self._identity = os.fstat(self._stream.fileno())
        try:
            private_file(self.path, created=True)
            self._verify()
        except BaseException:
            self.close(remove_empty=True)
            raise

    def _verify(self) -> None:
        _no_links(self.path)
        private_directory(self.path.parent)
        actual = self.path.lstat()
        descriptor = os.fstat(self._stream.fileno())
        if not stat.S_ISREG(actual.st_mode) or not os.path.samestat(actual, descriptor):
            message = 'private destination changed'
            raise ValueError(message)
        private_file(self.path)

    def write(self, receipt: dict[str, object]) -> None:
        """Write and flush only after checking the held file still has private permissions."""
        self._verify()
        # Once writing starts, retain any partial private file for recovery.
        self._written = True
        _ = self._stream.write((json.dumps(receipt, indent=2) + '\n').encode('utf-8'))
        self._stream.flush()
        os.fsync(self._stream.fileno())

    def close(self, *, remove_empty: bool) -> None:
        """Remove only our still-empty inode on a failed create; never another path."""
        self._stream.close()
        if remove_empty and not self._written:
            try:
                current = self.path.lstat()
                if (
                    not is_link(current)
                    and not current.st_size
                    and os.path.samestat(current, self._identity)
                ):
                    self.path.unlink()
            except OSError:
                return
