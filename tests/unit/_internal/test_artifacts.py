"""Public SDK ordinary-artifact wire behavior."""

from __future__ import annotations

import asyncio
import hashlib
from http import HTTPStatus
from typing import TYPE_CHECKING

import httpx
import pytest
from pydantic import AnyUrl

from maivn import ArtifactDownload, ArtifactMetadata, ArtifactPreview, Client, ClientConfig
from maivn._internal.errors import MaivnHTTPError

if TYPE_CHECKING:
    from collections.abc import Callable
    from pathlib import Path

_DOWNLOAD_SIZE = 13
_PREVIEW_REVISION = 7
_PREVIEW_CONTENT = b'%PDF-1.7\nSDK preview proof'


def test_artifacts_client_lists_gets_and_downloads_through_authenticated_routes() -> None:
    """A caller obtains real bytes without learning storage coordinates or URLs."""
    requests: list[httpx.Request] = []
    payload = _metadata_payload()

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        assert request.headers['authorization'] == 'Bearer test-key'
        if request.url.path == '/v1/artifacts':
            return httpx.Response(
                HTTPStatus.OK,
                json={'artifacts': [payload], 'next_cursor': 'cursor-2'},
            )
        if request.url.path == '/v1/artifacts/artifact-sdk':
            return httpx.Response(HTTPStatus.OK, json=payload)
        assert request.url.path == '/v1/artifacts/artifact-sdk/download'
        return httpx.Response(
            HTTPStatus.OK,
            content=b'PNG-SDK-PROOF',
            headers={
                'content-type': 'image/png',
                'content-length': '13',
                'content-disposition': (
                    'attachment; filename="report.png"; filename*=UTF-8\'\'quarterly%20report.png'
                ),
                'cache-control': 'no-store',
            },
        )

    client = _client(handler)

    page = client.artifacts.list(invocation_id='invocation-sdk', limit=17)
    fetched = client.artifacts.get('artifact-sdk', revision=2)
    downloaded = client.artifacts.download('artifact-sdk', revision=2)

    assert page.artifacts == (ArtifactMetadata.model_validate(payload),)
    assert page.next_cursor == 'cursor-2'
    assert fetched == page.artifacts[0]
    assert isinstance(downloaded, ArtifactDownload)
    assert downloaded.content == b'PNG-SDK-PROOF'
    assert downloaded.filename == 'quarterly report.png'
    assert downloaded.mime_type == 'image/png'
    assert downloaded.size_bytes == _DOWNLOAD_SIZE
    assert downloaded.sha256 == hashlib.sha256(downloaded.content).hexdigest()
    assert dict(requests[0].url.params) == {
        'invocation_id': 'invocation-sdk',
        'state': 'available',
        'limit': '17',
    }
    assert dict(requests[1].url.params) == {'revision': '2'}
    assert dict(requests[2].url.params) == {'revision': '2'}


def test_artifacts_client_previews_an_exact_encoded_revision_with_verified_headers() -> None:
    """Preview bytes are exact-revision, URL-safe, and digest-verified."""
    requests: list[httpx.Request] = []
    digest = hashlib.sha256(_PREVIEW_CONTENT).hexdigest()

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        assert request.headers['authorization'] == 'Bearer test-key'
        return _preview_response(digest=digest)

    preview = _client(handler).artifacts.preview(
        'artifact / r\N{LATIN SMALL LETTER E WITH ACUTE}sum\N{LATIN SMALL LETTER E WITH ACUTE}',
        revision=_PREVIEW_REVISION,
    )

    assert isinstance(preview, ArtifactPreview)
    assert preview.content == _PREVIEW_CONTENT
    assert preview.content_type == 'application/pdf'
    assert preview.filename == 'quarterly preview.pdf'
    assert preview.sha256 == digest
    assert preview.source_revision_id == _PREVIEW_REVISION
    assert requests[0].url.raw_path == (
        b'/v1/artifacts/artifact%20%2F%20r%C3%A9sum%C3%A9/revisions/7/preview'
    )


def test_artifacts_client_async_preview_has_sync_parity() -> None:
    """The async facade returns the same validated preview value."""
    digest = hashlib.sha256(_PREVIEW_CONTENT).hexdigest()

    async def exercise() -> ArtifactPreview:
        return await _client(lambda _request: _preview_response(digest=digest)).artifacts.apreview(
            'artifact-sdk', revision=7
        )

    preview = asyncio.run(exercise())

    assert preview == ArtifactPreview(
        content=_PREVIEW_CONTENT,
        content_type='application/pdf',
        filename='quarterly preview.pdf',
        sha256=digest,
        source_revision_id=7,
    )


@pytest.mark.parametrize(
    ('header_overrides', 'content'),
    [
        ({'x-artifact-sha256': ''}, _PREVIEW_CONTENT),
        ({'x-artifact-sha256': '0' * 64}, _PREVIEW_CONTENT),
        ({'x-artifact-source-revision': '8'}, _PREVIEW_CONTENT),
        ({'x-artifact-source-revision': 'not-an-integer'}, _PREVIEW_CONTENT),
        ({'content-length': '999'}, _PREVIEW_CONTENT),
        ({'content-type': 'text/html'}, _PREVIEW_CONTENT),
        ({'content-disposition': 'attachment; filename="preview.pdf"'}, _PREVIEW_CONTENT),
        ({'cache-control': 'public, max-age=60'}, _PREVIEW_CONTENT),
        ({'x-content-type-options': ''}, _PREVIEW_CONTENT),
        ({}, b''),
    ],
)
def test_artifacts_client_rejects_invalid_preview_responses(
    header_overrides: dict[str, str],
    content: bytes,
) -> None:
    """Untrusted preview bytes never cross the typed SDK boundary."""
    digest = hashlib.sha256(_PREVIEW_CONTENT).hexdigest()

    def handler(_request: httpx.Request) -> httpx.Response:
        return _preview_response(
            digest=digest,
            content=content,
            header_overrides=header_overrides,
        )

    with pytest.raises(ValueError, match='artifact preview response was invalid'):
        _client(handler).artifacts.preview('artifact-sdk', revision=7)


def test_artifacts_preview_to_replaces_atomically_only_after_validation(tmp_path: Path) -> None:
    """A failed response leaves an existing destination byte-for-byte unchanged."""
    destination = tmp_path / 'preview.pdf'
    destination.write_bytes(b'existing-preview')
    responses = iter(
        [
            _preview_response(digest='0' * 64),
            _preview_response(digest=hashlib.sha256(_PREVIEW_CONTENT).hexdigest()),
        ]
    )
    client = _client(lambda _request: next(responses))

    with pytest.raises(ValueError, match='artifact preview response was invalid'):
        client.artifacts.preview_to('artifact-sdk', destination, revision=7)

    assert destination.read_bytes() == b'existing-preview'
    assert list(tmp_path.iterdir()) == [destination]

    preview = client.artifacts.preview_to('artifact-sdk', destination, revision=_PREVIEW_REVISION)

    assert preview.content == _PREVIEW_CONTENT
    assert destination.read_bytes() == _PREVIEW_CONTENT
    assert list(tmp_path.iterdir()) == [destination]


def test_artifacts_async_preview_to_writes_the_same_verified_bytes(tmp_path: Path) -> None:
    """The async file helper has the same result and atomic write semantics."""
    destination = tmp_path / 'async-preview.pdf'
    digest = hashlib.sha256(_PREVIEW_CONTENT).hexdigest()

    async def exercise() -> ArtifactPreview:
        return await _client(
            lambda _request: _preview_response(digest=digest)
        ).artifacts.apreview_to('artifact-sdk', destination, revision=_PREVIEW_REVISION)

    preview = asyncio.run(exercise())

    assert preview.sha256 == digest
    assert destination.read_bytes() == _PREVIEW_CONTENT
    assert list(tmp_path.iterdir()) == [destination]


def test_artifacts_preview_preserves_transport_errors() -> None:
    """HTTP authorization failures remain typed SDK transport errors."""
    client = _client(
        lambda _request: httpx.Response(
            HTTPStatus.FORBIDDEN,
            json={'detail': 'Not visible.', 'code': 'artifact_forbidden'},
        )
    )

    with pytest.raises(MaivnHTTPError) as captured:
        client.artifacts.preview('artifact-sdk', revision=7)

    assert captured.value.status_code == HTTPStatus.FORBIDDEN
    assert captured.value.code == 'artifact_forbidden'


def _client(handler: Callable[[httpx.Request], httpx.Response]) -> Client:
    return Client(
        config=ClientConfig(api_key='test-key', base_url=AnyUrl('http://testserver')),
        transport=httpx.MockTransport(handler),
    )


def _metadata_payload() -> dict[str, object]:
    return {
        'artifact_id': 'artifact-sdk',
        'revision': 2,
        'state': 'available',
        'invocation_id': 'invocation-sdk',
        'logical_output_id': 'report',
        'kind': 'image',
        'mime_type': 'image/png',
        'display_filename': 'quarterly report.png',
        'byte_size': 13,
        'sha256': hashlib.sha256(b'PNG-SDK-PROOF').hexdigest(),
        'created_at': '2026-08-11T12:00:00Z',
        'expires_at': '2026-09-10T12:00:00Z',
    }


def _preview_response(
    *,
    digest: str,
    content: bytes = _PREVIEW_CONTENT,
    header_overrides: dict[str, str] | None = None,
) -> httpx.Response:
    headers = {
        'content-type': 'application/pdf',
        'content-length': str(len(content)),
        'content-disposition': (
            'inline; filename="preview.pdf"; filename*=UTF-8\'\'quarterly%20preview.pdf'
        ),
        'cache-control': 'private, no-store',
        'x-content-type-options': 'nosniff',
        'x-artifact-sha256': digest,
        'x-artifact-source-revision': '7',
    }
    headers.update(header_overrides or {})
    return httpx.Response(HTTPStatus.OK, content=content, headers=headers)
