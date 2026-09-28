"""Regular-file metadata scanning without opening file contents."""

from __future__ import annotations

import fnmatch
import os
import stat
from dataclasses import dataclass
from pathlib import Path

from maivn._internal.local_file_security import is_link

_MAX_SAFE_INTEGER = 9_007_199_254_740_991
_MAX_PATH = 1024
_MAX_MTIME = 99_999_999_999_999_999_999
_FIRST_PRINTABLE = 32
_SURROGATE_START = 0xD800
_SURROGATE_END = 0xDFFF
_TEMP_SUFFIXES = ('.tmp', '.temp', '.part', '.partial', '.crdownload', '.swp', '~')
MAX_SCAN_ENTRIES = 100_000


class FileScanLimitError(RuntimeError):
    """A complete observation exceeded its explicit traversal budget; cursor is unchanged."""


@dataclass(frozen=True)
class FileIdentity:
    """Stable metadata identity and optional independently completed marker."""

    size: int
    mtime_ns: int
    marker: str = ''


def scan_files(
    root: Path,
    *,
    pattern: str,
    recursive: bool,
    completion_suffix: str | None,
    max_entries: int = MAX_SCAN_ENTRIES,
) -> dict[str, FileIdentity]:
    """Return contained regular metadata; fail whole scans on directory I/O errors."""
    found: dict[str, FileIdentity] = {}
    directories = [root]
    examined = 0
    while directories:
        directory = directories.pop()
        if not _contained_regular(directory, root, directory=True):
            continue
        with os.scandir(directory) as entries:
            for entry in entries:
                examined += 1
                if examined > max_entries:
                    message = 'scan entry limit reached; choose a smaller root or raise max_entries'
                    raise FileScanLimitError(message)
                path = Path(entry.path)
                if recursive and _contained_regular(path, root, directory=True):
                    directories.append(path)
                identity = _file(path, root)
                relative = path.relative_to(root).as_posix()
                if identity and _matches(relative, pattern, completion_suffix):
                    marker = _marker(path, root, completion_suffix, identity)
                    found[relative] = FileIdentity(identity.size, identity.mtime_ns, marker)
    return found


def _matches(relative: str, pattern: str, suffix: str | None) -> bool:
    return (
        len(relative) <= _MAX_PATH
        and '\\' not in relative
        and ':' not in relative
        and not any(
            ord(char) < _FIRST_PRINTABLE or _SURROGATE_START <= ord(char) <= _SURROGATE_END
            for char in relative
        )
        and not relative.lower().endswith(_TEMP_SUFFIXES)
        and not (suffix and relative.endswith(suffix))
        and fnmatch.fnmatchcase(relative, pattern)
    )


def _contained_regular(path: Path, root: Path, *, directory: bool = False) -> bool:
    try:
        details = path.lstat()
        resolved = path.resolve(strict=True)
        resolved.relative_to(root)
        return not is_link(details) and (
            stat.S_ISDIR(details.st_mode) if directory else stat.S_ISREG(details.st_mode)
        )
    except (FileNotFoundError, NotADirectoryError, ValueError, RuntimeError):
        return False


def _file(path: Path, root: Path) -> FileIdentity | None:
    if not _contained_regular(path, root):
        return None
    try:
        details = path.lstat()
        if is_link(details) or not stat.S_ISREG(details.st_mode):
            return None
        path.resolve(strict=True).relative_to(root)
    except (FileNotFoundError, NotADirectoryError, ValueError, RuntimeError):
        return None
    if not (0 <= details.st_size <= _MAX_SAFE_INTEGER and 0 <= details.st_mtime_ns <= _MAX_MTIME):
        return None
    return FileIdentity(details.st_size, details.st_mtime_ns)


def _marker(path: Path, root: Path, suffix: str | None, identity: FileIdentity) -> str:
    if suffix is None:
        return ''
    marker = Path(str(path) + suffix)
    details = _file(marker, root)
    if details is None or details.mtime_ns < identity.mtime_ns:
        return ''
    try:
        result = marker.lstat()
    except (FileNotFoundError, NotADirectoryError):
        return ''
    return f'{result.st_dev}:{result.st_ino}:{details.size}:{details.mtime_ns}'
