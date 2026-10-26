"""Input and output JSON schemas derived from a tool's signature and dependency metadata.

Split out of tooling.py, which re-exports preserve_public_tool_dependency_guidance.
"""

from __future__ import annotations

from inspect import Parameter, Signature, signature
from typing import TYPE_CHECKING, Final, cast, get_type_hints

from maivn_contracts.artifacts import GeneratedFile, GeneratedFiles
from pydantic import BaseModel, PydanticUserError, TypeAdapter

from maivn._internal.compat.decorators import (
    INTERRUPT_DEPENDENCIES_ATTR,
    OUTPUT_SCHEMA_ATTR,
    PRIVATE_DATA_DEPENDENCIES_ATTR,
    TOOL_DEPENDENCIES_ATTR,
    ToolDependency,
)
from maivn._internal.decisions.dependencies import (
    DECISION_DEPENDENCIES_ATTR,
    validate_decision_dependencies,
)
from maivn._internal.private_model_inputs import PrivateInputJsonSchema
from maivn._internal.schema_references import prune_unused_definitions

if TYPE_CHECKING:
    from collections.abc import Callable, Mapping

    from maivn._internal.compat.tooling import ToolTarget
    from maivn._internal.models import JsonObject

SCHEMA_FALLBACK_ERRORS: Final = (TypeError, ValueError, PydanticUserError)
"""Errors after which an annotation's schema falls back to a generic one.

``PydanticUserError`` covers an annotation pydantic cannot describe at all: a class it
has no schema for, or a forward reference (a ``TYPE_CHECKING``-only import) it cannot
resolve.
"""


def input_schema_for(func: ToolTarget) -> JsonObject:
    validate_decision_dependencies(func)
    if isinstance(func, type) and issubclass(func, BaseModel):
        schema = func.model_json_schema(schema_generator=PrivateInputJsonSchema)
        dependency_args = _dependency_arg_names(func)
        model_properties = schema.get('properties')
        if isinstance(model_properties, dict):
            typed_properties = cast('Mapping[str, object]', model_properties)
            schema['properties'] = {
                name: value
                for name, value in typed_properties.items()
                if name not in dependency_args
            }
        model_required = schema.get('required')
        if isinstance(model_required, list):
            schema['required'] = [
                name
                for name in cast('list[object]', model_required)
                if isinstance(name, str) and name not in dependency_args
            ]
        preserve_public_tool_dependency_guidance(func, schema)
        return prune_unused_definitions(schema)
    try:
        func_signature = signature(cast('Callable[..., object]', func))
    except (TypeError, ValueError):
        return {'type': 'object', 'properties': {}}

    callable_func = cast('Callable[..., object]', func)
    dependency_args = _dependency_arg_names(callable_func)
    resolved_hints = _resolved_type_hints(callable_func)
    properties: dict[str, object] = {}
    definition_adapters: dict[str, TypeAdapter[object]] = {}
    required: list[str] = []
    for name, parameter in func_signature.parameters.items():
        if (
            name == 'self'
            or name in dependency_args
            or parameter.kind in (Parameter.VAR_KEYWORD, Parameter.VAR_POSITIONAL)
        ):
            continue
        properties[name] = _schema_for_parameter(
            parameter,
            resolved_hints.get(name),
            definition_adapters=definition_adapters,
        )
        if parameter.default is Signature.empty:
            required.append(name)

    schema: JsonObject = {
        'type': 'object',
        'properties': properties,
        'additionalProperties': False,
    }
    if required:
        schema['required'] = required
    if definition_adapters:
        # Generate these parameters together: Pydantic resolves recursive links
        # and distinguishes different models that happen to share a name.
        # Individual parameter schemas use root refs, so nesting their $defs
        # beneath properties would leave those references dangling.
        parameter_schemas, definitions = TypeAdapter.json_schemas(
            [(name, 'validation', adapter) for name, adapter in definition_adapters.items()],
            schema_generator=PrivateInputJsonSchema,
        )
        properties.update({name: value for (name, _mode), value in parameter_schemas.items()})
        schema.update(definitions)
    preserve_public_tool_dependency_guidance(callable_func, schema)
    return schema


def output_schema_for(target: ToolTarget) -> object | None:
    raw = cast('object | None', getattr(target, OUTPUT_SCHEMA_ATTR, None))
    if raw is None:
        try:
            annotation = get_type_hints(target).get('return')
        except (TypeError, NameError):
            annotation = None
        if annotation in (GeneratedFile, GeneratedFiles):
            raw = annotation
    if isinstance(raw, type) and issubclass(raw, BaseModel):
        return raw.model_json_schema()
    return cast('object | None', raw)


def _schema_for_parameter(
    parameter: Parameter,
    resolved_annotation: object | None,
    *,
    definition_adapters: dict[str, TypeAdapter[object]],
) -> JsonObject:
    annotation = resolved_annotation or parameter.annotation
    if annotation is not Signature.empty:
        try:
            adapter: TypeAdapter[object] = TypeAdapter(annotation)
            schema = adapter.json_schema()
        except SCHEMA_FALLBACK_ERRORS:
            pass
        else:
            if '$defs' in schema:
                definition_adapters[parameter.name] = adapter
            return schema
    schema_by_annotation: dict[object, JsonObject] = {
        bool: {'type': 'boolean'},
        int: {'type': 'integer'},
        float: {'type': 'number'},
        str: {'type': 'string'},
        list: {'type': 'array'},
        tuple: {'type': 'array'},
        dict: {'type': 'object'},
    }
    return schema_by_annotation.get(annotation, {'description': parameter.name})


def _resolved_type_hints(func: Callable[..., object]) -> dict[str, object]:
    try:
        return cast('dict[str, object]', get_type_hints(func, include_extras=True))
    except Exception:  # noqa: BLE001 - third-party annotations may not resolve locally.
        return {}


def _dependency_arg_names(func: object) -> frozenset[str]:
    # Every dependency kind fills its argument from outside the model, so none of them may
    # appear in the model-facing schema. Interrupt args were missing from this list, so the
    # model still saw an argument only a human could supply - and looped trying to invent it.
    dependencies: list[object] = []
    for attr_name in (
        PRIVATE_DATA_DEPENDENCIES_ATTR,
        TOOL_DEPENDENCIES_ATTR,
        INTERRUPT_DEPENDENCIES_ATTR,
        DECISION_DEPENDENCIES_ATTR,
    ):
        raw = getattr(func, attr_name, ())
        if isinstance(raw, list):
            dependencies.extend(cast('list[object]', raw))
    return frozenset(
        arg_name
        for dependency in dependencies
        if isinstance((arg_name := getattr(dependency, 'arg_name', None)), str)
    )


def preserve_public_tool_dependency_guidance(
    target: ToolTarget,
    schema: JsonObject,
) -> None:
    """Retain public prerequisites and field guidance after bound inputs are hidden.

    Dependency values are runtime-bound, so their properties cannot remain provider
    inputs. Their explicit descriptions may still tell a model how to use the public
    result. Private-data dependencies never contribute guidance here.
    """
    private_args = {
        arg_name
        for dependency in _metadata_values(target, PRIVATE_DATA_DEPENDENCIES_ATTR)
        if isinstance(arg_name := getattr(dependency, 'arg_name', None), str)
    }
    guidance: list[str] = []
    dependencies = _metadata_values(target, TOOL_DEPENDENCIES_ATTR)
    model_fields = (
        target.model_fields if isinstance(target, type) and issubclass(target, BaseModel) else {}
    )
    for dependency in dependencies:
        if not isinstance(dependency, ToolDependency) or dependency.arg_name in private_args:
            continue
        guidance.append(
            f'- {dependency.arg_name}: supplied by {dependency.tool_name}; '
            'this tool can execute only after that producer succeeds. '
            'The runtime supplies its result; do not copy it into arguments.'
        )
        field_info = model_fields.get(dependency.arg_name)
        description = None if field_info is None else field_info.description
        if isinstance(description, str) and description.strip():
            guidance.append(f'- {dependency.arg_name}: {description.strip()}')
    if not guidance:
        return
    marker = 'Public dependency guidance:'
    existing = schema.get('description')
    # The schema is freshly generated on each compilation. Authored prose that
    # happens to contain our heading must not suppress dependency descriptions.
    prefix = existing.strip() if isinstance(existing, str) else ''
    parts = (prefix, marker, '\n'.join(dict.fromkeys(guidance)))
    schema['description'] = '\n\n'.join(part for part in parts if part)


def _metadata_values(target: object, attr_name: str) -> list[object]:
    """Return owned decorator metadata without treating arbitrary values as records."""
    raw = getattr(target, attr_name, ())
    return list(cast('list[object]', raw)) if isinstance(raw, list) else []


__all__ = [
    '_dependency_arg_names',
    '_metadata_values',
    '_resolved_type_hints',
    '_schema_for_parameter',
    'input_schema_for',
    'output_schema_for',
    'preserve_public_tool_dependency_guidance',
]
