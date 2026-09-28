"""Typed SDK client surface for resource routes."""

from __future__ import annotations

import base64
from typing import TYPE_CHECKING, Any, Literal, TypeAlias

from pydantic import BaseModel, ConfigDict, Field, StrictBool, StrictInt, StrictStr

from maivn._internal.api.async_stream import run_blocking
from maivn._internal.memory import MemoryScope
from maivn._internal.wire import (
    RESOURCE_BIND_PATH,
    RESOURCE_PATH,
    RESOURCE_REBIND_PATH,
    RESOURCE_RESTORE_PATH,
    RESOURCES_PATH,
)

if TYPE_CHECKING:
    from collections.abc import Callable

    from maivn._internal.transport.http import HttpJsonClient

JsonObject: TypeAlias = dict[str, Any]
ResourceBindingType: TypeAlias = Literal['portal', 'agent', 'swarm']
ResourceStatus: TypeAlias = Literal['registered', 'superseded', 'deleted', 'error']
ResourceStatusFilter: TypeAlias = Literal['registered', 'superseded', 'deleted', 'error', 'all']
ProcessingStatus: TypeAlias = Literal[
    'pending',
    'processing',
    'processed',
    'needs_ocr',
    'ready',
    'failed',
    'quarantined',
    'redaction_failed',
]
ProcessingStatusFilter: TypeAlias = Literal[
    'pending',
    'processing',
    'ready',
    'failed',
    'quarantined',
    'redaction_failed',
    'all',
]
PortalScope: TypeAlias = Literal['project', 'org']
BoundResourceType: TypeAlias = Literal['agent', 'swarm']


class ResourceScopeRequest(BaseModel):
    """Request scope for uploading or updating portal resources."""

    model_config = ConfigDict(extra='forbid', frozen=True)

    project_id: StrictStr | None = Field(default=None, min_length=1)
    organization_id: StrictStr | None = Field(default=None, min_length=1)
    sharing_scope: PortalScope = 'project'


class _ResourceUpload(BaseModel):
    """Internal upload request body for resource registration."""

    model_config = ConfigDict(extra='forbid', frozen=True)

    resource_id: StrictStr | None = Field(default=None, min_length=1)
    scope: ResourceScopeRequest = Field(default_factory=ResourceScopeRequest)
    name: StrictStr = Field(..., min_length=1, max_length=255)
    media_type: StrictStr = Field(..., min_length=3, max_length=255)
    content_base64: StrictStr = Field(..., min_length=1)
    description: StrictStr | None = Field(default=None, max_length=4000)
    tags: tuple[StrictStr, ...] = Field(default=(), max_length=32)
    metadata: JsonObject = Field(default_factory=dict)


class ResourceUploadOptions(BaseModel):
    """Optional upload metadata for resource registration."""

    model_config = ConfigDict(extra='forbid', frozen=True)

    resource_id: StrictStr | None = Field(default=None, min_length=1)
    scope: ResourceScopeRequest = Field(default_factory=ResourceScopeRequest)
    description: StrictStr | None = Field(default=None, max_length=4000)
    tags: tuple[StrictStr, ...] = Field(default=(), max_length=32)
    metadata: JsonObject = Field(default_factory=dict)


class ResourcePatch(BaseModel):
    """Request body for patching resource metadata and portal scope."""

    model_config = ConfigDict(extra='forbid', frozen=True)

    name: StrictStr | None = Field(default=None, min_length=1, max_length=255)
    description: StrictStr | None = Field(default=None, max_length=4000)
    tags: tuple[StrictStr, ...] | None = Field(default=None, max_length=32)
    metadata: JsonObject | None = None
    sharing_scope: PortalScope | None = None


class ResourceRecord(BaseModel):
    """Typed SDK resource pointer record returned by resource routes."""

    model_config = ConfigDict(extra='forbid', frozen=True)

    memory_id: StrictStr = Field(..., min_length=1)
    resource_id: StrictStr = Field(..., min_length=1)
    resource_thread_id: StrictStr = Field(..., pattern=r'^rth-[0-9a-f]{32}$')
    scope: MemoryScope
    name: StrictStr = Field(..., min_length=1, max_length=255)
    description: StrictStr | None = Field(default=None, max_length=4000)
    media_type: StrictStr = Field(..., min_length=3, max_length=255)
    content_hash: StrictStr = Field(..., pattern=r'^[0-9a-f]{64}$')
    size_bytes: StrictInt = Field(..., ge=1)
    status: ResourceStatus
    processing_status: ProcessingStatus
    tags: tuple[StrictStr, ...] = Field(default=(), max_length=32)
    metadata: JsonObject = Field(default_factory=dict)
    binding_type: ResourceBindingType
    deduplicated: StrictBool
    created_at: StrictStr = Field(..., min_length=1)
    updated_at: StrictStr = Field(..., min_length=1)
    markdown: StrictStr | None = Field(default=None, min_length=1)
    structure: tuple[JsonObject, ...] = ()
    failure_reason: StrictStr | None = Field(default=None, min_length=1, max_length=2048)


class ResourceDeleteResponse(BaseModel):
    """Typed SDK response for successful resource deletes."""

    model_config = ConfigDict(extra='forbid', frozen=True)

    success: StrictBool


class _ResourceListResponse(BaseModel):
    """Internal list envelope for resource records."""

    model_config = ConfigDict(extra='forbid', frozen=True)

    items: tuple[ResourceRecord, ...] = ()


class ResourcesClient:
    """Root facade for resource routes."""

    def __init__(self, http: Callable[[], HttpJsonClient]) -> None:
        """Create a resources facade backed by a bound HTTP client accessor."""
        self._get_http = http

    def upload(
        self,
        content: bytes,
        *,
        media_type: str,
        name: str,
        options: ResourceUploadOptions | None = None,
    ) -> ResourceRecord:
        """Upload bytes and register one resource."""
        return run_blocking(
            lambda: self.aupload(
                content,
                media_type=media_type,
                name=name,
                options=options,
            ),
        )

    async def aupload(
        self,
        content: bytes,
        *,
        media_type: str,
        name: str,
        options: ResourceUploadOptions | None = None,
    ) -> ResourceRecord:
        """Upload bytes and register one resource asynchronously."""
        if not content:
            message = 'resource content must not be empty'
            raise ValueError(message)
        resolved_options = options or ResourceUploadOptions()
        request = _ResourceUpload(
            resource_id=resolved_options.resource_id,
            scope=resolved_options.scope,
            name=name,
            media_type=media_type,
            content_base64=base64.b64encode(content).decode('ascii'),
            description=resolved_options.description,
            tags=tuple(resolved_options.tags),
            metadata=dict(resolved_options.metadata),
        )
        return ResourceRecord.model_validate(
            await self._get_http().post(RESOURCES_PATH, _dump_model(request)),
        )

    def list(
        self,
        *,
        query: str | None = None,
        status: ResourceStatusFilter = 'registered',
        processing_status: ProcessingStatusFilter = 'all',
        limit: int = 50,
    ) -> tuple[ResourceRecord, ...]:
        """List resource records visible to the caller."""
        return run_blocking(
            lambda: self.alist(
                query=query,
                status=status,
                processing_status=processing_status,
                limit=limit,
            ),
        )

    async def alist(
        self,
        *,
        query: str | None = None,
        status: ResourceStatusFilter = 'registered',
        processing_status: ProcessingStatusFilter = 'all',
        limit: int = 50,
    ) -> tuple[ResourceRecord, ...]:
        """List resource records asynchronously."""
        payload = await self._get_http().get(
            RESOURCES_PATH,
            params=_list_params(
                query=query,
                status=status,
                processing_status=processing_status,
                limit=limit,
            ),
        )
        return _ResourceListResponse.model_validate(payload).items

    def get(self, resource_id: str) -> ResourceRecord:
        """Fetch one resource record."""
        return run_blocking(lambda: self.aget(resource_id))

    async def aget(self, resource_id: str) -> ResourceRecord:
        """Fetch one resource record asynchronously."""
        path = RESOURCE_PATH.format(resource_id=resource_id)
        return ResourceRecord.model_validate(await self._get_http().get(path))

    def patch(self, resource_id: str, request: ResourcePatch) -> ResourceRecord:
        """Patch one resource metadata record."""
        return run_blocking(lambda: self.apatch(resource_id, request))

    async def apatch(self, resource_id: str, request: ResourcePatch) -> ResourceRecord:
        """Patch one resource metadata record asynchronously."""
        path = RESOURCE_PATH.format(resource_id=resource_id)
        return ResourceRecord.model_validate(
            await self._get_http().patch(path, _dump_model(request)),
        )

    def delete(self, resource_id: str) -> ResourceDeleteResponse:
        """Soft-delete one portal resource."""
        return run_blocking(lambda: self.adelete(resource_id))

    async def adelete(self, resource_id: str) -> ResourceDeleteResponse:
        """Soft-delete one portal resource asynchronously."""
        path = RESOURCE_PATH.format(resource_id=resource_id)
        return ResourceDeleteResponse.model_validate(await self._get_http().delete(path))

    def bind(
        self,
        resource_id: str,
        *,
        binding_type: BoundResourceType,
        target_id: str,
    ) -> ResourceRecord:
        """Bind one resource to an agent or swarm scope."""
        return run_blocking(
            lambda: self.abind(resource_id, binding_type=binding_type, target_id=target_id),
        )

    async def abind(
        self,
        resource_id: str,
        *,
        binding_type: BoundResourceType,
        target_id: str,
    ) -> ResourceRecord:
        """Bind one resource asynchronously to an agent or swarm scope."""
        path = RESOURCE_BIND_PATH.format(resource_id=resource_id)
        payload: JsonObject = {'binding_type': binding_type, 'target_id': target_id}
        return ResourceRecord.model_validate(await self._get_http().post(path, payload))

    def restore(self, resource_id: str) -> ResourceRecord:
        """Restore one resource to registered portal scope."""
        return run_blocking(lambda: self.arestore(resource_id))

    async def arestore(self, resource_id: str) -> ResourceRecord:
        """Restore one resource asynchronously to registered portal scope."""
        path = RESOURCE_RESTORE_PATH.format(resource_id=resource_id)
        return ResourceRecord.model_validate(await self._get_http().post(path, {}))

    def rebind(self, resource_id: str) -> ResourceRecord:
        """Rebind one agent or swarm resource to portal scope."""
        return run_blocking(lambda: self.arebind(resource_id))

    async def arebind(self, resource_id: str) -> ResourceRecord:
        """Rebind one agent or swarm resource asynchronously to portal scope."""
        path = RESOURCE_REBIND_PATH.format(resource_id=resource_id)
        return ResourceRecord.model_validate(await self._get_http().post(path, {}))


def _list_params(
    *,
    query: str | None,
    status: ResourceStatusFilter,
    processing_status: ProcessingStatusFilter,
    limit: int,
) -> dict[str, str | int]:
    params: dict[str, str | int] = {
        'status': status,
        'processing_status': processing_status,
        'limit': limit,
    }
    if query is not None:
        params['query'] = query
    return params


def _dump_model(model: BaseModel) -> JsonObject:
    return model.model_dump(mode='json', exclude_none=True, exclude_defaults=True)


__all__ = [
    'ResourceDeleteResponse',
    'ResourcePatch',
    'ResourceRecord',
    'ResourceScopeRequest',
    'ResourceUploadOptions',
    'ResourcesClient',
]
