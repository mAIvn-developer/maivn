"""Caller-owned private fields at Pydantic's model validation boundary."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import cast

from pydantic import BaseModel, GetCoreSchemaHandler, PydanticSchemaGenerationError, TypeAdapter
from pydantic.json_schema import GenerateJsonSchema, JsonSchemaValue
from pydantic_core import PydanticCustomError, core_schema

from maivn._internal.compat.decorators import (
    PRIVATE_DATA_DEPENDENCIES_ATTR,
    PrivateDataDependency,
)


def _dependencies(model: type[BaseModel]) -> tuple[PrivateDataDependency, ...]:
    values = getattr(model, PRIVATE_DATA_DEPENDENCIES_ATTR, ())
    return tuple(item for item in values if isinstance(item, PrivateDataDependency))


def _input_paths(model: type[BaseModel], name: str) -> list[list[str | int]]:
    field = model.model_fields[name]
    alias = field.validation_alias
    if isinstance(alias, str):
        return [[alias]]
    if alias is not None:
        paths = alias.convert_to_aliases()
        return cast('list[list[str | int]]', paths if isinstance(paths[0], list) else [paths])
    return [[field.alias or name]]


class PrivateInputJsonSchema(GenerateJsonSchema):
    """Hide private model fields in every definition without guessing reference topology."""

    def model_schema(self, schema: core_schema.ModelSchema) -> JsonSchemaValue:
        model = cast('type[BaseModel]', schema['cls'])
        result = super().model_schema(schema)
        hidden = {dependency.arg_name for dependency in _dependencies(model)} | {
            path[0]
            for dependency in _dependencies(model)
            for path in _input_paths(model, dependency.arg_name)
            if len(path) == 1
        }
        properties = result.get('properties', {})
        for name in hidden:
            properties.pop(name, None)
        if 'required' in result:
            result['required'] = [name for name in result['required'] if name not in hidden]
        return result


def _inject(model: type[BaseModel], value: object, private_data: Mapping[str, object]) -> object:
    dependencies = _dependencies(model)
    if not isinstance(value, (Mapping, model)):
        return value
    updates: dict[str, object] = {}
    for dependency in dependencies:
        if dependency.data_key not in private_data:
            error_code = 'private_data_missing'
            raise PydanticCustomError(
                error_code,
                'private data is missing key {key}',
                {'key': dependency.data_key},
            )
        updates[dependency.arg_name] = private_data[dependency.data_key]
    if isinstance(value, model):
        return value.model_copy(update=updates)
    copied = dict(cast('Mapping[str, object]', value))
    for name, private_value in updates.items():
        paths = _input_paths(model, name)
        # Use the highest-priority validation alias. Alternate supplied spellings
        # must not survive as hostile extras or shadow the caller-owned value.
        for path in paths:
            if len(path) == 1:
                copied.pop(str(path[0]), None)
        copied.pop(name, None)
        _set_path(copied, paths[0], private_value)
    return copied


def _set_path(target: dict[str, object], path: list[str | int], value: object) -> None:
    target.update(cast('dict[str, object]', _with_path(target, path, value)))


def _with_path(current: object, path: list[str | int], value: object) -> object:
    """Copy an alias path while keeping unrelated supplied values untouched."""
    if not path:
        return value
    segment, *remaining = path
    if isinstance(segment, str):
        mapping = (
            dict(cast('Mapping[str, object]', current)) if isinstance(current, Mapping) else {}
        )
        mapping[segment] = _with_path(mapping.get(segment), remaining, value)
        return mapping
    items = list(cast('list[object]', current)) if isinstance(current, (list, tuple)) else []
    required_length = segment + 1 if segment >= 0 else -segment
    while len(items) < required_length:
        items.append(None)
    items[segment] = _with_path(items[segment], remaining, value)
    return items


@dataclass(frozen=True)
class _PrivateModelContext:
    private_data: Mapping[str, object]


def install_private_model_validation(target: object) -> None:
    """Make an explicitly decorated model resolve its fields only during SDK calls."""
    if not isinstance(target, type) or not issubclass(target, BaseModel):
        return
    marker = '__maivn_private_validation__'
    if target.__dict__.get(marker):
        return
    previous = target.__get_pydantic_core_schema__
    default_hook = previous.__func__ is BaseModel.__get_pydantic_core_schema__.__func__

    def schema_hook(
        cls: type[BaseModel],
        source: type[BaseModel],
        handler: GetCoreSchemaHandler,
    ) -> core_schema.CoreSchema:
        schema = dict(handler(source) if default_hook else previous(source, handler))
        reference = schema.pop('ref', None)

        def inject(value: object, info: core_schema.ValidationInfo) -> object:
            context = info.context
            if not isinstance(context, _PrivateModelContext):
                return value
            return _inject(cls, value, context.private_data)

        return core_schema.with_info_before_validator_function(
            inject,
            cast('core_schema.CoreSchema', schema),
            ref=cast('str | None', reference),
        )

    setattr(target, '__get_pydantic_core_schema__', classmethod(schema_hook))  # noqa: B010 - dynamic class hook.
    setattr(target, marker, True)
    target.model_rebuild(force=True)


def validate_private_models(
    annotation: object,
    value: object,
    private_data: Mapping[str, object],
) -> object:
    """Pass caller-owned context through Pydantic's normal nested validation path."""
    try:
        adapter: TypeAdapter[object] = TypeAdapter(annotation)
    except PydanticSchemaGenerationError:
        # Opaque developer objects (for example injected clients) were never
        # Pydantic inputs and must keep their existing invocation behavior.
        return value

    def has_private_model(node: object) -> bool:
        if isinstance(node, list):
            return any(has_private_model(item) for item in cast('list[object]', node))
        if not isinstance(node, dict):
            return False
        schema_node = cast('dict[str, object]', node)
        model = schema_node.get('cls')
        if isinstance(model, type) and issubclass(model, BaseModel) and _dependencies(model):
            return True
        return any(has_private_model(item) for item in schema_node.values())

    if has_private_model(adapter.core_schema) or (
        isinstance(annotation, type) and issubclass(annotation, BaseModel)
    ):
        return adapter.validate_python(value, context=_PrivateModelContext(private_data))
    return value
