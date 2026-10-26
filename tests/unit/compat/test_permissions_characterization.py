"""Characterization tests for the v1-compatible permission helpers.

These pin the authorization policy exposed as ``maivn.PermissionFlag``,
``maivn.PermissionSet`` and ``maivn.require_permissions``: which flags exist,
how sets combine, which flags count as destructive, and the exact refusal raised
when a grant is short of a requirement.
"""

from __future__ import annotations

import pytest

from maivn import PERMISSION_FLAG_NAMES, PermissionFlag, PermissionSet, require_permissions

_ALL_NAMES = ['read', 'write', 'delete', 'export', 'import', 'admin', 'impersonate']


def test_flag_names_are_the_canonical_v1_vocabulary_in_declaration_order() -> None:
    """Every non-empty flag maps to its lowercase v1 name, in declaration order."""
    assert list(PERMISSION_FLAG_NAMES.values()) == _ALL_NAMES
    assert list(PERMISSION_FLAG_NAMES) == [
        PermissionFlag.READ,
        PermissionFlag.WRITE,
        PermissionFlag.DELETE,
        PermissionFlag.EXPORT,
        PermissionFlag.IMPORT,
        PermissionFlag.ADMIN,
        PermissionFlag.IMPERSONATE,
    ]
    assert PermissionFlag.NONE.value == 0
    for flag, name in PERMISSION_FLAG_NAMES.items():
        assert PermissionSet.from_names([name]).flags == flag


@pytest.mark.parametrize(
    ('name', 'destructive'),
    [
        ('read', False),
        ('write', False),
        ('export', False),
        ('delete', True),
        ('import', True),
        ('admin', True),
        ('impersonate', True),
    ],
)
def test_is_destructive_matches_each_single_flag(name: str, *, destructive: bool) -> None:
    """A set is destructive exactly when it holds one of the destructive flags."""
    permissions = PermissionSet.from_names([name])
    assert permissions.is_destructive() is destructive


def test_mixed_set_with_one_destructive_flag_is_destructive() -> None:
    """One destructive flag among safe ones makes the whole set destructive."""
    assert PermissionSet.from_names(['read', 'write', 'admin']).is_destructive() is True
    assert PermissionSet.from_names(['read', 'write', 'export']).is_destructive() is False


def test_default_permission_set_is_empty_and_not_destructive() -> None:
    """A bare set carries no flags at all."""
    permissions = PermissionSet()
    assert permissions.flags == PermissionFlag.NONE
    assert permissions.is_empty() is True
    assert permissions.is_destructive() is False
    assert permissions.to_list() == []
    assert repr(permissions) == 'PermissionSet([])'


def test_from_names_unions_known_names_and_ignores_duplicates() -> None:
    """Known names accumulate into one bitfield; repeats change nothing."""
    permissions = PermissionSet.from_names(['write', 'read', 'write'])
    assert permissions.flags == PermissionFlag.READ | PermissionFlag.WRITE
    assert permissions.to_list() == ['read', 'write']
    assert permissions.is_empty() is False


def test_from_names_of_nothing_is_empty() -> None:
    """An empty iterable yields the empty set."""
    assert PermissionSet.from_names([]).is_empty() is True


def test_from_names_rejects_every_unknown_name_sorted() -> None:
    """Unknown names are all reported, sorted, and nothing is granted."""
    with pytest.raises(ValueError, match=r'^') as excinfo:
        _ = PermissionSet.from_names(['zeta', 'read', 'Admin', 'alpha'])
    assert str(excinfo.value) == "unknown permission flags: ['Admin', 'alpha', 'zeta']"


def test_from_names_is_case_sensitive() -> None:
    """Names must be the canonical lowercase spelling."""
    with pytest.raises(ValueError, match=r'^') as excinfo:
        _ = PermissionSet.from_names(['READ'])
    assert str(excinfo.value) == "unknown permission flags: ['READ']"


def test_all_contains_every_flag_in_order() -> None:
    """``PermissionSet.all()`` grants every defined flag."""
    everything = PermissionSet.all()
    assert everything.to_list() == _ALL_NAMES
    assert everything.is_destructive() is True
    for flag in PERMISSION_FLAG_NAMES:
        assert everything.includes(flag) is True
    assert repr(everything) == f'PermissionSet({_ALL_NAMES!r})'


def test_includes_requires_every_bit_of_a_combined_flag() -> None:
    """A combined flag is included only when all its bits are granted."""
    granted = PermissionSet(PermissionFlag.READ | PermissionFlag.WRITE)
    assert granted.includes(PermissionFlag.READ) is True
    assert granted.includes(PermissionFlag.READ | PermissionFlag.WRITE) is True
    assert granted.includes(PermissionFlag.READ | PermissionFlag.DELETE) is False
    assert granted.includes(PermissionFlag.NONE) is True


def test_contains_accepts_flags_and_rejects_other_objects() -> None:
    """``in`` checks flags and is False for names or other values."""
    granted = PermissionSet(PermissionFlag.EXPORT)
    assert PermissionFlag.EXPORT in granted
    assert PermissionFlag.READ not in granted
    assert 'export' not in granted
    assert PermissionFlag.EXPORT.value not in granted


def test_union_and_intersection_accept_sets_and_flags() -> None:
    """``|`` and ``&`` combine with sets or raw flags and return new sets."""
    read = PermissionSet(PermissionFlag.READ)
    write = PermissionSet(PermissionFlag.WRITE)
    both = read | write
    assert isinstance(both, PermissionSet)
    assert both.to_list() == ['read', 'write']
    assert (read | PermissionFlag.ADMIN).to_list() == ['read', 'admin']
    assert (both & read).to_list() == ['read']
    assert (both & PermissionFlag.WRITE).to_list() == ['write']
    assert (both & PermissionFlag.DELETE).is_empty() is True
    assert read.to_list() == ['read']


def test_equality_against_sets_flags_and_foreign_values() -> None:
    """Sets equal sets or flags with the same bits and nothing else."""
    granted = PermissionSet(PermissionFlag.READ | PermissionFlag.WRITE)
    assert granted == PermissionSet.from_names(['write', 'read'])
    assert granted == PermissionFlag.READ | PermissionFlag.WRITE
    assert granted != PermissionSet(PermissionFlag.READ)
    assert granted != PermissionFlag.READ
    assert granted != ['read', 'write']
    assert granted != 'read'


def test_equal_sets_hash_alike_and_deduplicate() -> None:
    """Equal sets share a hash so they collapse in sets and dict keys."""
    first = PermissionSet.from_names(['read', 'admin'])
    second = PermissionSet(PermissionFlag.ADMIN | PermissionFlag.READ)
    assert hash(first) == hash(second)
    assert len({first, second}) == 1


def test_repr_lists_names_in_declaration_order() -> None:
    """The debug repr lists canonical names, not insertion order."""
    permissions = PermissionSet.from_names(['impersonate', 'read', 'export'])
    assert repr(permissions) == "PermissionSet(['read', 'export', 'impersonate'])"


def test_require_permissions_passes_when_grant_covers_requirement() -> None:
    """A grant that covers every required flag returns None silently."""
    granted = PermissionSet.from_names(['read', 'write', 'delete'])
    require_permissions(granted, PermissionFlag.READ)
    require_permissions(granted, PermissionSet.from_names(['read', 'delete']))
    require_permissions(granted, PermissionSet())
    require_permissions(PermissionSet(), PermissionFlag.NONE)


def test_require_permissions_names_only_the_missing_flags() -> None:
    """The refusal lists the missing flags in declaration order, not the granted ones."""
    granted = PermissionSet.from_names(['read'])
    required = PermissionSet.from_names(['impersonate', 'read', 'write', 'admin'])
    with pytest.raises(PermissionError, match=r'^') as excinfo:
        require_permissions(granted, required)
    assert str(excinfo.value) == "missing required permissions: ['write', 'admin', 'impersonate']"


def test_require_permissions_accepts_a_raw_flag_requirement() -> None:
    """A raw flag requirement is checked like a one-flag set."""
    with pytest.raises(PermissionError, match=r'^') as excinfo:
        require_permissions(PermissionSet(), PermissionFlag.DELETE)
    assert str(excinfo.value) == "missing required permissions: ['delete']"


def test_require_permissions_refuses_a_partial_combined_flag() -> None:
    """Holding part of a combined requirement is still a refusal."""
    granted = PermissionSet(PermissionFlag.WRITE)
    with pytest.raises(PermissionError, match=r'^') as excinfo:
        require_permissions(granted, PermissionFlag.WRITE | PermissionFlag.EXPORT)
    assert str(excinfo.value) == "missing required permissions: ['export']"


def test_admin_does_not_imply_other_flags() -> None:
    """There is no flag hierarchy: admin grants admin and nothing else."""
    admin = PermissionSet(PermissionFlag.ADMIN)
    with pytest.raises(PermissionError, match=r'^') as excinfo:
        require_permissions(admin, PermissionFlag.READ)
    assert str(excinfo.value) == "missing required permissions: ['read']"
