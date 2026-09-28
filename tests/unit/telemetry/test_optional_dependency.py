"""Packaging boundary tests for the optional OpenTelemetry adapter."""

from __future__ import annotations

import subprocess
import sys
from importlib.metadata import requires


def test_core_telemetry_import_does_not_import_opentelemetry() -> None:
    """Core hooks retain zero OpenTelemetry runtime dependencies."""
    script = (
        'import sys; import maivn.telemetry; '
        'assert not any(name.startswith("opentelemetry") for name in sys.modules)'
    )

    completed = subprocess.run(  # noqa: S603 - current test interpreter is trusted.
        [sys.executable, '-c', script],
        check=False,
        capture_output=True,
        text=True,
    )

    assert completed.returncode == 0, completed.stderr


def test_published_extra_depends_on_api_only() -> None:
    """The runtime extra never forces an SDK, processor, or exporter choice."""
    package_requirements = requires('maivn') or []
    otel_requirements = [
        requirement
        for requirement in package_requirements
        if requirement.casefold().startswith('opentelemetry')
    ]

    assert len(otel_requirements) == 1
    assert otel_requirements[0].startswith('opentelemetry-api<2,>=1.30')
    assert "extra == 'otel'" in otel_requirements[0]
    assert all('opentelemetry-sdk' not in requirement for requirement in package_requirements)


def test_adapter_import_without_api_names_the_otel_extra() -> None:
    """Missing optional API dependency produces an actionable installation error."""
    script = """
import sys

class BlockOpenTelemetry:
    def find_spec(self, fullname, path=None, target=None):
        if fullname == "opentelemetry" or fullname.startswith("opentelemetry."):
            raise ModuleNotFoundError("blocked for test", name=fullname)
        return None

sys.meta_path.insert(0, BlockOpenTelemetry())
import maivn.telemetry
try:
    import maivn.telemetry.opentelemetry
except ModuleNotFoundError as exc:
    assert "pip install maivn[otel]" in str(exc)
else:
    raise AssertionError("adapter import unexpectedly succeeded")
"""

    completed = subprocess.run(  # noqa: S603 - current test interpreter is trusted.
        [sys.executable, '-c', script],
        check=False,
        capture_output=True,
        text=True,
    )

    assert completed.returncode == 0, completed.stderr
