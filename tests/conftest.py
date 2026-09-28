"""Suite-wide isolation from the developer's own machine state."""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

if TYPE_CHECKING:
    from pathlib import Path


@pytest.fixture(autouse=True)
def isolated_local_vault(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """Keep the automatic vault away from a real local vault on this machine.

    Without this, any test that constructs a Client finds the developer's
    existing vault and tries to verify its account identity over HTTP.
    """
    monkeypatch.setenv('MAIVN_VAULT_DIRECTORY', str(tmp_path / 'vault'))
