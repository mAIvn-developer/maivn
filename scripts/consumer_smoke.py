"""Import every base SDK module from a clean installed wheel."""

from __future__ import annotations

import importlib
import importlib.metadata
import json
import pkgutil
import sys

OPTIONAL = (
    'maivn.events.fastapi',
    'maivn.telemetry.opentelemetry',
    'maivn._internal.reporting.terminal_reporter.reporters.rich_reporter',
)


def _import_tree(name: str, imported: list[str], skipped: list[str]) -> None:
    """Import each module after excluding explicitly optional integration prefixes."""
    if any(name == prefix or name.startswith(prefix + '.') for prefix in OPTIONAL):
        skipped.append(name)
        return
    module = importlib.import_module(name)
    imported.append(name)
    for child in pkgutil.iter_modules(getattr(module, '__path__', ()), name + '.'):
        _import_tree(child.name, imported, skipped)


def main() -> None:
    """Fail on any undeclared import error and report the explicit extras exclusions."""
    imported: list[str] = []
    skipped: list[str] = []
    for package in ('maivn', 'maivn_contracts', 'private_data_vault'):
        _import_tree(package, imported, skipped)
    sys.stdout.write(
        json.dumps(
            {
                'sdk_version': importlib.metadata.version('maivn'),
                'imported_count': len(imported),
                'imported': imported,
                'optional_exclusions': skipped,
            },
            indent=2,
        )
        + '\n'
    )


if __name__ == '__main__':
    main()
