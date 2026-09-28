"""Normalization and coercion of v1-shaped scope constructor input."""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from typing import TYPE_CHECKING, cast

from pydantic import BaseModel

from maivn._internal.compat.mcp import MCPServer
from maivn._internal.compat.options import (
    MemoryConfig,
    SessionOrchestrationConfig,
    SystemToolsConfig,
)
from maivn._internal.compat.privacy import PrivateData
from maivn._internal.compat.tooling import ToolRegistrationOptions, tool_metadata_for_callable
from maivn._internal.models import ToolMetadata
from maivn._internal.skills import Skill

if TYPE_CHECKING:
    from maivn._internal.scope.types import ResourceSpec, ToolSpec


def register_initial_tools(tools: Sequence[object]) -> list[ToolSpec]:
    """Normalize constructor-provided v1 tools through the same add_tool path."""
    normalized: list[ToolSpec] = []
    for item in tools:
        if isinstance(item, ToolMetadata):
            normalized.append(item)
            continue
        if isinstance(item, type) and issubclass(item, BaseModel):
            normalized.append(
                tool_metadata_for_callable(
                    item,
                    ToolRegistrationOptions(),
                )
            )
            continue
        if callable(cast('object', item)):
            normalized.append(
                tool_metadata_for_callable(
                    cast('Callable[..., object]', item),
                    ToolRegistrationOptions(),
                )
            )
            continue
        message = 'tools must contain ToolMetadata or callable entries'
        raise TypeError(message)
    return normalized


def normalize_skill_specs(value: object) -> list[Skill]:
    """Normalize v1 dict skills into validated v2 Skill objects."""
    if value is None:
        return []
    if not isinstance(value, Sequence) or isinstance(value, str | bytes):
        message = 'skills must be a list of dictionaries, Skill objects, or None'
        raise TypeError(message)

    items = cast('Sequence[object]', value)
    normalized: list[Skill] = []
    for index, item in enumerate(items):
        if isinstance(item, Skill):
            normalized.append(item)
            continue
        if isinstance(item, Mapping):
            normalized.append(_skill_from_mapping(cast('Mapping[str, object]', item)))
            continue
        message = f'skills[{index}] must be a dictionary or Skill'
        raise TypeError(message)
    return normalized


def _skill_from_mapping(item: Mapping[str, object]) -> Skill:
    payload = dict(item)
    skill_id = payload.pop('skill_id', None)
    if skill_id is not None and payload.get('memory_id') is None:
        payload['memory_id'] = str(skill_id)
    legacy_id = payload.pop('id', None)
    if legacy_id is not None and payload.get('memory_id') is None:
        payload['memory_id'] = str(legacy_id)
    return Skill.model_validate(payload)


def normalize_resource_specs(value: object) -> list[ResourceSpec]:
    """Normalize v1 resource binding dictionaries."""
    if value is None:
        return []
    if not isinstance(value, Sequence) or isinstance(value, str | bytes):
        message = 'resources must be a list of dictionaries or None'
        raise TypeError(message)
    items = cast('Sequence[object]', value)
    normalized: list[ResourceSpec] = []
    for index, item in enumerate(items):
        if not isinstance(item, Mapping):
            message = f'resources[{index}] must be a dictionary'
            raise TypeError(message)
        normalized.append(dict(cast('Mapping[str, object]', item)))
    return normalized


def normalize_string_list(value: object, field_name: str) -> list[str]:
    """Normalize v1 string-list fields."""
    if value is None:
        return []
    if not isinstance(value, Sequence) or isinstance(value, str | bytes):
        message = f'{field_name} must be a list of strings or None'
        raise TypeError(message)
    items = cast('Sequence[object]', value)
    return [str(item) for item in items]


def coerce_memory_config(value: object) -> MemoryConfig:
    """Coerce v1 memory_config constructor input into the compat model."""
    if value is None:
        return MemoryConfig()
    if isinstance(value, MemoryConfig):
        return value
    if isinstance(value, Mapping):
        payload = dict(cast('Mapping[str, object]', value))
        return MemoryConfig.model_validate(payload)
    message = 'memory_config must be a MemoryConfig, dictionary, or None'
    raise TypeError(message)


def coerce_optional_memory_config(value: object) -> MemoryConfig | None:
    """Coerce optional memory overrides."""
    if value is None:
        return None
    return coerce_memory_config(value)


def coerce_system_tools_config(value: object) -> SystemToolsConfig:
    """Coerce v1 system_tools_config constructor input into the compat model."""
    if value is None:
        return SystemToolsConfig()
    if isinstance(value, SystemToolsConfig):
        return value
    if isinstance(value, Mapping):
        payload = dict(cast('Mapping[str, object]', value))
        return SystemToolsConfig.model_validate(payload)
    message = 'system_tools_config must be a SystemToolsConfig, dictionary, or None'
    raise TypeError(message)


def coerce_orchestration_config(value: object) -> SessionOrchestrationConfig:
    """Coerce v1 orchestration_config constructor input into the compat model."""
    if value is None:
        return SessionOrchestrationConfig()
    if isinstance(value, SessionOrchestrationConfig):
        return value
    if isinstance(value, Mapping):
        payload = dict(cast('Mapping[str, object]', value))
        return SessionOrchestrationConfig.model_validate(payload)
    message = 'orchestration_config must be a SessionOrchestrationConfig, dictionary, or None'
    raise TypeError(message)


def normalize_private_data(
    value: object,
) -> dict[object, object]:
    """Normalize v1 private_data dict/list inputs into a key-value mapping."""
    if value is None:
        return {}
    if isinstance(value, Mapping):
        return dict(cast('Mapping[object, object]', value))
    if isinstance(value, Sequence) and not isinstance(value, str | bytes):
        return _private_data_list_to_dict(cast('Sequence[object]', value))
    message = 'private_data must be a dictionary, list of PrivateData, or None'
    raise TypeError(message)


def private_data_mapping(
    value: Mapping[object, object] | Sequence[PrivateData | Mapping[str, object]] | None,
) -> Mapping[object, object] | None:
    """Narrow constructor-compatible private data after scope normalization."""
    if value is None or isinstance(value, Mapping):
        return value
    message = 'scope private_data was not normalized during initialization'
    raise TypeError(message)


def _private_data_list_to_dict(items: Sequence[object]) -> dict[object, object]:
    """Convert a v1 list of PrivateData objects or dicts to a key-value dict."""
    result: dict[object, object] = {}
    counter = 0
    for item in items:
        if isinstance(item, PrivateData):
            private_data = item
        elif isinstance(item, Mapping) and 'value' in item:
            private_data = PrivateData.model_validate(dict(cast('Mapping[str, object]', item)))
        else:
            message = 'private_data list entries must be PrivateData objects or value dicts'
            raise TypeError(message)
        key = private_data.name
        if not key:
            counter += 1
            key = f'_private_{counter}'
        elif key in result:
            message = f'duplicate private_data name: {key}'
            raise ValueError(message)
        result[key] = private_data.value
    return result


def normalize_mcp_servers(servers: object) -> list[MCPServer]:
    """Normalize v1 MCP server registration input."""
    if isinstance(servers, MCPServer):
        return [servers]
    if isinstance(servers, Sequence) and not isinstance(servers, str | bytes):
        items = cast('Sequence[object]', servers)
        normalized: list[MCPServer] = []
        for server in items:
            if not isinstance(server, MCPServer):
                message = 'register_mcp_servers expects MCPServer instances'
                raise TypeError(message)
            normalized.append(server)
        return normalized
    message = 'register_mcp_servers expects an MCPServer or sequence of MCPServer'
    raise TypeError(message)
