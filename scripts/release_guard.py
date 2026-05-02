from __future__ import annotations

import json
import os
import re
import sys
import urllib.error
import urllib.request
from pathlib import Path

import tomllib
from packaging.version import InvalidVersion, Version

VERSION_RE = re.compile(r'__version__\s*=\s*"([^"]+)"')


def read_project_metadata(project_root: Path) -> tuple[str, str]:
    pyproject = tomllib.loads(project_root.joinpath("pyproject.toml").read_text(encoding="utf-8"))
    project = pyproject["project"]
    project_name = project["name"]
    project_version = project.get("version")
    if project_version:
        return project_name, project_version

    version_path = pyproject.get("tool", {}).get("hatch", {}).get("version", {}).get("path")
    if not version_path:
        raise RuntimeError("Unable to determine project version from pyproject.toml.")

    version_text = project_root.joinpath(version_path).read_text(encoding="utf-8")
    match = VERSION_RE.search(version_text)
    if not match:
        raise RuntimeError(f"Unable to parse __version__ from {version_path}.")
    return project_name, match.group(1)


def fetch_published_versions(project_name: str) -> list[Version]:
    url = f"https://pypi.org/pypi/{project_name}/json"
    request = urllib.request.Request(url, headers={"Accept": "application/json"})
    try:
        # Fixed HTTPS PyPI API URL; no user-controlled scheme reaches urlopen.
        with urllib.request.urlopen(  # noqa: S310  # nosec B310
            request,
            timeout=15,
        ) as response:
            payload = json.load(response)
    except urllib.error.HTTPError as exc:
        if exc.code == 404:
            return []
        raise

    versions: list[Version] = []
    for raw_version in payload.get("releases", {}):
        try:
            versions.append(Version(raw_version))
        except InvalidVersion:
            continue
    return versions


def main() -> int:
    project_root = Path(__file__).resolve().parents[1]
    project_name, project_version = read_project_metadata(project_root)
    tag_name = os.environ.get("GITHUB_REF_NAME", "").strip()
    expected_tag = f"v{project_version}"

    if not tag_name:
        print("GITHUB_REF_NAME is required for release validation.", file=sys.stderr)
        return 1

    if tag_name != expected_tag:
        message = (
            "Tag/version mismatch: "
            f"expected {expected_tag} for {project_name} {project_version}, "
            f"got {tag_name}."
        )
        print(message, file=sys.stderr)
        return 1

    current_version = Version(project_version)
    published_versions = fetch_published_versions(project_name)
    if not published_versions:
        return 0

    latest_published = max(published_versions)
    if current_version <= latest_published:
        message = (
            f"{project_name} version {project_version} is not greater than "
            f"the latest published version {latest_published}."
        )
        print(message, file=sys.stderr)
        return 1

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
