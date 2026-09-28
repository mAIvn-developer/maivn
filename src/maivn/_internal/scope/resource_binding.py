"""Upload and bind the resources a scope declares, exactly once per scope."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Protocol, cast

from maivn._internal.errors import ResourceRegistrationError
from maivn._internal.resources import ResourceUploadOptions

if TYPE_CHECKING:
    from maivn._internal.client import Client
    from maivn._internal.scope.types import ResourceSpec, ScopeResourceBindingType


@dataclass(frozen=True, slots=True)
class _ScopeResourceDeclaration:
    content: bytes
    name: str
    media_type: str
    tags: tuple[str, ...]
    description: str | None
    binding_type: ScopeResourceBindingType | None


class _ResourceScope(Protocol):
    resources: list[ResourceSpec]
    _registered_resource_ids: list[str] | None

    @property
    def _client(self) -> Client:
        """Return the client used to register the scope's resources."""
        ...


async def aregister_scope_resources_once(
    scope: _ResourceScope,
    *,
    scope_name: str,
    scope_binding_type: ScopeResourceBindingType,
) -> None:
    """Register and bind a scope's declared resources exactly once.

    Uses the same dynamic-attribute shim as the tool cache: Agent and Swarm are
    independent dataclasses, so shared scope state is reached by name rather than
    through a common base they do not have.
    """
    if getattr(scope, '_registered_resource_ids', None) is not None:
        return
    # Registration is awaited inline rather than cached as a Task. A Task belongs to the
    # loop that created it, and a scope legitimately outlives one loop - a batch run drives
    # several - so a cached Task is awaited from a loop it does not belong to and raises
    # "attached to a different loop".
    #
    # Nothing is lost by dropping the cache: registration is idempotent at the server, which
    # deduplicates on the content hash within a tenant. Two callers racing here register the
    # same bytes and the second one resolves to the first one's resource.
    resource_ids = await _aregister_scope_resources(
        cast('Client', getattr(scope, '_client', None)),
        scope.resources,
        scope_name=scope_name,
        scope_binding_type=scope_binding_type,
    )
    setattr(scope, '_registered_resource_ids', resource_ids)  # noqa: B010 - dynamic scope shim.


async def _aregister_scope_resources(
    client: Client,
    resources: Sequence[ResourceSpec],
    *,
    scope_name: str,
    scope_binding_type: ScopeResourceBindingType,
) -> list[str]:
    """Upload and bind the normalized resources declared by one scope."""
    declarations = [
        _scope_resource_declaration(
            spec,
            index=index,
            scope_binding_type=scope_binding_type,
        )
        for index, spec in enumerate(resources)
    ]
    resource_ids: list[str] = []
    for declaration in declarations:
        try:
            resource = await client.resources.aupload(
                declaration.content,
                media_type=declaration.media_type,
                name=declaration.name,
                options=ResourceUploadOptions(
                    description=declaration.description,
                    tags=declaration.tags,
                ),
            )
            if declaration.binding_type is not None:
                await client.resources.abind(
                    resource.resource_id,
                    binding_type=declaration.binding_type,
                    target_id=scope_name,
                )
        except ResourceRegistrationError:
            raise
        except Exception as exc:
            message = (
                f"failed to register resource '{declaration.name}' for "
                f"{scope_binding_type} '{scope_name}'"
            )
            raise ResourceRegistrationError(message) from exc
        resource_ids.append(resource.resource_id)
    return resource_ids


def _scope_resource_declaration(
    spec: ResourceSpec,
    *,
    index: int,
    scope_binding_type: ScopeResourceBindingType,
) -> _ScopeResourceDeclaration:
    """Validate one normalized scope resource declaration for registration."""
    text_content = spec.get('text_content')
    if isinstance(text_content, str) and text_content:
        content = text_content.encode('utf-8')
    else:
        file_value = spec.get('file')
        if not isinstance(file_value, str | Path):
            message = f'resources[{index}].text_content must be a non-empty string'
            raise ResourceRegistrationError(message)
        try:
            content = Path(file_value).read_bytes()
        except OSError as exc:
            message = f'resources[{index}].file must reference a readable non-empty file'
            raise ResourceRegistrationError(message) from exc
        if not content:
            message = f'resources[{index}].file must reference a readable non-empty file'
            raise ResourceRegistrationError(message)
    name = _required_resource_string(spec, 'name', index=index)
    media_type = _required_resource_string(spec, 'mime_type', index=index)
    description = spec.get('description')
    if description is not None and not isinstance(description, str):
        message = f'resources[{index}].description must be a string when provided'
        raise ResourceRegistrationError(message)
    tags = _resource_tags(spec, index=index)
    binding_type = _resource_binding_type(
        spec,
        index=index,
        scope_binding_type=scope_binding_type,
    )
    return _ScopeResourceDeclaration(
        content=content,
        name=name,
        media_type=media_type,
        tags=tags,
        description=description,
        binding_type=binding_type,
    )


def _required_resource_string(spec: ResourceSpec, key: str, *, index: int) -> str:
    """Return a non-blank declared resource string field."""
    value = spec.get(key)
    if not isinstance(value, str) or not value.strip():
        message = f'resources[{index}].{key} must be a non-empty string'
        raise ResourceRegistrationError(message)
    return value


def _resource_tags(spec: ResourceSpec, *, index: int) -> tuple[str, ...]:
    """Validate declared resource tags before uploading them."""
    value = spec.get('tags', ())
    if not isinstance(value, Sequence) or isinstance(value, str | bytes):
        message = f'resources[{index}].tags must be a list of strings when provided'
        raise ResourceRegistrationError(message)
    tags: list[str] = []
    for tag_index, tag in enumerate(cast('Sequence[object]', value)):
        if not isinstance(tag, str) or not tag.strip():
            message = f'resources[{index}].tags[{tag_index}] must be a non-empty string'
            raise ResourceRegistrationError(message)
        tags.append(tag)
    return tuple(tags)


def _resource_binding_type(
    spec: ResourceSpec,
    *,
    index: int,
    scope_binding_type: ScopeResourceBindingType,
) -> ScopeResourceBindingType | None:
    """Return the requested binding type when it belongs to the declaring scope."""
    value = spec.get('binding_type')
    if value is None:
        return None
    if value not in {'agent', 'swarm'}:
        message = f'resources[{index}].binding_type must be agent or swarm when provided'
        raise ResourceRegistrationError(message)
    binding_type = cast('ScopeResourceBindingType', value)
    if binding_type != scope_binding_type:
        message = f"resources[{index}].binding_type must be '{scope_binding_type}' for this scope"
        raise ResourceRegistrationError(message)
    return binding_type
