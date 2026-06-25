# pyright: strict
"""Tests for model selection threading through StateCompiler.compile_state."""

from __future__ import annotations

import pytest
from maivn_shared import ModelChoice, ModelConfig, SessionRequest

from maivn._internal.core.application_services.state_compilation.state_compiler import (
    StateCompiler,
)
from maivn._internal.core.tool_specs import ToolSpecFactory

# MARK: - Helpers


class _MinimalScope:
    """Minimal scope required by StateCompiler."""

    id: str = "agent-test-id"


def _make_compiler() -> StateCompiler:
    return StateCompiler(tool_spec_factory=ToolSpecFactory())


# MARK: - Tests


def test_state_compiler_accepts_model_auto() -> None:
    """model='auto' must be accepted and land on SessionRequest.model."""
    compiler = _make_compiler()
    scope = _MinimalScope()

    result: SessionRequest = compiler.compile_state(
        messages=[],
        tools=[],
        scope=scope,
        model="auto",
    )

    assert result.model == "auto"


def test_state_compiler_accepts_model_config() -> None:
    """ModelConfig must be accepted and land on SessionRequest.model."""
    compiler = _make_compiler()
    scope = _MinimalScope()
    model_config = ModelConfig(
        response=ModelChoice(tier="balanced"),
        thinking=ModelChoice(model_id="claude-opus-4-5"),
    )

    result: SessionRequest = compiler.compile_state(
        messages=[],
        tools=[],
        scope=scope,
        model=model_config,
    )

    assert result.model == model_config


def test_state_compiler_threads_force_model() -> None:
    """force_model must be threaded to SessionRequest.force_model."""
    compiler = _make_compiler()
    scope = _MinimalScope()

    with pytest.warns(DeprecationWarning, match="force_model is deprecated"):
        result: SessionRequest = compiler.compile_state(
            messages=[],
            tools=[],
            scope=scope,
            force_model="gpt-5.4",
        )

    assert result.force_model == "gpt-5.4"


def test_state_compiler_rejects_model_config_with_force_model() -> None:
    """ModelConfig replaces force_model and must not be combined with it."""
    compiler = _make_compiler()
    scope = _MinimalScope()
    model_config = ModelConfig(response=ModelChoice(tier="max"))

    with pytest.raises(ValueError, match="force_model cannot be combined with ModelConfig"):
        _ = compiler.compile_state(
            messages=[],
            tools=[],
            scope=scope,
            model=model_config,
            force_model="claude-opus-5",
        )


def test_state_compiler_force_model_defaults_to_none() -> None:
    """force_model must default to None when omitted."""
    compiler = _make_compiler()
    scope = _MinimalScope()

    result: SessionRequest = compiler.compile_state(
        messages=[],
        tools=[],
        scope=scope,
    )

    assert result.force_model is None
