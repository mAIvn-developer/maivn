"""The committed record of every API call the SDK makes matches the SDK.

Each `drivers_*.py` module next to this file runs public SDK methods against a
recording fake API (see `recording.py`). `api_calls_snapshot.json` is the
value-free result: method, path, query names, request body shape, and the
response fields the SDK reads. The platform's root contract test compares it
with the servers' OpenAPI documents, so it must never drift from the SDK.
Regenerate it by running this file's tests with MAIVN_WRITE_API_CALLS_SNAPSHOT=1
set, and review the diff: every change is a change in what the SDK sends or reads.

Two checks prove the drivers saw everything the SDK can call:

* every `/v1/` path the SDK source spells out was requested by some driver, and
* every public async method of every SDK class that sent a request was driven.
"""

from __future__ import annotations

import ast
import asyncio
import importlib
import inspect
import json
import os
import re
from functools import cache
from pathlib import Path
from typing import TYPE_CHECKING, Any

import maivn
from tests.contract.api_calls.recording import FakeApi, RecordedCall, observing_byte_replies

if TYPE_CHECKING:
    from tests.contract.api_calls.recording import Driver

HERE = Path(__file__).parent
SNAPSHOT_PATH = HERE / 'api_calls_snapshot.json'
WRITE_ENV = 'MAIVN_WRITE_API_CALLS_SNAPSHOT'
SDK_SOURCE = Path(maivn.__file__).parent
# The SDK spelled out 54 request paths on 2026-10-09; far fewer means the walk is broken.
MIN_LITERAL_PATHS = 40

# Public async methods of request-sending classes that never appear on a request's stack,
# each with the reason: no request of their own, or requests sent only from a background task
# (a task's stack starts at the task, so the method that created it is not on it).
NO_REQUEST_METHODS: dict[str, str] = {
    'maivn._internal.decisions.runtime:DecisionClient.abatch': (
        'sends each request from asyncio.create_task; run_decisions drives it with '
        'native_batch=True and its /v1/decisions requests are recorded'
    ),
}

# `/v1/` literals in the SDK source that no driver requests, each with the reason.
UNDRIVEN_PATHS: dict[str, str] = {}


def _drivers() -> list[Driver]:
    drivers: list[Driver] = []
    for module_path in sorted(HERE.glob('drivers_*.py')):
        module = importlib.import_module(f'tests.contract.api_calls.{module_path.stem}')
        drivers.extend(module.DRIVERS)
    return drivers


async def _drive(driver: Driver) -> list[RecordedCall]:
    api = FakeApi(driver.__name__)
    with observing_byte_replies(api.transport):
        await driver(api)
    return api.calls


@cache
def recorded_calls() -> tuple[RecordedCall, ...]:
    """Run every driver once, each against its own fake API."""
    calls: list[RecordedCall] = []
    for driver in _drivers():
        calls.extend(asyncio.run(_drive(driver)))
    return tuple(calls)


def _snapshot_text() -> str:
    entries = [call.snapshot() for call in recorded_calls()]
    entries.sort(key=lambda entry: json.dumps(entry, sort_keys=True))
    return json.dumps({'calls': entries}, indent=1, sort_keys=True) + '\n'


def test_api_calls_snapshot_matches_the_sdk() -> None:
    """Fail on any change in what the SDK sends or reads until the file is regenerated."""
    live = _snapshot_text()
    if os.environ.get(WRITE_ENV) == '1':
        SNAPSHOT_PATH.write_text(live, encoding='utf-8', newline='\n')
    committed = SNAPSHOT_PATH.read_text(encoding='utf-8')

    assert committed == live, (
        f'{SNAPSHOT_PATH.name} differs from what the SDK does; regenerate it with '
        f'{WRITE_ENV}=1 and review the diff'
    )


def _literal_paths() -> set[str]:
    """Every request path the SDK source spells out, placeholders kept as `{}`."""
    paths: set[str] = set()
    for source in SDK_SOURCE.rglob('*.py'):
        for node in ast.walk(ast.parse(source.read_text(encoding='utf-8'))):
            if isinstance(node, ast.Constant) and isinstance(node.value, str):
                text = node.value
            elif isinstance(node, ast.JoinedStr):
                text = ''.join(
                    str(part.value) if isinstance(part, ast.Constant) else '{}'
                    for part in node.values
                )
            else:
                continue
            start = text.find('/v1/')
            if start < 0:
                continue
            path = re.sub(r'\{[^}]*\}', '{}', text[start:])
            # A route pattern, a prefix test or a message, not a request path.
            if any(char in path for char in '()\\ ?') or path.endswith('/'):
                continue
            paths.add(path)
    return paths


def test_every_path_the_sdk_spells_out_is_driven() -> None:
    """A route string the drivers never request is a route the contract never checks."""
    requested = {call.path for call in recorded_calls()}
    literals = _literal_paths()
    assert len(literals) > MIN_LITERAL_PATHS, (
        'the source walk found too few paths to be reading the SDK'
    )

    undriven = {
        literal
        for literal in literals
        if not any(
            re.fullmatch(re.escape(literal).replace(r'\{\}', '[^/]+'), path) for path in requested
        )
    }

    assert undriven - set(UNDRIVEN_PATHS) == set(), 'add a driver for these SDK paths'
    assert set(UNDRIVEN_PATHS) - undriven == set(), 'these paths are driven now; drop them'


def _public_async_methods(owner: type[Any]) -> set[str]:
    return {
        name
        for name, member in vars(owner).items()
        if not name.startswith('_')
        and (inspect.iscoroutinefunction(member) or inspect.isasyncgenfunction(member))
    }


def test_every_public_async_method_of_a_requesting_class_is_driven() -> None:
    """Driving one method of a facade and not its sibling would leave the sibling unchecked."""
    callers = {caller for call in recorded_calls() for caller in call.callers}
    owners = {owner: _resolve_class(owner) for owner in {c.rpartition('.')[0] for c in callers}}

    expected = {
        f'{key}.{method}' for key, cls in owners.items() for method in _public_async_methods(cls)
    }

    # Resolve listed methods directly: on Python 3.10 `asyncio.wait_for` runs its awaitable in a
    # new task, so a class can be missing from `callers` there while its methods still exist.
    gone = {
        key
        for key in NO_REQUEST_METHODS
        if key.rpartition('.')[2]
        not in _public_async_methods(_resolve_class(key.rpartition('.')[0]))
    }

    assert expected - callers - set(NO_REQUEST_METHODS) == set(), 'drive these SDK methods'
    assert gone == set(), 'these methods no longer exist'
    assert set(NO_REQUEST_METHODS) & callers == set(), 'these methods send requests now'


def _resolve_class(owner: str) -> type[Any]:
    module_name, _, qualname = owner.partition(':')
    candidate: Any = importlib.import_module(module_name)
    for part in qualname.split('.'):
        candidate = getattr(candidate, part)
    return candidate
