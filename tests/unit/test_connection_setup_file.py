"""Private terminal secret output refuses shared, preexisting and redirected paths."""

from __future__ import annotations

import os
import subprocess
from typing import TYPE_CHECKING

import pytest

import maivn._internal.connection_setup_file as files
from maivn._internal.connection_setup_file import PrivateSetupFile
from maivn._internal.local_file_security import private_file

if TYPE_CHECKING:
    from pathlib import Path


def test_new_file_is_owner_private_before_any_secret_is_written(tmp_path: Path) -> None:
    """Verify actual Windows ACL/POSIX mode, not just the requested creation flags."""
    target = tmp_path / 'private' / 'secret.json'
    output = PrivateSetupFile(str(target))
    try:
        private_file(target)
        assert target.read_bytes() == b''
        output.write({'one_time_secret': 'synthetic-private-value'})
        private_file(target)
    finally:
        output.close(remove_empty=True)
    assert target.exists()


def test_shared_directory_is_refused_without_changing_its_permissions(tmp_path: Path) -> None:
    """Never silently chmod an existing shared location to make it acceptable."""
    shared = tmp_path / 'shared'
    shared.mkdir(mode=0o755)
    if os.name != 'nt':
        shared.chmod(0o755)
    before = shared.stat().st_mode
    with pytest.raises(ValueError, match='state directory'):
        PrivateSetupFile(str(shared / 'secret.json'))
    assert shared.stat().st_mode == before
    assert not (shared / 'secret.json').exists()


def test_parent_link_or_windows_junction_is_refused_before_creating_a_file(tmp_path: Path) -> None:
    """Exercise a real reparse point on Windows and a real symlink on POSIX."""
    actual = tmp_path / 'actual'
    actual.mkdir()
    linked = tmp_path / 'linked'
    if os.name == 'nt':
        result = subprocess.run(  # noqa: S603 - explicit temporary junction fixture, no credentials.
            ['cmd.exe', '/c', 'mklink', '/J', str(linked), str(actual)],  # noqa: S607 - Windows builtin.
            capture_output=True,
            check=False,
        )
        assert result.returncode == 0
    else:
        linked.symlink_to(actual, target_is_directory=True)
    try:
        with pytest.raises(ValueError, match='symlinks or junctions'):
            PrivateSetupFile(str(linked / 'private' / 'secret.json'))
        assert not (actual / 'private').exists()
    finally:
        if os.name == 'nt':
            linked.rmdir()
        else:
            linked.unlink()


def test_failed_create_removes_only_the_reserved_empty_file(tmp_path: Path) -> None:
    """An existing destination cannot be replaced or removed by cleanup."""
    target = tmp_path / 'private' / 'secret.json'
    output = PrivateSetupFile(str(target))
    output.close(remove_empty=True)
    assert not target.exists()
    target.write_text('preexisting')
    with pytest.raises(FileExistsError):
        PrivateSetupFile(str(target))
    assert target.read_text() == 'preexisting'


def test_permission_loss_between_reservation_and_write_refuses_secret(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Recheck the destination at write time, after the network round trip."""
    target = tmp_path / 'private' / 'secret.json'
    output = PrivateSetupFile(str(target))

    def permission_lost(_path: Path) -> None:
        message = 'file permissions changed'
        raise ValueError(message)

    monkeypatch.setattr(files, 'private_file', permission_lost)
    try:
        with pytest.raises(ValueError, match='permissions changed'):
            output.write({'one_time_secret': 'must-not-reach-disk'})
        assert target.read_bytes() == b''
    finally:
        output.close(remove_empty=True)
