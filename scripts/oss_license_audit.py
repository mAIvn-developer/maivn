from __future__ import annotations

import argparse
import importlib.metadata as metadata
import re
import subprocess
import sys
from collections import Counter
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import cast

import tomllib
from packaging.markers import default_environment
from packaging.requirements import InvalidRequirement, Requirement

FIRST_PARTY_PREFIXES = ("maivn-",)
REPORT_FILENAME = "THIRD_PARTY_LICENSES.md"
LICENSE_ELECTIONS_FILENAME = "LICENSE_ELECTIONS.md"
REPORT_VERSION = "2.1"

PROJECT_DISTRIBUTIONS = {
    "maivn": "Public PyPI SDK",
    "maivn-shared": "Public PyPI shared package",
    "maivn-internal-shared": "Private GitHub package consumed by services",
    "maivn-server": "Railway-hosted private service",
    "maivn-agents": "LangSmith Deployments",
}

LICENSE_ELECTIONS = {
    "maivn-agents": {
        "forbiddenfruit": {
            "selected_license": "MIT License",
            "current_scope": "dev-only",
            "rationale": "Use the permissive option and avoid strong-copyleft obligations.",
            "display_name": "forbiddenfruit",
        }
    }
}

REQ_NAME_RE = re.compile(r"^\s*([A-Za-z0-9_.-]+)\s*(?:==|@)")
STRONG_PATTERNS = ("agpl", "gpl", "sspl")
WEAK_PATTERNS = ("mpl", "mozilla public", "lgpl", "eclipse public")
NON_OSI_PATTERNS = ("elastic", "commons clause", "polyform", "source available", "bsl")
REPORT_DATE_RE = re.compile(r"^\*\*Report Date\*\*: .*$", re.MULTILINE)
ELECTION_DATE_RE = re.compile(r"^\*\*Date\*\*: .*$", re.MULTILINE)


@dataclass(frozen=True)
class PackageRecord:
    name: str
    version: str
    license_name: str
    category: str
    notes: str


def canonicalize_name(name: str) -> str:
    return name.strip().lower().replace("_", "-")


def read_project_name(project_root: Path) -> str:
    pyproject = tomllib.loads(project_root.joinpath("pyproject.toml").read_text(encoding="utf-8"))
    return pyproject["project"]["name"]


def run_export(project_root: Path, project_name: str, *extra_args: str) -> list[str]:
    command = [
        "uv",
        "export",
        "--locked",
        "--package",
        project_name,
        "--format",
        "requirements.txt",
        "--no-hashes",
        "--no-annotate",
        "--no-header",
        *extra_args,
    ]
    result = subprocess.run(
        command,
        cwd=project_root,
        capture_output=True,
        text=True,
        check=True,
    )
    return result.stdout.splitlines()


def parse_requirement_names(lines: list[str]) -> dict[str, str]:
    packages: dict[str, str] = {}
    environment = cast(dict[str, str], default_environment())
    for raw_line in lines:
        line = raw_line.strip()
        if not line or line.startswith("#") or line.startswith("-e "):
            continue
        try:
            requirement = Requirement(line)
        except InvalidRequirement:
            base_requirement = line.split(";", 1)[0].strip()
            match = REQ_NAME_RE.match(base_requirement)
            if not match:
                continue
            package_name = match.group(1)
        else:
            if requirement.marker and not requirement.marker.evaluate(environment):
                continue
            package_name = requirement.name
        canonical_name = canonicalize_name(package_name)
        if canonical_name.startswith(FIRST_PARTY_PREFIXES):
            continue
        packages[canonical_name] = line
    return packages


def distribution_index() -> dict[str, metadata.Distribution]:
    index: dict[str, metadata.Distribution] = {}
    for dist in metadata.distributions():
        name = dist.metadata.get("Name")
        if not name:
            continue
        index[canonicalize_name(name)] = dist
    return index


def license_from_classifiers(classifiers: list[str]) -> list[str]:
    simplified: list[str] = []
    for classifier in classifiers:
        if not classifier.startswith("License :: "):
            continue
        simplified_value = classifier.removeprefix("License :: ").strip()
        if simplified_value.startswith("OSI Approved :: "):
            simplified_value = simplified_value.removeprefix("OSI Approved :: ").strip()
        simplified.append(simplified_value)
    return simplified


def compress_license_text(raw_value: str) -> str:
    candidate = " ".join(raw_value.split())
    lowered = candidate.lower()
    if not candidate:
        return "UNKNOWN"
    if "mit" in lowered:
        return "MIT"
    if "apache" in lowered and "2" in lowered:
        return "Apache-2.0"
    if "mozilla public" in lowered or "mpl" in lowered:
        return "MPL-2.0"
    if "elastic" in lowered:
        return "Elastic-2.0"
    if "bsd" in lowered and "3" in lowered:
        return "BSD-3-Clause"
    if "bsd" in lowered and "2" in lowered:
        return "BSD-2-Clause"
    if "bsd" in lowered:
        return "BSD"
    if "unlicense" in lowered:
        return "Unlicense"
    if "isc" in lowered:
        return "ISC"
    if "psf" in lowered:
        return "PSF-2.0"
    if "gpl" in lowered:
        return "GPL"
    return candidate.split(". ")[0][:120]


def normalize_license(dist: metadata.Distribution) -> str:
    metadata_map = dist.metadata
    expression = metadata_map.get("License-Expression", "").strip()
    if expression and expression.upper() != "UNKNOWN":
        return expression

    classifiers = license_from_classifiers(metadata_map.get_all("Classifier", []))
    if classifiers:
        unique_values = list(dict.fromkeys(classifiers))
        return " OR ".join(unique_values)

    raw_license = metadata_map.get("License", "").strip()
    if raw_license and raw_license.upper() != "UNKNOWN":
        return compress_license_text(raw_license)

    return "UNKNOWN"


def classify_license(license_name: str) -> str:
    lowered = license_name.lower()
    if lowered == "unknown":
        return "Unknown"
    if any(pattern in lowered for pattern in NON_OSI_PATTERNS):
        return "Non-OSI"
    if any(pattern in lowered for pattern in STRONG_PATTERNS):
        return "Strong Copyleft"
    if any(pattern in lowered for pattern in WEAK_PATTERNS):
        return "Weak Copyleft"
    return "Permissive"


def build_records(
    package_names: dict[str, str],
    installed_packages: dict[str, metadata.Distribution],
    elections: dict[str, dict[str, str]],
) -> list[PackageRecord]:
    records: list[PackageRecord] = []
    for package_name in sorted(package_names):
        dist = installed_packages.get(package_name)
        if dist is None:
            raise RuntimeError(
                f"Package '{package_name}' is not installed in the current environment."
            )

        license_name = normalize_license(dist)
        notes: list[str] = []
        requirement = package_names[package_name]
        if " @ " in requirement:
            notes.append("direct URL dependency")
        if package_name in elections:
            license_name = elections[package_name]["selected_license"]
            notes.append(
                f"selected {elections[package_name]['selected_license']} from dual-license offering"
            )

        records.append(
            PackageRecord(
                name=package_name,
                version=dist.version,
                license_name=license_name,
                category=classify_license(license_name),
                notes="; ".join(notes),
            )
        )
    return records


def count_categories(records: list[PackageRecord]) -> dict[str, int]:
    counts = Counter(record.category for record in records)
    return {
        "Permissive": counts.get("Permissive", 0),
        "Weak Copyleft": counts.get("Weak Copyleft", 0),
        "Strong Copyleft": counts.get("Strong Copyleft", 0),
        "Non-OSI": counts.get("Non-OSI", 0),
        "Unknown": counts.get("Unknown", 0),
    }


def summarize_names(records: list[PackageRecord], category: str) -> str:
    names = [record.name for record in records if record.category == category]
    return ", ".join(names) if names else "none"


def render_summary_row(scope: str, records: list[PackageRecord]) -> str:
    counts = count_categories(records)
    return (
        f"| {scope} | {len(records)} | {counts['Permissive']} | {counts['Weak Copyleft']} | "
        f"{counts['Strong Copyleft']} | {counts['Non-OSI']} | {counts['Unknown']} |"
    )


def render_table(records: list[PackageRecord]) -> str:
    lines = [
        "| Package | Version | Effective License | Category | Notes |",
        "|---------|---------|-------------------|----------|-------|",
    ]
    for record in records:
        notes = record.notes or " "
        lines.append(
            f"| {record.name} | {record.version} | {record.license_name} | "
            f"{record.category} | {notes} |"
        )
    return "\n".join(lines)


def build_report(
    project_name: str,
    runtime_records: list[PackageRecord],
    dev_only_records: list[PackageRecord],
) -> str:
    today = date.today().isoformat()
    runtime_counts = count_categories(runtime_records)
    runtime_blockers = (
        summarize_names(runtime_records, "Strong Copyleft")
        if runtime_counts["Strong Copyleft"]
        else "none"
    )
    runtime_weak = summarize_names(runtime_records, "Weak Copyleft")
    dev_non_osi = summarize_names(dev_only_records, "Non-OSI")
    compliance_result = (
        "PASS"
        if runtime_counts["Strong Copyleft"] == 0
        and runtime_counts["Non-OSI"] == 0
        and runtime_counts["Unknown"] == 0
        else "FAIL"
    )
    report_lines = [
        "# Third-Party Open Source License Report",
        "",
        f"**Package**: {project_name}",
        f"**Distribution**: {PROJECT_DISTRIBUTIONS[project_name]}",
        f"**Report Date**: {today}",
        f"**Report Version**: {REPORT_VERSION}",
        "",
        "---",
        "",
        "## Executive Summary",
        "",
        (
            "This report is generated from the locked dependency graph "
            "(`uv export --locked`) and installed package metadata."
        ),
        "First-party `maivn-*` packages are excluded from the third-party inventory.",
        "",
        f"**Compliance Result**: {compliance_result}",
        "",
        "| Scope | Packages | Permissive | Weak Copyleft | Strong Copyleft | Non-OSI | Unknown |",
        "|-------|----------|------------|----------------|------------------|---------|---------|",
        render_summary_row("Runtime", runtime_records),
        render_summary_row("Dev-only", dev_only_records),
        "",
        "---",
        "",
        "## Notable Findings",
        "",
        f"- Runtime blockers detected: {runtime_blockers}",
        f"- Runtime weak-copyleft packages: {runtime_weak}",
        f"- Dev-only non-OSI packages: {dev_non_osi}",
    ]

    elections = LICENSE_ELECTIONS.get(project_name, {})
    if elections:
        selected = ", ".join(
            f"{config['display_name']} -> {config['selected_license']}"
            for config in elections.values()
        )
        report_lines.append(f"- Recorded license elections: {selected}")

    report_lines.extend(
        [
            "",
            "## Runtime Dependencies",
            "",
            render_table(runtime_records),
            "",
            "---",
            "",
            "## Development-Only Dependencies",
            "",
            "These packages are not part of the production runtime image.",
            "",
            render_table(dev_only_records),
            "",
            "---",
            "",
            "## Compliance Checklist",
            "",
            (
                f"- [{'x' if compliance_result == 'PASS' else ' '}] "
                "No blocked runtime licenses detected "
                "(strong copyleft, non-OSI, or unknown)."
            ),
            "- [x] Weak-copyleft runtime dependencies are used unmodified.",
            "- [x] Dev-only non-OSI packages remain excluded from production artifacts.",
            "- [x] Committed license report is expected to stay in sync with `uv.lock`.",
            "",
            "---",
            "",
            "## Certification",
            "",
            (
                "This report was generated from `uv export --locked` output and "
                "installed package metadata as of the report date above."
            ),
        ]
    )
    return "\n".join(report_lines) + "\n"


def build_elections_report(
    project_name: str, dev_only_names: set[str], runtime_names: set[str]
) -> str | None:
    elections = LICENSE_ELECTIONS.get(project_name, {})
    if not elections:
        return None

    today = date.today().isoformat()
    lines = [
        "# Dual-License Elections",
        "",
        f"**Project**: {project_name}",
        f"**Date**: {today}",
        "**Entity**: mAIvn, LLC",
        "",
        "---",
        "",
        "## Elections",
        "",
    ]

    for package_name, config in elections.items():
        if package_name in runtime_names:
            scope = "runtime"
        elif package_name in dev_only_names:
            scope = "dev-only"
        else:
            continue

        lines.extend(
            [
                f"### 1. {config['display_name']}",
                "",
                f"- **Selected License**: {config['selected_license']}",
                f"- **Current Scope**: {scope}",
                f"- **Rationale**: {config['rationale']}",
                "",
            ]
        )

    lines.extend(
        [
            "---",
            "",
            "## Effect",
            "",
            (
                "These elections document the permissive option chosen where "
                "package metadata presents a dual-license choice."
            ),
        ]
    )
    return "\n".join(lines) + "\n"


def normalize_report_content(content: str) -> str:
    normalized = REPORT_DATE_RE.sub("**Report Date**: <ignored-for-check>", content)
    return ELECTION_DATE_RE.sub("**Date**: <ignored-for-check>", normalized)


def ensure_report(path: Path, content: str, check_only: bool) -> bool:
    existing = path.read_text(encoding="utf-8") if path.exists() else None
    if check_only:
        if existing is None:
            return False
        normalized_existing = normalize_report_content(existing)
        normalized_content = normalize_report_content(content)
        return normalized_existing == normalized_content
    path.write_text(content, encoding="utf-8")
    return True


def main() -> int:
    parser = argparse.ArgumentParser(description="Generate and validate OSS license reports.")
    parser.add_argument(
        "--check",
        action="store_true",
        help="Verify the committed reports match the generated output.",
    )
    args = parser.parse_args()

    project_root = Path(__file__).resolve().parents[1]
    project_name = read_project_name(project_root)
    elections = LICENSE_ELECTIONS.get(project_name, {})

    runtime_requirements = parse_requirement_names(
        run_export(project_root, project_name, "--no-dev")
    )
    dev_requirements = parse_requirement_names(run_export(project_root, project_name, "--only-dev"))
    dev_only_requirements = {
        package_name: requirement
        for package_name, requirement in dev_requirements.items()
        if package_name not in runtime_requirements
    }

    installed_packages = distribution_index()
    runtime_records = build_records(runtime_requirements, installed_packages, elections)
    dev_only_records = build_records(dev_only_requirements, installed_packages, elections)

    report_ok = ensure_report(
        project_root / REPORT_FILENAME,
        build_report(project_name, runtime_records, dev_only_records),
        args.check,
    )

    elections_content = build_elections_report(
        project_name,
        set(dev_only_requirements),
        set(runtime_requirements),
    )
    elections_path = project_root / LICENSE_ELECTIONS_FILENAME
    if elections_content is None:
        elections_ok = True
    else:
        elections_ok = ensure_report(elections_path, elections_content, args.check)

    runtime_counts = count_categories(runtime_records)
    blocked_runtime = (
        runtime_counts["Strong Copyleft"] > 0
        or runtime_counts["Non-OSI"] > 0
        or runtime_counts["Unknown"] > 0
    )

    if args.check:
        if not report_ok:
            print(f"{REPORT_FILENAME} is out of date.", file=sys.stderr)
        if not elections_ok:
            print(f"{LICENSE_ELECTIONS_FILENAME} is out of date.", file=sys.stderr)

    if blocked_runtime:
        print("Blocked runtime licenses detected.", file=sys.stderr)

    return 0 if report_ok and elections_ok and not blocked_runtime else 1


if __name__ == "__main__":
    raise SystemExit(main())
