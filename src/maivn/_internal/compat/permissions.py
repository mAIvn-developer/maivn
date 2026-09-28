"""V1-compatible permission helpers."""

from __future__ import annotations

from enum import Flag, auto
from typing import TYPE_CHECKING, ClassVar

if TYPE_CHECKING:
    from collections.abc import Iterable


class PermissionFlag(Flag):
    """Categories of work a tool may perform."""

    NONE = 0
    READ = auto()
    WRITE = auto()
    DELETE = auto()
    EXPORT = auto()
    IMPORT = auto()
    ADMIN = auto()
    IMPERSONATE = auto()


PERMISSION_FLAG_NAMES: dict[PermissionFlag, str] = {
    PermissionFlag.READ: 'read',
    PermissionFlag.WRITE: 'write',
    PermissionFlag.DELETE: 'delete',
    PermissionFlag.EXPORT: 'export',
    PermissionFlag.IMPORT: 'import',
    PermissionFlag.ADMIN: 'admin',
    PermissionFlag.IMPERSONATE: 'impersonate',
}

NAME_TO_PERMISSION_FLAG: dict[str, PermissionFlag] = {
    name: flag for flag, name in PERMISSION_FLAG_NAMES.items()
}

DESTRUCTIVE_FLAG_NAMES: frozenset[str] = frozenset(
    {'delete', 'import', 'admin', 'impersonate'},
)


class PermissionSet:
    """Immutable set of permission flags with v1 inspection helpers."""

    __slots__: ClassVar[tuple[str, ...]] = ('_flags',)

    _flags: PermissionFlag

    def __init__(self, flags: PermissionFlag = PermissionFlag.NONE) -> None:
        """Create a permission set from a bitflag value."""
        self._flags = flags

    @classmethod
    def from_names(cls, names: Iterable[str]) -> PermissionSet:
        """Build a permission set from canonical flag names."""
        unknown: list[str] = []
        accumulator = PermissionFlag.NONE
        for name in names:
            flag = NAME_TO_PERMISSION_FLAG.get(name)
            if flag is None:
                unknown.append(name)
                continue
            accumulator |= flag
        if unknown:
            message = f'unknown permission flags: {sorted(unknown)!r}'
            raise ValueError(message)
        return cls(accumulator)

    @classmethod
    def all(cls) -> PermissionSet:
        """Return a permission set containing every defined flag."""
        flags = PermissionFlag.NONE
        for flag in PERMISSION_FLAG_NAMES:
            flags |= flag
        return cls(flags)

    @property
    def flags(self) -> PermissionFlag:
        """Return the underlying bitfield."""
        return self._flags

    def includes(self, flag: PermissionFlag) -> bool:
        """Return True when every bit in ``flag`` is present."""
        return (self._flags & flag) == flag

    def is_empty(self) -> bool:
        """Return True when no flag is set."""
        return self._flags == PermissionFlag.NONE

    def is_destructive(self) -> bool:
        """Return True when this set includes any destructive flag."""
        destructive = (
            PermissionFlag.DELETE
            | PermissionFlag.IMPORT
            | PermissionFlag.ADMIN
            | PermissionFlag.IMPERSONATE
        )
        return bool(self._flags & destructive)

    def to_list(self) -> list[str]:
        """Return canonical flag names in declaration order."""
        return [PERMISSION_FLAG_NAMES[flag] for flag in PERMISSION_FLAG_NAMES if flag & self._flags]

    def __or__(self, other: PermissionSet | PermissionFlag) -> PermissionSet:
        """Return a union with another permission set or flag."""
        flags = other.flags if isinstance(other, PermissionSet) else other
        return PermissionSet(self._flags | flags)

    def __and__(self, other: PermissionSet | PermissionFlag) -> PermissionSet:
        """Return an intersection with another permission set or flag."""
        flags = other.flags if isinstance(other, PermissionSet) else other
        return PermissionSet(self._flags & flags)

    def __contains__(self, flag: object) -> bool:
        """Return True when ``flag`` is included."""
        if isinstance(flag, PermissionFlag):
            return self.includes(flag)
        return False

    def __eq__(self, other: object) -> bool:
        """Compare with another set or raw flag."""
        if isinstance(other, PermissionSet):
            return self._flags == other.flags
        if isinstance(other, PermissionFlag):
            return self._flags == other
        return False

    def __hash__(self) -> int:
        """Return a stable hash."""
        return hash(self._flags)

    def __repr__(self) -> str:
        """Return the v1-style debug representation."""
        return f'PermissionSet({self.to_list()!r})'


def require_permissions(granted: PermissionSet, required: PermissionSet | PermissionFlag) -> None:
    """Raise PermissionError if ``granted`` lacks any required flag."""
    required_set = required if isinstance(required, PermissionSet) else PermissionSet(required)
    missing_flags = required_set.flags & ~granted.flags
    if missing_flags:
        missing = PermissionSet(missing_flags).to_list()
        message = f'missing required permissions: {missing!r}'
        raise PermissionError(message)


__all__ = [
    'DESTRUCTIVE_FLAG_NAMES',
    'NAME_TO_PERMISSION_FLAG',
    'PERMISSION_FLAG_NAMES',
    'PermissionFlag',
    'PermissionSet',
    'require_permissions',
]
