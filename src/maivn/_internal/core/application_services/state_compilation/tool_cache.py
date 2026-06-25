# pyright: strict
"""Content-addressed caching of compiled tool specs.

The compiled tool-spec list for an agent is a pure function of its tools'
*schemas* plus the agent id. A tool's ``tool_id`` is name-based (``module.qualname``)
and does NOT change when a model's fields change, so it cannot drive cache
invalidation. We fingerprint the resolved *schema content* instead — including
the JSON schema of every type hint on a function tool, so a function whose
Pydantic-model parameter gains a field busts the cache even though its signature
string is unchanged.

Bias is toward false MISSES (a harmless recompute), never false HITS (a
correctness bug): any tool whose schema cannot be confidently fingerprinted makes
the whole compilation uncacheable (``compilation_fingerprint`` returns ``None``).
"""

from __future__ import annotations

import hashlib
import inspect
import os
import typing
from collections import OrderedDict
from collections.abc import Iterable
from typing import TYPE_CHECKING

import orjson
from pydantic import BaseModel, TypeAdapter

if TYPE_CHECKING:
    from maivn_shared import ToolSpec

    from maivn._internal.core.entities import BaseTool

# MARK: - Constants

_FIELD_SEP = "\x1e"
_TOOL_SEP = "\x1f"
_DISABLE_ENV = "MAIVN_DISABLE_TOOLSPEC_CACHE"
_DEFAULT_MAX_ENTRIES = 256
_TRUTHY = {"1", "true", "yes", "on"}


# MARK: - Public API


def toolspec_cache_enabled() -> bool:
    """Whether the compiled-tool-spec cache is active (disable via env)."""
    return os.environ.get(_DISABLE_ENV, "").strip().lower() not in _TRUTHY


def compilation_fingerprint(tools: Iterable[BaseTool], agent_id: str) -> str | None:
    """Content fingerprint for a whole compilation, or None if uncacheable.

    Returns None as soon as any tool cannot be confidently fingerprinted, so the
    caller falls back to a full (uncached) recompilation.
    """
    parts: list[str] = [f"agent:{agent_id}"]
    for tool in tools:
        fingerprint = _tool_fingerprint(tool)
        if fingerprint is None:
            return None
        parts.append(fingerprint)
    return hashlib.sha256(_TOOL_SEP.join(parts).encode("utf-8")).hexdigest()


# MARK: - Cache


class CompiledToolSpecCache:
    """Bounded LRU cache of compiled tool-spec lists keyed by content fingerprint."""

    def __init__(self, max_entries: int = _DEFAULT_MAX_ENTRIES) -> None:
        self._max_entries: int = max_entries
        self._entries: OrderedDict[str, list[ToolSpec]] = OrderedDict()

    def get(self, key: str) -> list[ToolSpec] | None:
        specs = self._entries.get(key)
        if specs is None:
            return None
        self._entries.move_to_end(key)
        # Shallow copy: callers must treat the cached ToolSpecs as immutable.
        return list(specs)

    def put(self, key: str, specs: list[ToolSpec]) -> None:
        self._entries[key] = list(specs)
        self._entries.move_to_end(key)
        while len(self._entries) > self._max_entries:
            self._entries.popitem(last=False)


# MARK: - Internal helpers


def _stable_json(obj: object) -> str:
    """Canonical JSON (sorted keys) for hashing; tolerant of stray objects."""
    return orjson.dumps(obj, option=orjson.OPT_SORT_KEYS, default=str).decode("utf-8")


def _tool_fingerprint(tool: BaseTool) -> str | None:
    """Content fingerprint for one tool, or None if it cannot be fingerprinted."""
    model = getattr(tool, "model", None)
    func = getattr(tool, "func", None)

    if isinstance(model, type) and issubclass(model, BaseModel):
        source = f"model:{_stable_json(model.model_json_schema())}"
    elif callable(func):
        function_source = _function_source(func)
        if function_source is None:
            return None
        source = function_source
    else:
        # agent / mcp / unknown tool types -> uncacheable (bias to false miss).
        return None

    common = [
        str(getattr(tool, "tool_type", "")),
        tool.name,
        tool.description,
        str(tool.final_tool),
        str(tool.always_execute),
        _stable_json(tool.output_schema),
        "|".join(sorted(repr(dependency) for dependency in tool.dependencies)),
        source,
    ]
    return _FIELD_SEP.join(common)


def _function_source(func: object) -> str | None:
    """Schema-resolved fingerprint for a function tool, or None if unresolvable."""
    try:
        hints = typing.get_type_hints(func)
    except Exception:  # noqa: BLE001 - unresolved annotations -> uncacheable.
        return None

    qualname = getattr(func, "__qualname__", None) or getattr(func, "__name__", "")
    doc = inspect.getdoc(func) or ""
    parts: list[str] = [f"func:{qualname}", doc]
    for name in sorted(hints):
        try:
            schema = _stable_json(TypeAdapter(hints[name]).json_schema())
        except Exception:  # noqa: BLE001 - unschemable hint -> uncacheable.
            return None
        parts.append(f"{name}={schema}")
    return _FIELD_SEP.join(parts)


__all__ = [
    "CompiledToolSpecCache",
    "compilation_fingerprint",
    "toolspec_cache_enabled",
]
