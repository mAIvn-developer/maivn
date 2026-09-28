"""Owner-private local watcher state and an OS-released single-writer lock."""

from __future__ import annotations

import ctypes
import os
import re
import stat
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from pathlib import Path
    from typing import BinaryIO

_REPARSE_POINT = 0x400
_OWNER_DACL = 0x5
_PROTECTED_DACL = 0x80000004


def is_link(stat_result: os.stat_result) -> bool:
    """Reject POSIX symlinks and every Windows reparse point, including junctions."""
    return stat.S_ISLNK(stat_result.st_mode) or bool(
        getattr(stat_result, 'st_file_attributes', 0) & _REPARSE_POINT
    )


def private_directory(path: Path, *, exclusive: bool = False) -> None:
    """Create private state storage, or refuse an existing shared directory."""
    created = False
    try:
        path.mkdir(mode=0o700, parents=True)
        created = True
    except FileExistsError:
        if exclusive:
            raise
    result = path.lstat()
    if is_link(result) or not stat.S_ISDIR(result.st_mode):
        message = 'watcher state requires a private regular directory'
        raise ValueError(message)
    if os.name == 'nt':
        _windows_directory(path, create=created)
    elif result.st_uid != os.getuid() or stat.S_IMODE(result.st_mode) & 0o077:
        message = 'watcher state directory must be owned by you with permissions 0700'
        raise ValueError(message)


def private_file(path: Path, *, created: bool = False) -> None:
    """Verify an owner-only regular file; protect only a caller-created Windows file."""
    details = path.lstat()
    if is_link(details) or not stat.S_ISREG(details.st_mode):
        message = 'private storage requires a regular file'
        raise ValueError(message)
    if os.name == 'nt':
        _windows_directory(path, create=created)
    elif details.st_uid != os.getuid() or stat.S_IMODE(details.st_mode) & 0o077:
        message = 'private file must be owned by you with permissions 0600'
        raise ValueError(message)


def _windows_sid() -> str:
    advapi = ctypes.WinDLL('advapi32', use_last_error=True)
    kernel = ctypes.WinDLL('kernel32', use_last_error=True)
    kernel.GetCurrentProcess.restype = ctypes.c_void_p
    advapi.OpenProcessToken.argtypes = [ctypes.c_void_p, ctypes.c_ulong, ctypes.c_void_p]
    token = ctypes.c_void_p()
    if not advapi.OpenProcessToken(kernel.GetCurrentProcess(), 0x8, ctypes.byref(token)):
        raise ctypes.WinError()
    try:
        size = ctypes.c_ulong()
        advapi.GetTokenInformation.argtypes = [
            ctypes.c_void_p,
            ctypes.c_int,
            ctypes.c_void_p,
            ctypes.c_ulong,
            ctypes.c_void_p,
        ]
        advapi.GetTokenInformation(token, 1, None, 0, ctypes.byref(size))
        buffer = ctypes.create_string_buffer(size.value)
        if not advapi.GetTokenInformation(token, 1, buffer, size, ctypes.byref(size)):
            raise ctypes.WinError()
        sid = ctypes.cast(buffer, ctypes.POINTER(ctypes.c_void_p))[0]
        text = ctypes.c_wchar_p()
        advapi.ConvertSidToStringSidW.argtypes = [ctypes.c_void_p, ctypes.c_void_p]
        if not advapi.ConvertSidToStringSidW(sid, ctypes.byref(text)):
            raise ctypes.WinError()
        try:
            return text.value or ''
        finally:
            kernel.LocalFree.argtypes = [ctypes.c_void_p]
            kernel.LocalFree(text)
    finally:
        kernel.CloseHandle.argtypes = [ctypes.c_void_p]
        kernel.CloseHandle(token)


def _windows_directory(path: Path, *, create: bool) -> None:
    sid = _windows_sid()
    if create:
        _set_windows_private(path, sid)
    descriptor = _windows_descriptor(path)
    entries = re.findall(r'\(([^()]*)\)', descriptor)
    if (
        not descriptor.startswith(f'O:{sid}')
        or 'D:P' not in descriptor
        or len(entries) != 1
        or not entries[0].startswith('A;')
        or not entries[0].endswith(f';;;{sid}')
    ):
        message = 'watcher state directory must allow access only to its current user owner'
        raise ValueError(message)


def _set_windows_private(path: Path, sid: str) -> None:
    advapi = ctypes.WinDLL('advapi32', use_last_error=True)
    kernel = ctypes.WinDLL('kernel32', use_last_error=True)
    descriptor = ctypes.c_void_p()
    advapi.ConvertStringSecurityDescriptorToSecurityDescriptorW.argtypes = [
        ctypes.c_wchar_p,
        ctypes.c_ulong,
        ctypes.c_void_p,
        ctypes.c_void_p,
    ]
    if not advapi.ConvertStringSecurityDescriptorToSecurityDescriptorW(
        f'O:{sid}D:P(A;OICI;FA;;;{sid})', 1, ctypes.byref(descriptor), None
    ):
        raise ctypes.WinError()
    try:
        advapi.SetFileSecurityW.argtypes = [ctypes.c_wchar_p, ctypes.c_ulong, ctypes.c_void_p]
        if not advapi.SetFileSecurityW(str(path), _PROTECTED_DACL | 1, descriptor):
            raise ctypes.WinError()
    finally:
        kernel.LocalFree.argtypes = [ctypes.c_void_p]
        kernel.LocalFree(descriptor)


def _windows_descriptor(path: Path) -> str:
    advapi = ctypes.WinDLL('advapi32', use_last_error=True)
    kernel = ctypes.WinDLL('kernel32', use_last_error=True)
    size = ctypes.c_ulong()
    advapi.GetFileSecurityW.argtypes = [
        ctypes.c_wchar_p,
        ctypes.c_ulong,
        ctypes.c_void_p,
        ctypes.c_ulong,
        ctypes.c_void_p,
    ]
    advapi.GetFileSecurityW(str(path), _OWNER_DACL, None, 0, ctypes.byref(size))
    buffer = ctypes.create_string_buffer(size.value)
    if not advapi.GetFileSecurityW(str(path), _OWNER_DACL, buffer, size, ctypes.byref(size)):
        raise ctypes.WinError()
    text = ctypes.c_wchar_p()
    advapi.ConvertSecurityDescriptorToStringSecurityDescriptorW.argtypes = [
        ctypes.c_void_p,
        ctypes.c_ulong,
        ctypes.c_ulong,
        ctypes.c_void_p,
        ctypes.c_void_p,
    ]
    if not advapi.ConvertSecurityDescriptorToStringSecurityDescriptorW(
        buffer, 1, _OWNER_DACL, ctypes.byref(text), None
    ):
        raise ctypes.WinError()
    try:
        return text.value or ''
    finally:
        kernel.LocalFree.argtypes = [ctypes.c_void_p]
        kernel.LocalFree(text)


class StateLock:
    """Hold one byte of a private lock file until close or process death."""

    def __init__(self, path: Path) -> None:
        """Acquire the OS lock without replacing an existing lock inode."""
        flags = os.O_RDWR | os.O_CREAT | getattr(os, 'O_NOFOLLOW', 0)
        if path.exists() and (is_link(path.lstat()) or not path.is_file()):
            message = 'watcher lock must be a regular private file'
            raise ValueError(message)
        self._file: BinaryIO = os.fdopen(os.open(path, flags, 0o600), 'r+b')
        try:
            if os.name == 'nt':
                import msvcrt  # noqa: PLC0415 - platform-only standard library.

                msvcrt.locking(self._file.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl  # noqa: PLC0415 - platform-only standard library.

                fcntl.flock(self._file, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            self._file.close()
            message = 'another watcher is already using this state file'
            raise RuntimeError(message) from None

    def close(self) -> None:
        """Release the lock; the lock file remains to preserve inode identity."""
        self._file.close()
