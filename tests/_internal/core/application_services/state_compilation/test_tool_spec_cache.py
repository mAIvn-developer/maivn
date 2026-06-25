# pyright: strict
"""Tests for content-hash caching of compiled tool specs."""

from __future__ import annotations

from collections.abc import Callable
from typing import cast
from unittest.mock import patch

import pytest
from maivn_shared import SessionRequest
from pydantic import BaseModel

from maivn._internal.core.application_services.state_compilation import state_compiler as sc_module
from maivn._internal.core.application_services.state_compilation.state_compiler import StateCompiler
from maivn._internal.core.application_services.state_compilation.tool_cache import (
    CompiledToolSpecCache,
    compilation_fingerprint,
)
from maivn._internal.core.entities.tools.base_tool import BaseTool
from maivn._internal.core.entities.tools.function_tool import FunctionTool
from maivn._internal.core.entities.tools.model_tool import ModelTool
from maivn._internal.core.tool_specs import ToolSpecFactory

# MARK: - Helpers


class _MinimalScope:
    id: str = "agent-test-id"


class _AddArgs(BaseModel):
    a: int
    b: int


class _AddArgsExtra(BaseModel):
    a: int
    b: int
    c: int


class _ParamV1(BaseModel):
    a: int


class _ParamV2(BaseModel):
    a: int
    b: int


def _model_tool(model: type[BaseModel], name: str = "calc") -> ModelTool:
    return ModelTool(model=model, name=name, description="d")


def _handler_with_param(annotation: type) -> Callable[..., object]:
    def handler(x: object) -> object:
        return x

    # Same qualname + signature shape, only the annotation's schema differs.
    handler.__annotations__ = {"x": annotation, "return": object}
    return handler


def _make_compiler() -> StateCompiler:
    return StateCompiler(tool_spec_factory=ToolSpecFactory())


def _read_attr(obj: object, name: str) -> object:
    """Read a protected attribute without tripping reportPrivateUsage."""
    return cast(object, getattr(obj, name))


def _always_none(*_args: object, **_kwargs: object) -> None:
    """Stand-in fingerprint that marks every compilation uncacheable."""
    return None


# MARK: - Fingerprint unit tests


def test_identical_model_tools_share_fingerprint() -> None:
    fp1 = compilation_fingerprint([_model_tool(_AddArgs)], "a")
    fp2 = compilation_fingerprint([_model_tool(_AddArgs)], "a")
    assert fp1 is not None
    assert fp1 == fp2


def test_changed_model_fields_change_fingerprint() -> None:
    fp1 = compilation_fingerprint([_model_tool(_AddArgs)], "a")
    fp2 = compilation_fingerprint([_model_tool(_AddArgsExtra)], "a")
    assert fp1 != fp2


def test_agent_id_changes_fingerprint() -> None:
    fp1 = compilation_fingerprint([_model_tool(_AddArgs)], "a")
    fp2 = compilation_fingerprint([_model_tool(_AddArgs)], "b")
    assert fp1 != fp2


def test_function_param_model_field_change_busts_fingerprint() -> None:
    """A function whose model-typed param gains a field must change the fingerprint.

    Signature *string* and tool_id are identical here; only the param model's
    schema differs, so this is the case name-based ids cannot detect.
    """
    t1 = FunctionTool(func=_handler_with_param(_ParamV1), name="h", description="d")
    t2 = FunctionTool(func=_handler_with_param(_ParamV2), name="h", description="d")
    assert t1.tool_id == t2.tool_id  # name-based id cannot tell them apart
    fp1 = compilation_fingerprint([t1], "a")
    fp2 = compilation_fingerprint([t2], "a")
    assert fp1 is not None
    assert fp1 != fp2


def test_unfingerprintable_tool_makes_compilation_uncacheable() -> None:
    bare = BaseTool(name="x", description="y")
    assert compilation_fingerprint([bare], "a") is None


# MARK: - LRU cache unit tests


def test_cache_eviction_is_lru() -> None:
    cache: CompiledToolSpecCache = CompiledToolSpecCache(max_entries=2)
    cache.put("k1", [])
    cache.put("k2", [])
    _ = cache.get("k1")  # touch k1 so k2 is now least-recently-used
    cache.put("k3", [])  # evicts k2
    assert cache.get("k1") is not None
    assert cache.get("k2") is None
    assert cache.get("k3") is not None


def test_cache_returns_copy_not_shared_list() -> None:
    cache: CompiledToolSpecCache = CompiledToolSpecCache()
    cache.put("k", [])
    first = cache.get("k")
    assert first is not None
    first.append(object())  # type: ignore[arg-type]
    second = cache.get("k")
    assert second == []  # mutation of the returned list must not poison the cache


# MARK: - StateCompiler integration tests


def test_identical_compilation_hits_cache() -> None:
    compiler = _make_compiler()
    scope = _MinimalScope()
    real = _read_attr(compiler, "_create_tool_specs_parallel")
    with patch.object(compiler, "_create_tool_specs_parallel", wraps=real) as spy:
        r1: SessionRequest = compiler.compile_state(
            messages=[], tools=[_model_tool(_AddArgs)], scope=scope
        )
        r2: SessionRequest = compiler.compile_state(
            messages=[], tools=[_model_tool(_AddArgs)], scope=scope
        )
    assert spy.call_count == 1
    assert [t.model_dump() for t in r1.tools] == [t.model_dump() for t in r2.tools]


def test_changed_tool_schema_busts_cache() -> None:
    compiler = _make_compiler()
    scope = _MinimalScope()
    real = _read_attr(compiler, "_create_tool_specs_parallel")
    with patch.object(compiler, "_create_tool_specs_parallel", wraps=real) as spy:
        compiler.compile_state(messages=[], tools=[_model_tool(_AddArgs)], scope=scope)
        compiler.compile_state(messages=[], tools=[_model_tool(_AddArgsExtra)], scope=scope)
    assert spy.call_count == 2


def test_cache_disabled_via_env_recompiles(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MAIVN_DISABLE_TOOLSPEC_CACHE", "1")
    compiler = _make_compiler()
    scope = _MinimalScope()
    real = _read_attr(compiler, "_create_tool_specs_parallel")
    with patch.object(compiler, "_create_tool_specs_parallel", wraps=real) as spy:
        compiler.compile_state(messages=[], tools=[_model_tool(_AddArgs)], scope=scope)
        compiler.compile_state(messages=[], tools=[_model_tool(_AddArgs)], scope=scope)
    assert spy.call_count == 2


def test_uncacheable_fingerprint_bypasses_cache(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(sc_module, "compilation_fingerprint", _always_none)
    compiler = _make_compiler()
    scope = _MinimalScope()
    real = _read_attr(compiler, "_create_tool_specs_parallel")
    with patch.object(compiler, "_create_tool_specs_parallel", wraps=real) as spy:
        compiler.compile_state(messages=[], tools=[_model_tool(_AddArgs)], scope=scope)
        compiler.compile_state(messages=[], tools=[_model_tool(_AddArgs)], scope=scope)
    assert spy.call_count == 2
