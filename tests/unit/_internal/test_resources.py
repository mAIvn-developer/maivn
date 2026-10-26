"""Unit tests for SDK resource wire mapping."""

from __future__ import annotations

import base64
import json
from collections.abc import Callable, Coroutine
from http import HTTPStatus
from typing import Any, TypeAlias, cast

import httpx
import pytest
from pydantic import AnyUrl, ValidationError

from maivn import (
    Client,
    ClientConfig,
    PIIWhitelist,
    PIIWhitelistEntry,
    ResourcePatch,
    ResourceScopeRequest,
    ResourceUploadOptions,
)
from maivn._internal.resources import ResourceRecord

JsonObject: TypeAlias = dict[str, Any]
_SyncHandler: TypeAlias = Callable[[httpx.Request], httpx.Response]
_AsyncHandler: TypeAlias = Callable[[httpx.Request], Coroutine[None, None, httpx.Response]]


@pytest.mark.parametrize(
    'navigation_source',
    [
        None,
        {
            'generation_id': 'a' * 64,
            'coordinate_space': 'converted_markdown_codepoints',
            'markdown_sha256': 'b' * 64,
        },
    ],
)
def test_resource_reads_hide_server_viewer_identity(navigation_source: JsonObject | None) -> None:
    """Source viewer transport metadata does not change the ordinary SDK resource shape."""
    payload = _resource_json()
    payload['navigation_source'] = navigation_source

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            HTTPStatus.OK,
            json={'items': [payload]} if request.url.path == '/v1/resources' else payload,
        )

    client = _client(handler)
    detail = client.resources.get('resource-sdk')
    listed = client.resources.list()
    assert detail.resource_id == listed[0].resource_id
    assert 'navigation_source' not in detail.model_dump()


def test_resource_viewer_compatibility_preserves_strict_other_fields() -> None:
    """Only recognized server viewer metadata is omitted from SDK parsing."""
    payload = _resource_json()
    payload.update({'navigation_source': None, 'unexpected_resource_field': True})
    with pytest.raises(ValidationError, match='unexpected_resource_field'):
        ResourceRecord.model_validate(payload)


def test_client_resource_methods_map_to_v1_resource_routes() -> None:
    """Resource helpers encode bytes internally and map lifecycle routes."""
    calls: list[tuple[str, str, JsonObject | None, dict[str, str]]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(
            (request.method, request.url.path, _json_body(request), dict(request.url.params)),
        )
        if request.method == 'GET' and request.url.path == '/v1/resources':
            return httpx.Response(HTTPStatus.OK, json={'items': [_resource_json()]})
        if request.method == 'DELETE':
            return httpx.Response(HTTPStatus.OK, json={'success': True})
        if request.url.path.endswith('/bind'):
            return httpx.Response(
                HTTPStatus.OK,
                json=_resource_json(
                    binding_type='agent',
                    sharing_scope='agent',
                    agent_id='agent-sdk',
                ),
            )
        if request.url.path.endswith('/restore') or request.url.path.endswith('/rebind'):
            return httpx.Response(HTTPStatus.OK, json=_resource_json())
        if request.method == 'PATCH':
            return httpx.Response(
                HTTPStatus.OK,
                json=_resource_json(name='renewal-updated.txt', metadata={'phase': 'patched'}),
            )
        return httpx.Response(HTTPStatus.OK, json=_resource_json())

    client = _client(handler)
    scope = ResourceScopeRequest(project_id='project-sdk', organization_id='org-sdk')

    uploaded = client.resources.upload(
        b'Renewal plan body',
        media_type='text/plain',
        name='renewal.txt',
        options=ResourceUploadOptions(
            scope=scope,
            description='Renewal plan',
            tags=('renewal', 'plan'),
            metadata={'source': 'sdk'},
        ),
    )
    listed = client.resources.list(
        query='renewal',
        status='all',
        processing_status='pending',
        limit=5,
    )
    fetched = client.resources.get('resource-sdk')
    patched = client.resources.patch(
        'resource-sdk',
        ResourcePatch(name='renewal-updated.txt', metadata={'phase': 'patched'}),
    )
    bound = client.resources.bind('resource-sdk', binding_type='agent', target_id='agent-sdk')
    restored = client.resources.restore('resource-sdk')
    rebound = client.resources.rebind('resource-sdk')
    deleted = client.resources.delete('resource-sdk')

    assert uploaded.resource_id == 'resource-sdk'
    assert uploaded.resource_thread_id == 'rth-0123456789abcdef0123456789abcdef'
    assert [item.resource_id for item in listed] == ['resource-sdk']
    assert fetched.media_type == 'text/plain'
    assert patched.name == 'renewal-updated.txt'
    assert bound.binding_type == 'agent'
    assert bound.scope.agent_id == 'agent-sdk'
    assert restored.binding_type == 'portal'
    assert rebound.binding_type == 'portal'
    assert deleted.success is True
    assert calls == [
        ('POST', '/v1/resources', _resource_upload_payload(), {}),
        (
            'GET',
            '/v1/resources',
            None,
            {'query': 'renewal', 'status': 'all', 'processing_status': 'pending', 'limit': '5'},
        ),
        ('GET', '/v1/resources/resource-sdk', None, {}),
        (
            'PATCH',
            '/v1/resources/resource-sdk',
            {'name': 'renewal-updated.txt', 'metadata': {'phase': 'patched'}},
            {},
        ),
        (
            'POST',
            '/v1/resources/resource-sdk/bind',
            {'binding_type': 'agent', 'target_id': 'agent-sdk'},
            {},
        ),
        ('POST', '/v1/resources/resource-sdk/restore', {}, {}),
        ('POST', '/v1/resources/resource-sdk/rebind', {}, {}),
        ('DELETE', '/v1/resources/resource-sdk', None, {}),
    ]


def _client(handler: _SyncHandler | _AsyncHandler) -> Client:
    transport = httpx.MockTransport(handler)
    return Client(
        config=ClientConfig(api_key='test-key', base_url=AnyUrl('http://testserver')),
        transport=transport,
    )


def _json_body(request: httpx.Request) -> JsonObject | None:
    if not request.content:
        return None
    return cast('JsonObject', json.loads(request.content.decode('utf-8')))


def _memory_scope_json(
    *,
    sharing_scope: str = 'project',
    agent_id: str | None = None,
    swarm_id: str | None = None,
) -> JsonObject:
    return {
        'project_id': 'project-sdk',
        'organization_id': 'org-sdk',
        'agent_id': agent_id,
        'swarm_id': swarm_id,
        'sharing_scope': sharing_scope,
    }


def _resource_json(
    *,
    name: str = 'renewal.txt',
    metadata: JsonObject | None = None,
    binding_type: str = 'portal',
    sharing_scope: str = 'project',
    agent_id: str | None = None,
) -> JsonObject:
    return {
        'memory_id': 'resource-sdk',
        'resource_id': 'resource-sdk',
        'resource_thread_id': 'rth-0123456789abcdef0123456789abcdef',
        'scope': _memory_scope_json(sharing_scope=sharing_scope, agent_id=agent_id),
        'name': name,
        'description': 'Renewal plan',
        'media_type': 'text/plain',
        'content_hash': '0' * 64,
        'size_bytes': 17,
        'status': 'registered',
        'processing_status': 'pending',
        'tags': ['plan', 'renewal'],
        'metadata': metadata or {'source': 'sdk'},
        'binding_type': binding_type,
        'deduplicated': False,
        'created_at': '2026-07-05T12:00:00Z',
        'updated_at': '2026-07-05T12:00:00Z',
    }


def _resource_upload_payload() -> JsonObject:
    return {
        'scope': {'project_id': 'project-sdk', 'organization_id': 'org-sdk'},
        'name': 'renewal.txt',
        'media_type': 'text/plain',
        'content_base64': base64.b64encode(b'Renewal plan body').decode('ascii'),
        'description': 'Renewal plan',
        'tags': ['renewal', 'plan'],
        'metadata': {'source': 'sdk'},
    }


def test_upload_sends_the_callers_whitelist_for_redaction() -> None:
    """A timesheet upload can approve its dates and times, as a redacted message can."""
    bodies: list[JsonObject | None] = []

    def handler(request: httpx.Request) -> httpx.Response:
        bodies.append(_json_body(request))
        return httpx.Response(HTTPStatus.OK, json=_resource_json())

    whitelist = PIIWhitelist(
        entries=(
            PIIWhitelistEntry(
                entity_type='datetime', justification='Dates and times are the timesheet.'
            ),
        ),
    )
    _client(handler).resources.upload(
        b'\x89PNG timesheet',
        media_type='image/png',
        name='timesheet.png',
        options=ResourceUploadOptions(pii_whitelist=whitelist),
    )

    assert bodies[0] is not None
    assert bodies[0]['pii_whitelist'] == whitelist.model_dump(
        mode='json', exclude_none=True, exclude_defaults=True
    )
    assert bodies[0]['pii_whitelist']['entries'][0]['entity_type'] == 'datetime'
