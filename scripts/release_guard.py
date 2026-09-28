"""Release guard: publish only from the exact release tag, with a version newer than PyPI's.

Checks, in order, before any network call:
1. The workflow runs on a tag (GITHUB_REF_TYPE == "tag"), never on a branch or a
   workflow_dispatch from a branch, even one named like a tag.
2. GITHUB_REF is exactly refs/tags/v<version> and GITHUB_REF_NAME is v<version>.
3. <version> is greater than every version already published on PyPI.
"""

from __future__ import annotations

import json
import os
import re
import sys
import urllib.error
import urllib.request
from collections.abc import Mapping
from http import HTTPStatus
from pathlib import Path
from typing import cast

import tomllib
from packaging.version import InvalidVersion, Version

VERSION_RE = re.compile(r"""__version__\s*=\s*['"]([^'"]+)['"]""")


def read_project_metadata(project_root: Path) -> tuple[str, str]:
    """Read the distribution name and version from its checked-in metadata."""
    pyproject = cast(
        'Mapping[str, object]',
        tomllib.loads(project_root.joinpath('pyproject.toml').read_text(encoding='utf-8')),
    )
    project = cast('Mapping[str, object]', pyproject['project'])
    project_name = project['name']
    if not isinstance(project_name, str):
        message = 'pyproject.toml project.name must be a string.'
        raise TypeError(message)

    project_version = project.get('version')
    if isinstance(project_version, str) and project_version:
        return project_name, project_version

    tool = cast('Mapping[str, object]', pyproject.get('tool', {}))
    hatch = cast('Mapping[str, object]', tool.get('hatch', {}))
    version_config = cast('Mapping[str, object]', hatch.get('version', {}))
    version_path = version_config.get('path')
    if not isinstance(version_path, str) or not version_path:
        message = 'Unable to determine project version from pyproject.toml.'
        raise RuntimeError(message)

    version_text = project_root.joinpath(version_path).read_text(encoding='utf-8')
    match = VERSION_RE.search(version_text)
    if not match:
        message = f'Unable to parse __version__ from {version_path}.'
        raise RuntimeError(message)
    return project_name, match.group(1)


def check_release_ref(environ: Mapping[str, str], project_version: str) -> str | None:
    """Return why this run may not publish, or None when it runs on the exact release tag."""
    expected_tag = f'v{project_version}'
    ref_type = environ.get('GITHUB_REF_TYPE', '').strip()
    ref = environ.get('GITHUB_REF', '').strip()
    ref_name = environ.get('GITHUB_REF_NAME', '').strip()
    if ref_type != 'tag':
        return f'Releases publish only from a tag; GITHUB_REF_TYPE is {ref_type or "unset"!r}.'
    if ref != f'refs/tags/{expected_tag}':
        return f"GITHUB_REF is {ref or 'unset'!r}; expected 'refs/tags/{expected_tag}'."
    if ref_name != expected_tag:
        return f'GITHUB_REF_NAME is {ref_name or "unset"!r}; expected {expected_tag!r}.'
    return None


def fetch_published_versions(project_name: str) -> list[Version]:
    """Read the versions PyPI has already published for this distribution."""
    url = f'https://pypi.org/pypi/{project_name}/json'
    request = urllib.request.Request(url, headers={'Accept': 'application/json'})  # noqa: S310
    try:
        # The URL uses the fixed HTTPS PyPI origin; only the project name varies.
        response = urllib.request.urlopen(request, timeout=15)  # nosec B310  # noqa: S310
        try:
            payload_obj = cast('object', json.loads(response.read().decode('utf-8')))
        finally:
            response.close()
    except urllib.error.HTTPError as exc:
        if exc.code == HTTPStatus.NOT_FOUND:
            return []
        raise
    payload = cast('Mapping[str, object]', payload_obj)
    releases_obj = payload.get('releases', {})
    if not isinstance(releases_obj, Mapping):
        return []
    versions: list[Version] = []
    for raw_version in cast('Mapping[object, object]', releases_obj):
        if isinstance(raw_version, str):
            try:
                versions.append(Version(raw_version))
            except InvalidVersion:
                continue
    return versions


def main() -> int:
    """Refuse unsafe refs and versions before publishing can begin."""
    project_root = Path(__file__).resolve().parents[1]
    project_name, project_version = read_project_metadata(project_root)
    refusal = check_release_ref(os.environ, project_version)
    if refusal:
        sys.stderr.write(f'{refusal}\n')
        return 1
    current_version = Version(project_version)
    published_versions = fetch_published_versions(project_name)
    if published_versions and current_version <= max(published_versions):
        sys.stderr.write(
            f'{project_name} version {project_version} is not greater than the latest '
            f'published version {max(published_versions)}.\n',
        )
        return 1
    sys.stdout.write(
        f'{project_name} {project_version} is releasable from refs/tags/v{project_version}.\n'
    )
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
