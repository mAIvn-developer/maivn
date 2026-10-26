"""Characterize owner-private local storage checks and the single-writer state lock."""

from __future__ import annotations

import os
import stat
import subprocess
import sys
from types import SimpleNamespace
from typing import TYPE_CHECKING, cast

import pytest

from maivn._internal.local_file_security import (
    StateLock,
    is_link,
    private_directory,
    private_file,
)

if TYPE_CHECKING:
    from pathlib import Path

_WINDOWS = os.name == 'nt'
_NOT_PRIVATE_WINDOWS = 'watcher state directory must allow access only to its current user owner'
_EVERYONE = '*S-1-1-0'
windows_only = pytest.mark.skipif(not _WINDOWS, reason='Windows ACL behavior')
posix_only = pytest.mark.skipif(_WINDOWS, reason='POSIX permission-bit behavior')


def _junction(target: Path, link: Path) -> None:
    """Create a Windows directory junction, a reparse point that is not a symlink."""
    if sys.platform == 'win32':
        import _winapi  # noqa: PLC0415 - Windows-only standard library.

        _winapi.CreateJunction(str(target), str(link))


def _icacls(path: Path, *arguments: str) -> None:
    """Edit an ACL with the local Windows tool; nothing leaves this machine."""
    subprocess.run(  # noqa: S603 - fixed local system tool, no shell, no network.
        ['icacls', str(path), *arguments],  # noqa: S607 - resolved from the system directory.
        check=True,
        capture_output=True,
    )


def _current_user_sid() -> str:
    """Return this process's user SID, which owns every file the test creates."""
    result = subprocess.run(
        ['whoami', '/user', '/fo', 'csv', '/nh'],  # noqa: S607 - resolved from the system directory.
        check=True,
        capture_output=True,
        text=True,
    )
    return result.stdout.strip().split(',')[-1].strip('"')


def test_regular_entries_are_not_links(tmp_path: Path) -> None:
    """Ordinary files and directories are not treated as links."""
    file = tmp_path / 'file'
    file.write_bytes(b'x')
    assert not is_link(file.lstat())
    assert not is_link(tmp_path.lstat())


def test_symlinks_are_links(tmp_path: Path) -> None:
    """A symbolic link is rejected by its mode bits."""
    link = tmp_path / 'link'
    try:
        link.symlink_to(tmp_path)
    except OSError:
        pytest.skip('symbolic links are not permitted for this account')
    assert is_link(link.lstat())


def test_reparse_attribute_alone_marks_a_link() -> None:
    """A Windows reparse attribute is a link even when the mode is a regular file."""

    # os.stat_result drops st_file_attributes off Windows, so a stand-in carries it everywhere.
    def details(attributes: int) -> os.stat_result:
        return cast(
            'os.stat_result',
            SimpleNamespace(st_mode=stat.S_IFREG | 0o600, st_file_attributes=attributes),
        )

    assert is_link(details(0x400))
    assert not is_link(details(0x20))


@windows_only
def test_junctions_are_links(tmp_path: Path) -> None:
    """A directory junction is a link although its mode is not a symlink."""
    target = tmp_path / 'target'
    target.mkdir()
    junction = tmp_path / 'junction'
    _junction(target, junction)
    details = junction.lstat()
    assert not stat.S_ISLNK(details.st_mode)
    assert is_link(details)


def test_private_directory_creates_missing_parents_and_reaccepts(tmp_path: Path) -> None:
    """A newly created private directory, parents included, passes later checks."""
    target = tmp_path / 'a' / 'b' / 'state'
    private_directory(target)
    assert target.is_dir()
    private_directory(target)


def test_exclusive_private_directory_refuses_an_existing_one(tmp_path: Path) -> None:
    """Exclusive creation fails when the directory already exists."""
    target = tmp_path / 'state'
    private_directory(target)
    with pytest.raises(FileExistsError):
        private_directory(target, exclusive=True)


def test_private_directory_refuses_files_and_junctions(tmp_path: Path) -> None:
    """An existing non-directory or link is never accepted as state storage."""
    file = tmp_path / 'file'
    file.write_bytes(b'x')
    with pytest.raises(ValueError, match=r'^watcher state requires a private regular directory$'):
        private_directory(file)
    if _WINDOWS:
        target = tmp_path / 'target'
        private_directory(target)
        junction = tmp_path / 'junction'
        _junction(target, junction)
        with pytest.raises(
            ValueError, match=r'^watcher state requires a private regular directory$'
        ):
            private_directory(junction)


@windows_only
def test_existing_inherited_directory_is_refused(tmp_path: Path) -> None:
    """A directory with inherited, shared access is not adopted as private storage."""
    shared = tmp_path / 'shared'
    shared.mkdir()
    with pytest.raises(ValueError, match=f'^{_NOT_PRIVATE_WINDOWS}$'):
        private_directory(shared)


@posix_only
def test_group_readable_directory_is_refused(tmp_path: Path) -> None:
    """A directory with group or other permission bits is refused."""
    shared = tmp_path / 'shared'
    shared.mkdir()
    shared.chmod(0o750)
    with pytest.raises(ValueError, match='permissions 0700'):
        private_directory(shared)


def test_private_file_refuses_directories(tmp_path: Path) -> None:
    """Private storage must be a regular file."""
    with pytest.raises(ValueError, match=r'^private storage requires a regular file$'):
        private_file(tmp_path)


@windows_only
def test_caller_created_file_is_protected_and_inherited_file_is_refused(tmp_path: Path) -> None:
    """Only a file the caller just created is locked down; an inherited one is refused."""
    created = tmp_path / 'created'
    created.write_bytes(b'x')
    private_file(created, created=True)
    private_file(created)
    inherited = tmp_path / 'inherited'
    inherited.write_bytes(b'x')
    with pytest.raises(ValueError, match=f'^{_NOT_PRIVATE_WINDOWS}$'):
        private_file(inherited)


@windows_only
def test_any_second_access_entry_is_refused(tmp_path: Path) -> None:
    """A protected file that also grants another principal access is refused."""
    path = tmp_path / 'secret'
    path.write_bytes(b'x')
    private_file(path, created=True)
    owner = _current_user_sid()
    _icacls(path, '/remove:g', f'*{owner}')
    _icacls(path, '/grant', f'{_EVERYONE}:(R)')
    _icacls(path, '/grant', f'*{owner}:(F)')
    with pytest.raises(ValueError, match=f'^{_NOT_PRIVATE_WINDOWS}$'):
        private_file(path)


@windows_only
def test_single_entry_for_another_principal_is_refused(tmp_path: Path) -> None:
    """One access entry is only accepted when it allows the current owner."""
    path = tmp_path / 'secret'
    path.write_bytes(b'x')
    private_file(path, created=True)
    owner = _current_user_sid()
    _icacls(path, '/grant:r', f'{_EVERYONE}:(R)')
    _icacls(path, '/remove:g', f'*{owner}')
    with pytest.raises(ValueError, match=f'^{_NOT_PRIVATE_WINDOWS}$'):
        private_file(path)


@windows_only
def test_single_deny_entry_is_refused(tmp_path: Path) -> None:
    """A lone deny entry for the owner is not an owner-only allow list."""
    path = tmp_path / 'secret'
    path.write_bytes(b'x')
    private_file(path, created=True)
    owner = _current_user_sid()
    _icacls(path, '/deny', f'*{owner}:(WD)')
    _icacls(path, '/remove:g', f'*{owner}')
    with pytest.raises(ValueError, match=f'^{_NOT_PRIVATE_WINDOWS}$'):
        private_file(path)
    _icacls(path, '/remove:d', f'*{owner}')


def test_state_lock_is_exclusive_until_closed(tmp_path: Path) -> None:
    """A second writer is refused until the first releases the lock."""
    path = tmp_path / 'state.lock'
    first = StateLock(path)
    with pytest.raises(
        RuntimeError, match=r'^another watcher is already using this state file$'
    ) as caught:
        StateLock(path)
    assert caught.value.__cause__ is None
    assert caught.value.__suppress_context__
    first.close()
    second = StateLock(path)
    second.close()
    assert path.is_file()


def test_state_lock_refuses_directories_and_links(tmp_path: Path) -> None:
    """The lock path must be a regular file, never a directory or a link."""
    with pytest.raises(ValueError, match=r'^watcher lock must be a regular private file$'):
        StateLock(tmp_path)
    if _WINDOWS:
        target = tmp_path / 'target'
        target.mkdir()
        junction = tmp_path / 'junction'
        _junction(target, junction)
        with pytest.raises(ValueError, match=r'^watcher lock must be a regular private file$'):
            StateLock(junction)
