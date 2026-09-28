"""Invocation image selection is an exact authenticated SDK operation."""

from __future__ import annotations

import asyncio
import hashlib
import json
import re
from typing import Any, cast

import httpx
import pytest
from maivn_contracts.artifacts import OrdinaryArtifactRef
from pydantic import AnyUrl

from maivn import Agent, Client, ClientConfig
from maivn._internal.artifact_image_context import InvocationArtifactImages
from maivn._internal.models import StreamEvent
from maivn.artifact_images import ArtifactImageUnavailableError, resolve_artifact_image
from maivn.messages import HumanMessage
from tests.unit._internal.test_tool_files import (
    _artifact_ref,  # pyright: ignore[reportPrivateUsage] - shared canonical receipt fixture.
    _render_tool_sse_response,  # pyright: ignore[reportPrivateUsage] - shared SDK pump fixture.
)


def _image_ref() -> OrdinaryArtifactRef:
    value = _artifact_ref(b'PNG')
    value.update(
        {
            'kind': 'image',
            'mime_type': 'image/png',
            'display_filename': 'image.png',
            'safe_preview': {'kind': 'image', 'width_pixels': 1, 'height_pixels': 1},
        }
    )
    return OrdinaryArtifactRef.model_validate(value)


def test_context_only_resolves_registered_exact_refs_and_conflicts_fail_closed() -> None:
    """Model-chosen IDs never become download authority, and conflicts never pick a winner."""
    ref = _image_ref()
    seen: list[object] = []

    class Ordinary:
        async def adownload(self, selected: object) -> object:
            seen.append(selected)
            return type('Download', (), {'content': b'PNG'})()

    registry = InvocationArtifactImages(
        ordinary=cast('Any', Ordinary()), private=None, session_id='session-current'
    )
    registry.register((ref,))

    async def run() -> None:
        with registry.activate():
            image = await resolve_artifact_image(ref.artifact_id)
            assert image.content == b'PNG'
            with pytest.raises(ArtifactImageUnavailableError):
                await resolve_artifact_image('invented-id')
            registry.register(
                (ref.model_copy(update={'sha256': hashlib.sha256(b'other').hexdigest()}),)
            )
            with pytest.raises(ArtifactImageUnavailableError):
                await resolve_artifact_image(ref.artifact_id)
        with pytest.raises(ArtifactImageUnavailableError):
            await resolve_artifact_image(ref.artifact_id)

    asyncio.run(run())
    assert seen == [ref]


def test_registry_accepts_canonical_completion_refs_and_ignores_result_embedded_refs() -> None:
    """Only the event's typed outcome attachment field enters the registry."""
    ref = _image_ref()
    registry = InvocationArtifactImages(
        ordinary=cast('Any', None), private=None, session_id='session'
    )
    registry.observe(
        StreamEvent(
            position=1,
            event_type='system_tool_complete',
            data={
                'type': 'system_tool_complete',
                'payload': {
                    'outcome': {
                        'status': 'ok',
                        'call_id': 'image-call',
                        'duration_ms': 1,
                        'result': {'artifact_refs': [ref.model_dump(mode='json')]},
                    }
                },
            },
        )
    )
    assert registry.references == ()
    registry.observe(
        StreamEvent(
            position=2,
            event_type='system_tool_complete',
            data={
                'type': 'system_tool_complete',
                'payload': {
                    'outcome': {
                        'status': 'ok',
                        'call_id': 'image-call',
                        'duration_ms': 1,
                        'result': {},
                        'artifact_refs': [ref.model_dump(mode='json')],
                    }
                },
            },
        )
    )
    assert registry.references == (ref,)


@pytest.mark.parametrize('selector_source', ['generated', 'input'])
def test_public_agent_image_bridge_uses_exact_authenticated_download(selector_source: str) -> None:
    """The real SDK stream pump binds generation outcomes and caller attachment selectors."""
    ref = _image_ref()
    downloads: list[str] = []
    original = _render_tool_sse_response().content.decode()
    event = json.loads(
        next(line[6:] for line in original.splitlines() if line.startswith('data: '))
    )
    event.update(
        {
            'type': 'system_tool_complete',
            'event_id': 'image-generated',
            'ordinal': 'ordinal_0',
            'correlation_id': 'image-call',
            'payload': {
                'stage': 'tool_complete',
                'outcome': {
                    'status': 'ok',
                    'duration_ms': 1,
                    'call_id': 'image-call',
                    'result': {},
                    'artifact_refs': [ref.model_dump(mode='json')],
                },
            },
        }
    )
    prefix = f'id: 1\nevent: system_tool_complete\ndata: {json.dumps(event)}\n\n'
    stream = (
        prefix + re.sub(r'id: ([123])\n', lambda match: f'id: {int(match[1]) + 1}\n', original)
        if selector_source == 'generated'
        else original
    )

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == '/v1/invoke':
            return httpx.Response(
                202, json={'session_id': 'session_sdk_file', 'stream_position': 0}
            )
        if request.url.path.endswith('/events'):
            return httpx.Response(
                200, content=stream, headers={'content-type': 'text/event-stream'}
            )
        if request.url.path.endswith('/tools/results'):
            assert json.loads(request.content)['outcomes'][0]['result'] == {'composed': True}
            return httpx.Response(202, json={'accepted': True})
        assert request.url.path == f'/v1/artifacts/{ref.artifact_id}/download'
        assert request.headers['authorization'] == 'Bearer test-key'
        assert dict(request.url.params) == {'revision': '1'}
        downloads.append(ref.artifact_id)
        return httpx.Response(
            200,
            content=b'PNG',
            headers={
                'content-type': 'image/png',
                'content-length': '3',
                'content-disposition': 'attachment; filename="image.png"',
            },
        )

    client = Client(
        config=ClientConfig(api_key='test-key', base_url=AnyUrl('http://testserver')),
        transport=httpx.MockTransport(handler),
    )
    agent = Agent(name='image-composition', client=client)

    @agent.toolify(name='render')
    async def compose() -> dict[str, bool]:
        local = await resolve_artifact_image(ref.artifact_id)
        assert local.content == b'PNG'
        return {'composed': True}

    _ = compose
    message = HumanMessage(
        content='Compose the attached image',
        artifact_refs=(ref,) if selector_source == 'input' else None,
    )
    result = agent.invoke(message)
    assert result.response == 'report ready'
    assert downloads == [ref.artifact_id]
    with pytest.raises(ArtifactImageUnavailableError):
        asyncio.run(resolve_artifact_image(ref.artifact_id))


def test_download_failure_is_value_free_and_conflict_during_download_refuses() -> None:
    """Neither provider details nor a race against an ambiguous selector may escape."""
    ref = _image_ref()

    class Broken:
        async def adownload(self, _ref: object) -> object:
            message = 'private-source-sentinel and provider credentials'
            raise RuntimeError(message)

    failed = InvocationArtifactImages(
        ordinary=cast('Any', Broken()), private=None, session_id='session'
    )
    failed.register((ref,))
    with pytest.raises(ArtifactImageUnavailableError) as caught:
        asyncio.run(failed.resolve(ref.artifact_id))
    assert 'private-source-sentinel' not in str(caught.value)
    assert caught.value.__suppress_context__ is True

    class ConcurrentConflict:
        async def adownload(self, _ref: object) -> object:
            raced.register((ref.model_copy(update={'sha256': '0' * 64}),))
            return type('Download', (), {'content': b'PNG'})()

    raced = InvocationArtifactImages(
        ordinary=cast('Any', ConcurrentConflict()), private=None, session_id='session'
    )
    raced.register((ref,))
    with pytest.raises(ArtifactImageUnavailableError):
        asyncio.run(raced.resolve(ref.artifact_id))
