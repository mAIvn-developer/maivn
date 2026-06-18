"""Output-schema contract helpers for SDK tools."""

# pyright: strict
from __future__ import annotations

from collections.abc import Mapping
from typing import Final, TypeAlias, TypeGuard, cast

from pydantic import BaseModel, JsonValue

# MARK: Types

JsonObject: TypeAlias = dict[str, JsonValue]
OutputSchemaInput: TypeAlias = Mapping[str, object] | type[BaseModel]

# MARK: Constants

TOOL_OUTPUT_SCHEMA_ATTR: Final[str] = "__maivn_output_schema__"


# MARK: Public Helpers


def normalize_output_schema(
    schema: OutputSchemaInput,
    *,
    context: str = "output_schema",
) -> JsonObject:
    """Normalize a Pydantic model or JSON schema mapping into a JSON object."""
    if _is_pydantic_model_class(schema):
        return cast(JsonObject, schema.model_json_schema())

    if isinstance(schema, Mapping):
        return cast(JsonObject, dict(schema))

    raise TypeError(f"{context} must be a Pydantic BaseModel class or JSON schema dict.")


def attach_output_schema(target: object, schema: OutputSchemaInput) -> None:
    """Attach a normalized output schema contract to a callable target."""
    setattr(target, TOOL_OUTPUT_SCHEMA_ATTR, normalize_output_schema(schema))


def collect_output_schema(target: object) -> JsonObject | None:
    """Collect an attached output schema from a callable or bound method."""
    for candidate in _schema_candidates(target):
        raw_schema = getattr(candidate, TOOL_OUTPUT_SCHEMA_ATTR, None)
        if raw_schema is not None:
            return normalize_output_schema(
                cast(OutputSchemaInput, raw_schema),
                context="attached output_schema",
            )
    return None


# MARK: Private Helpers


def _schema_candidates(target: object) -> list[object]:
    candidates = [target]
    underlying = getattr(target, "__func__", None)
    if underlying is not None and underlying is not target:
        candidates.append(underlying)
    return candidates


def _is_pydantic_model_class(value: object) -> TypeGuard[type[BaseModel]]:
    return isinstance(value, type) and issubclass(value, BaseModel)


__all__ = [
    "JsonObject",
    "OutputSchemaInput",
    "attach_output_schema",
    "collect_output_schema",
    "normalize_output_schema",
]
