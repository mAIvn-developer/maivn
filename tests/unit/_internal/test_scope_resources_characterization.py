"""Characterization tests for scope resource declaration validation and registration."""

from __future__ import annotations

import asyncio
import base64
import json
from http import HTTPStatus
from typing import TYPE_CHECKING, Any, cast

import httpx
import pytest
from pydantic import AnyUrl

from maivn import Agent, Client, ClientConfig, ResourceRegistrationError, Swarm

if TYPE_CHECKING:
    from pathlib import Path

JsonObject = dict[str, Any]


def _resource_record() -> JsonObject:
    return {
        'memory_id': 'res-1',
        'resource_id': 'res-1',
        'resource_thread_id': 'rth-0123456789abcdef0123456789abcdef',
        'scope': {
            'project_id': 'project-sdk',
            'organization_id': 'org-sdk',
            'agent_id': None,
            'swarm_id': None,
            'sharing_scope': 'project',
        },
        'name': 'notes.txt',
        'description': None,
        'media_type': 'text/plain',
        'content_hash': '0' * 64,
        'size_bytes': 5,
        'status': 'registered',
        'processing_status': 'pending',
        'tags': [],
        'metadata': {},
        'binding_type': 'portal',
        'deduplicated': False,
        'created_at': '2026-07-13T12:00:00Z',
        'updated_at': '2026-07-13T12:00:00Z',
    }


def _final_sse() -> httpx.Response:
    event: JsonObject = {
        'event_id': 'evt-final',
        'ordinal': 'ord-1',
        'type': 'final',
        'session_id': 'ses-res',
        'root_event_id': 'evt-root',
        'payload': {
            'message': {
                'message_id': 'msg-final',
                'role': 'assistant',
                'content': 'done',
                'ts': '2026-07-04T12:00:00Z',
            },
            'usage': {},
            'tool_calls': {'count': 0, 'names': []},
        },
        'ts': '2026-07-04T12:00:00Z',
    }
    return httpx.Response(
        HTTPStatus.OK,
        headers={'content-type': 'text/event-stream'},
        content=f'id: 1\nevent: final\ndata: {json.dumps(event)}\n\n',
    )


class _Server:
    def __init__(self, *, upload_status: int = HTTPStatus.OK) -> None:
        self.calls: list[tuple[str, JsonObject | None]] = []
        self._upload_status = upload_status

    def handle(self, request: httpx.Request) -> httpx.Response:
        body = cast('JsonObject', json.loads(request.content)) if request.content else None
        self.calls.append((request.url.path, body))
        if request.url.path == '/v1/resources':
            if self._upload_status != HTTPStatus.OK:
                return httpx.Response(self._upload_status, json={'detail': 'storage offline'})
            return httpx.Response(HTTPStatus.OK, json=_resource_record())
        if request.url.path == '/v1/invoke':
            return httpx.Response(
                HTTPStatus.ACCEPTED, json={'session_id': 'ses-res', 'stream_position': 0}
            )
        return _final_sse()

    def client(self) -> Client:
        return Client(
            config=ClientConfig(api_key='test-key', base_url=AnyUrl('http://testserver')),
            transport=httpx.MockTransport(self.handle),
            private_data_store=None,
        )


def _registration_error(resource: JsonObject, *, scope: str = 'agent') -> str:
    server = _Server()
    holder = (
        Agent(name='holder', client=server.client(), resources=[resource])
        if scope == 'agent'
        else Swarm(name='holder', client=server.client(), resources=[resource])
    )
    with pytest.raises(ResourceRegistrationError) as raised:
        _ = holder.invoke('go')
    assert server.calls == []
    return str(raised.value)


@pytest.mark.parametrize(
    ('resource', 'expected'),
    [
        (
            {'text_content': '', 'name': 'n', 'mime_type': 'text/plain'},
            'resources[0].text_content must be a non-empty string',
        ),
        (
            {'text_content': 'x', 'mime_type': 'text/plain'},
            'resources[0].name must be a non-empty string',
        ),
        (
            {'text_content': 'x', 'name': '  ', 'mime_type': 'text/plain'},
            'resources[0].name must be a non-empty string',
        ),
        ({'text_content': 'x', 'name': 'n'}, 'resources[0].mime_type must be a non-empty string'),
        (
            {'text_content': 'x', 'name': 'n', 'mime_type': 'text/plain', 'description': 3},
            'resources[0].description must be a string when provided',
        ),
        (
            {'text_content': 'x', 'name': 'n', 'mime_type': 'text/plain', 'tags': 'tag'},
            'resources[0].tags must be a list of strings when provided',
        ),
        (
            {'text_content': 'x', 'name': 'n', 'mime_type': 'text/plain', 'tags': ['ok', ' ']},
            'resources[0].tags[1] must be a non-empty string',
        ),
        (
            {'text_content': 'x', 'name': 'n', 'mime_type': 'text/plain', 'binding_type': 'portal'},
            'resources[0].binding_type must be agent or swarm when provided',
        ),
        (
            {'text_content': 'x', 'name': 'n', 'mime_type': 'text/plain', 'binding_type': 'swarm'},
            "resources[0].binding_type must be 'agent' for this scope",
        ),
    ],
)
def test_invalid_agent_resource_declarations_fail_before_any_request(
    resource: JsonObject, expected: str
) -> None:
    """Each malformed declaration raises ResourceRegistrationError before the network."""
    assert _registration_error(resource) == expected


def test_swarm_resource_binding_must_match_the_swarm_scope() -> None:
    """A swarm resource cannot request an agent binding."""
    resource = {
        'text_content': 'x',
        'name': 'n',
        'mime_type': 'text/plain',
        'binding_type': 'agent',
    }

    assert (
        _registration_error(resource, scope='swarm')
        == "resources[0].binding_type must be 'swarm' for this scope"
    )


def test_file_resources_must_be_readable_and_non_empty(tmp_path: Path) -> None:
    """Missing and empty files are refused with the same message."""
    empty = tmp_path / 'empty.txt'
    empty.write_bytes(b'')

    for path in (tmp_path / 'missing.txt', empty):
        message = _registration_error({'file': path, 'name': 'n', 'mime_type': 'text/plain'})
        assert message == 'resources[0].file must reference a readable non-empty file'


def test_unbound_resource_uploads_without_binding_and_registers_once(tmp_path: Path) -> None:
    """A resource without binding_type uploads exact file bytes, shared, and is not bound."""
    source = tmp_path / 'notes.txt'
    source.write_bytes(b'notes')
    server = _Server()
    agent = Agent(
        name='holder',
        client=server.client(),
        resources=[
            {
                'file': str(source),
                'name': 'notes.txt',
                'mime_type': 'text/plain',
                'description': 'Team notes.',
            }
        ],
    )

    _ = asyncio.run(agent.ainvoke('go'))
    _ = asyncio.run(agent.ainvoke('again'))

    resource_calls = [body for path, body in server.calls if path.startswith('/v1/resources')]
    assert resource_calls == [
        {
            'scope': {'shared': True},
            'name': 'notes.txt',
            'media_type': 'text/plain',
            'content_base64': base64.b64encode(b'notes').decode('ascii'),
            'description': 'Team notes.',
        },
    ]


def test_upload_failure_is_wrapped_with_the_resource_and_scope_names() -> None:
    """A transport failure during upload becomes a ResourceRegistrationError naming both."""
    server = _Server(upload_status=HTTPStatus.SERVICE_UNAVAILABLE)
    swarm = Swarm(
        name='team',
        client=server.client(),
        resources=[
            {
                'text_content': 'x',
                'name': 'notes.txt',
                'mime_type': 'text/plain',
                'binding_type': 'swarm',
            }
        ],
    )

    with pytest.raises(ResourceRegistrationError) as raised:
        _ = swarm.invoke('go')

    assert str(raised.value) == "failed to register resource 'notes.txt' for swarm 'team'"
    assert raised.value.__cause__ is not None
    assert [path for path, _body in server.calls] == ['/v1/resources']
