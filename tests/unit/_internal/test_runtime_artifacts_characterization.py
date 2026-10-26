"""Characterize ordinary artifact download and preview response validation."""

from __future__ import annotations

import hashlib
from http import HTTPStatus
from typing import TYPE_CHECKING

import httpx
import pytest
from maivn_contracts.artifacts import OrdinaryArtifactRef
from pydantic import AnyUrl

from maivn import Client, ClientConfig

if TYPE_CHECKING:
    from collections.abc import Callable
    from pathlib import Path

_CONTENT = b'PNG-CHARACTERIZED'
_INVALID = r'^artifact download response was invalid$'


def _client(handler: Callable[[httpx.Request], httpx.Response]) -> Client:
    return Client(
        config=ClientConfig(api_key='test-key', base_url=AnyUrl('http://testserver')),
        transport=httpx.MockTransport(handler),
    )


def _download_headers(**overrides: str | None) -> dict[str, str]:
    headers: dict[str, str | None] = {
        'content-type': 'image/png; charset=binary',
        'content-length': str(len(_CONTENT)),
        'content-disposition': 'attachment; filename="report.png"',
    }
    headers.update(overrides)
    return {key: value for key, value in headers.items() if value is not None}


def _serving(
    seen: list[httpx.Request], headers: dict[str, str], content: bytes = _CONTENT
) -> Callable[[httpx.Request], httpx.Response]:
    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(HTTPStatus.OK, content=content, headers=headers)

    return handler


def _ref(**changes: object) -> OrdinaryArtifactRef:
    value: dict[str, object] = {
        'artifact_id': 'artifact-char',
        'logical_output_id': 'report',
        'revision': 1,
        'kind': 'image',
        'mime_type': 'image/png',
        'created_at': '2026-08-11T12:00:00Z',
        'effective_retention': {
            'policy_snapshot_id': 'policy',
            'retention_class': 'artifact-30d',
            'expires_at': '2099-09-10T12:00:00Z',
        },
        'custody': 'ordinary',
        'state': 'available',
        'display_filename': 'report.png',
        'size_bytes': len(_CONTENT),
        'sha256': hashlib.sha256(_CONTENT).hexdigest(),
        'producer': {
            'producer_class': 'hosted_tool',
            'producer_id': 'system-create-artifact',
            'root_invocation_id': 'invocation',
            'session_id': 'session',
            'call_id': 'call',
        },
        'validation_receipt': {
            'receipt_id': 'receipt',
            'status': 'validated',
            'validator_profile': 'image-validator-v1',
            'validator_version': '1.0.0',
        },
        'safe_preview': {'kind': 'image', 'width_pixels': 4, 'height_pixels': 4},
        'retrieval_action': {'relation': 'artifact.download_authorization'},
    }
    value.update(changes)
    return OrdinaryArtifactRef.model_validate(value)


def test_list_without_optional_filters_sends_only_state_and_limit() -> None:
    """Omitted list filters are not sent."""
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(HTTPStatus.OK, json={'artifacts': [], 'next_cursor': None})

    page = _client(handler).artifacts.list(cursor='c-1', revision=4)
    assert page.artifacts == ()
    assert dict(seen[0].url.params) == {
        'state': 'available',
        'limit': '50',
        'revision': '4',
        'cursor': 'c-1',
    }


def test_download_by_exact_ref_uses_its_id_and_revision() -> None:
    """A reference download is revision-pinned and verified against the ref."""
    seen: list[httpx.Request] = []
    download = _client(
        _serving(seen, _download_headers(**{'content-length': None}))
    ).artifacts.download(_ref())
    assert download.content == _CONTENT
    assert download.mime_type == 'image/png'
    assert download.filename == 'report.png'
    assert seen[0].url.raw_path == b'/v1/artifacts/artifact-char/download?revision=1'


def test_download_ref_revision_cannot_be_overridden() -> None:
    """A different explicit revision conflicts with the exact reference."""
    seen: list[httpx.Request] = []
    client = _client(_serving(seen, _download_headers()))
    with pytest.raises(ValueError, match=r'^artifact reference revision cannot be overridden$'):
        client.artifacts.download(_ref(), revision=2)
    assert client.artifacts.download(_ref(), revision=1).content == _CONTENT
    assert len(seen) == 1


def test_download_requires_a_non_empty_id() -> None:
    """An empty artifact id is refused before any request."""
    seen: list[httpx.Request] = []
    with pytest.raises(
        ValueError,
        match=r'^artifact download requires an ordinary reference or non-empty artifact id$',
    ):
        _client(_serving(seen, _download_headers())).artifacts.download('')
    assert seen == []


@pytest.mark.parametrize(
    ('headers', 'content'),
    [
        pytest.param(_download_headers(**{'content-length': 'many'}), _CONTENT, id='bad-length'),
        pytest.param(_download_headers(**{'content-length': '3'}), _CONTENT, id='short-length'),
        pytest.param(_download_headers(**{'content-type': None}), _CONTENT, id='no-type'),
        pytest.param(_download_headers(**{'content-disposition': None}), _CONTENT, id='no-name'),
        pytest.param(_download_headers(**{'content-length': '0'}), b'', id='empty-body'),
        pytest.param(
            _download_headers(**{'content-disposition': "attachment; filename*=latin-1''x.png"}),
            _CONTENT,
            id='non-utf8-extended-name',
        ),
        pytest.param(
            _download_headers(**{'content-disposition': "attachment; filename*=UTF-8''%FF.png"}),
            _CONTENT,
            id='undecodable-name',
        ),
        pytest.param(
            _download_headers(**{'content-disposition': 'attachment; filename="../x.png"'}),
            _CONTENT,
            id='path-name',
        ),
        pytest.param(
            _download_headers(**{'content-disposition': 'attachment; filename=".."'}),
            _CONTENT,
            id='dot-dot-name',
        ),
        pytest.param(
            _download_headers(**{'content-disposition': "attachment; filename*=UTF-8''a%09b.png"}),
            _CONTENT,
            id='control-character',
        ),
        pytest.param(
            _download_headers(**{'content-disposition': "attachment; filename*=UTF-8''%20a.png"}),
            _CONTENT,
            id='padded-name',
        ),
    ],
)
def test_invalid_download_responses_are_refused(headers: dict[str, str], content: bytes) -> None:
    """Length, type and filename headers must all describe the body safely."""
    seen: list[httpx.Request] = []
    with pytest.raises(ValueError, match=_INVALID):
        _client(_serving(seen, headers, content)).artifacts.download('artifact-1')


@pytest.mark.parametrize(
    'ref',
    [
        pytest.param(_ref(sha256='0' * 64), id='digest'),
        pytest.param(_ref(size_bytes=len(_CONTENT) + 1), id='size'),
    ],
)
def test_download_that_differs_from_its_ref_is_refused(ref: OrdinaryArtifactRef) -> None:
    """Bytes that do not match the reference's digest or size are refused."""
    seen: list[httpx.Request] = []
    with pytest.raises(ValueError, match=_INVALID):
        _client(_serving(seen, _download_headers())).artifacts.download(ref)


def test_download_to_a_directory_leaves_no_temporary_file(tmp_path: Path) -> None:
    """A failed atomic replace removes its temporary file and raises."""
    seen: list[httpx.Request] = []
    destination = tmp_path / 'occupied'
    destination.mkdir()
    (destination / 'keep.txt').write_text('keep', encoding='utf-8')
    with pytest.raises(OSError):  # noqa: PT011 - platform-specific replace failure subtype.
        _client(_serving(seen, _download_headers())).artifacts.download_to(
            'artifact-1', destination
        )
    assert sorted(path.name for path in tmp_path.iterdir()) == ['occupied']
    assert (destination / 'keep.txt').read_text(encoding='utf-8') == 'keep'


@pytest.mark.parametrize(
    ('artifact_id', 'revision'),
    [('', 1), ('artifact-1', 0), ('artifact-1', True)],
)
def test_preview_requires_an_id_and_positive_revision(artifact_id: str, revision: int) -> None:
    """Preview identity is validated before any request."""
    seen: list[httpx.Request] = []
    with pytest.raises(
        ValueError,
        match=r'^artifact preview requires a non-empty artifact id and positive revision$',
    ):
        _client(_serving(seen, {})).artifacts.preview(artifact_id, revision=revision)
    assert seen == []
