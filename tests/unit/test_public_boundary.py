"""Fail-closed contracts for the publishable public SDK boundary."""

from __future__ import annotations

import ast
from pathlib import Path

from maivn import Agent, RunOptions, Swarm
from maivn._internal.wire import optional_run_options

SDK_ROOT = Path(__file__).resolve().parents[2]
SOURCE_ROOT = SDK_ROOT / 'src' / 'maivn'
PROHIBITED_IMPORT_ROOTS = frozenset(
    {'maivn_brain', 'maivn_control_plane', 'maivn_data_plane'},
)
PROHIBITED_MODEL_MARKERS = (
    'claude-',
    'gpt-',
    'gemini-',
    'llama-',
    'mistral-',
)
TEXT_BOUNDARY_ROOTS = (
    SDK_ROOT / 'src',
    SDK_ROOT / 'tests',
    SDK_ROOT / 'evals',
)
TEXT_BOUNDARY_FILES = (SDK_ROOT / 'pyproject.toml',)

FORBIDDEN_TOPOLOGY_TERMS = (
    'data plane',
    'data-plane',
    'control plane',
    'control-plane',
    'event plane',
    'event-plane',
    'maivn_control_plane_base_url',
    'maivn_event_plane_base_url',
    'maivn_vault_origin',
    # The private services' local ports; the local API origin is :8000.
    ':8100',
    ':8200',
    ':8300',
    ':8443',
)
# Shipped source only (never `tests/`): tests intentionally exercise the
# retired env var names to prove the SDK now ignores them.
TOPOLOGY_BOUNDARY_ROOT = SDK_ROOT / 'src'
TOPOLOGY_BOUNDARY_FILES = (SDK_ROOT / 'README.md',)


def test_public_sdk_never_names_backend_service_topology() -> None:
    """Shipped SDK text never tells a developer how mAIvn's backend is laid out.

    A developer using this SDK should know about one public entry point
    (``MAIVN_BASE_URL`` / ``base_url``) and nothing about internal service
    layers, per-service hosts, or the retired per-plane environment
    variables. Docstrings, README text, and error/log messages are all
    developer-visible, so they are all in scope, including the local terminal
    tools (connection setup, the webhook tunnel and the file watcher).
    """
    offenders: list[str] = []
    candidates = [
        path
        for path in TOPOLOGY_BOUNDARY_ROOT.rglob('*')
        if path.is_file() and '__pycache__' not in path.parts
    ]
    candidates.extend(path for path in TOPOLOGY_BOUNDARY_FILES if path.exists())

    for path in candidates:
        rel = str(path.relative_to(SDK_ROOT)).replace('\\', '/')
        text = path.read_text(encoding='utf-8', errors='ignore').lower()
        if any(term in text for term in FORBIDDEN_TOPOLOGY_TERMS):
            offenders.append(rel)
    assert offenders == []


def test_public_sdk_has_no_service_plane_imports() -> None:
    """Shipped SDK Python must not import any hosted service package."""
    offenders: list[str] = []
    for path in SOURCE_ROOT.rglob('*.py'):
        tree = ast.parse(path.read_text(encoding='utf-8'))
        for node in ast.walk(tree):
            roots: set[str] = set()
            if isinstance(node, ast.ImportFrom) and node.module:
                roots.add(node.module.split('.')[0])
            elif isinstance(node, ast.Import):
                roots.update(alias.name.split('.')[0] for alias in node.names)
            if roots & PROHIBITED_IMPORT_ROOTS:
                offenders.append(str(path.relative_to(SDK_ROOT)))
    assert offenders == []


def test_public_sdk_contains_no_concrete_model_markers() -> None:
    """SDK-owned source, tests, evals, and docs use tiers or synthetic model ids."""
    boundary_test = Path(__file__).resolve()
    candidates = [
        path
        for root in TEXT_BOUNDARY_ROOTS
        if root.exists()
        for path in root.rglob('*')
        if path.is_file()
        and path.resolve() != boundary_test
        and '.venv' not in path.parts
        and '__pycache__' not in path.parts
    ]
    candidates.extend(path for path in TEXT_BOUNDARY_FILES if path.exists())

    offenders: list[str] = []
    for path in candidates:
        text = path.read_text(encoding='utf-8', errors='ignore').lower()
        if any(marker in text for marker in PROHIBITED_MODEL_MARKERS):
            offenders.append(str(path.relative_to(SDK_ROOT)))
    assert offenders == []


def test_legacy_v1_tree_is_not_present() -> None:
    """The real v1 repository is the reference; v2 does not package a source copy."""
    legacy_root = SOURCE_ROOT / '_v1'
    assert not legacy_root.exists() or not any(path.is_file() for path in legacy_root.rglob('*'))


def test_run_options_default_to_auto_model_tier() -> None:
    """The SDK's default model choice is the public automatic tier."""
    assert RunOptions().model == 'auto'


def test_agent_and_swarm_default_to_auto_model_tier() -> None:
    """Developer scopes default to automatic service-side model resolution."""
    assert Agent(name='agent', api_key='synthetic-key').model == 'auto'
    assert Swarm(name='swarm').model == 'auto'


def test_exact_caller_model_override_is_serialized_without_sdk_resolution() -> None:
    """Compatibility overrides remain opaque SDK wire values."""
    payload = optional_run_options(RunOptions(model='synthetic-model-id'))
    assert payload['model'] == 'synthetic-model-id'
