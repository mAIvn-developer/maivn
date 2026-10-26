"""Every route constant in the wire module names a route the SDK actually calls."""

from __future__ import annotations

import re
from pathlib import Path

from maivn._internal import wire

_SDK_SOURCE = Path(wire.__file__).resolve().parents[1]


def test_every_wire_path_constant_is_used_outside_the_wire_module() -> None:
    """A path nothing sends to is dead and misreports the SDK's API surface."""
    constants = sorted(name for name in vars(wire) if name.endswith('_PATH'))
    callers = '\n'.join(
        path.read_text(encoding='utf-8')
        for path in _SDK_SOURCE.rglob('*.py')
        if path.resolve() != Path(wire.__file__).resolve()
    )

    unused = [name for name in constants if not re.search(rf'\b{name}\b', callers)]

    assert constants
    assert unused == []
