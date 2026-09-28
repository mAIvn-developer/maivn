"""Package-boundary checks for the published SDK wheel."""

from __future__ import annotations

import shutil
import subprocess
from email import policy
from email.parser import BytesParser
from pathlib import Path
from zipfile import ZipFile

PROJECT_ROOT = Path(__file__).parents[2]


def test_wheel_requires_the_private_data_vault_native_binding(tmp_path: Path) -> None:
    """The SDK wheel must install the version-matched native vault binding."""
    uv = shutil.which('uv')
    assert uv is not None

    subprocess.run(  # noqa: S603 - fixed local build command and project path.
        [uv, 'build', '--wheel', '--out-dir', str(tmp_path)],
        check=True,
        cwd=PROJECT_ROOT,
    )

    wheel = next(tmp_path.glob('maivn-*.whl'))
    with ZipFile(wheel) as archive:
        metadata_path = next(
            path for path in archive.namelist() if path.endswith('.dist-info/METADATA')
        )
        metadata = BytesParser(policy=policy.default).parsebytes(archive.read(metadata_path))

    assert 'private-data-vault==0.0.0' in metadata.get_all('Requires-Dist', [])
