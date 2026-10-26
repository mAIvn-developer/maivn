"""Characterize the invocation image registry: conflicts, outcomes and private custody."""

from __future__ import annotations

import asyncio
import hashlib
from typing import Any, cast

import pytest
from maivn_contracts.artifacts import ArtifactRef, OrdinaryArtifactRef, PrivateArtifactRef

from maivn._internal.artifact_image_context import InvocationArtifactImages
from maivn._internal.models import StreamEvent
from maivn.artifact_images import ArtifactImageUnavailableError, resolve_artifact_image

_PNG = b'\x89PNG-bytes'
_MAX_IMAGE_BYTES = 8 * 1024 * 1024


def _common(artifact_id: str) -> dict[str, object]:
    return {
        'artifact_id': artifact_id,
        'logical_output_id': 'image_output',
        'revision': 1,
        'kind': 'image',
        'mime_type': 'image/png',
        'created_at': '2026-08-23T12:00:00Z',
        'effective_retention': {
            'policy_snapshot_id': 'policy_images',
            'retention_class': 'artifact_30d',
            'expires_at': '2099-09-22T12:00:00Z',
        },
        'producer': {
            'producer_class': 'external_tool',
            'producer_id': 'sdk_local_tool',
            'root_invocation_id': 'invocation_images',
            'session_id': 'session_images',
            'call_id': 'call_images',
        },
    }


def _ordinary(
    artifact_id: str = 'image_one', *, size: int | None = None, **update: object
) -> OrdinaryArtifactRef:
    payload = _common(artifact_id)
    payload.update(
        {
            'custody': 'ordinary',
            'state': 'available',
            'display_filename': 'image.png',
            'size_bytes': len(_PNG) if size is None else size,
            'sha256': hashlib.sha256(_PNG).hexdigest(),
            'validation_receipt': {
                'receipt_id': 'receipt_images',
                'status': 'validated',
                'validator_profile': 'image-validator-v1',
                'validator_version': '1',
            },
            'safe_preview': {'kind': 'image', 'width_pixels': 1, 'height_pixels': 1},
            'retrieval_action': {'relation': 'artifact.download_authorization'},
        }
    )
    payload.update(update)
    return OrdinaryArtifactRef.model_validate(payload)


def _private(artifact_id: str = 'private_image') -> PrivateArtifactRef:
    payload = _common(artifact_id)
    payload.update(
        {
            'custody': 'vault_private',
            'state': 'available_in_vault',
            'display_label': 'Private image',
            'producer': {
                'producer_class': 'vault',
                'producer_id': 'vault_renderer',
                'root_invocation_id': 'invocation_images',
                'session_id': 'session_images',
            },
            'creation_receipt_id': 'creation_receipt',
            'retrieval_action': {'relation': 'artifact.vault_download_authorization'},
        }
    )
    return PrivateArtifactRef.model_validate(payload)


class _Download:
    def __init__(self, content: bytes) -> None:
        self.content = content


class _Ordinary:
    def __init__(self, content: bytes = _PNG) -> None:
        self.content = content
        self.seen: list[ArtifactRef] = []

    async def adownload(self, ref: ArtifactRef) -> _Download:
        self.seen.append(ref)
        return _Download(self.content)


class _Private:
    def __init__(self, content: bytes = _PNG) -> None:
        self.content = content
        self.seen: list[tuple[ArtifactRef, str]] = []

    async def adownload(self, ref: ArtifactRef, *, session_id: str) -> bytes:
        self.seen.append((ref, session_id))
        return self.content


def _registry(
    ordinary: object = None, private: object = None, session_id: str = 'session-now'
) -> InvocationArtifactImages:
    return InvocationArtifactImages(
        ordinary=cast('Any', ordinary or _Ordinary()),
        private=cast('Any', private),
        session_id=session_id,
    )


def _resolve(registry: InvocationArtifactImages, artifact_id: str) -> object:
    async def run() -> object:
        with registry.activate():
            return await resolve_artifact_image(artifact_id)

    return asyncio.run(run())


def _completion(event_type: str, outcome: object) -> StreamEvent:
    return StreamEvent(position=1, event_type=event_type, data={'payload': {'outcome': outcome}})


def test_conflicting_refs_stay_unresolvable_even_if_reregistered() -> None:
    """Once an ID conflicts, neither version nor a later identical ref is accepted."""
    first = _ordinary()
    second = first.model_copy(update={'sha256': hashlib.sha256(b'other').hexdigest()})
    registry = _registry()
    registry.register((first, first))
    assert registry.references == (first,)
    registry.register((second,))
    assert registry.references == ()
    registry.register((first,))
    assert registry.references == ()
    with pytest.raises(ArtifactImageUnavailableError):
        _resolve(registry, first.artifact_id)


def test_private_image_downloads_through_the_vault_with_the_session() -> None:
    """Private refs resolve through the private client and report private custody."""
    private = _Private()
    ref = _private()
    registry = _registry(private=private, session_id='session-42')
    registry.register((ref,))
    image = _resolve(registry, ref.artifact_id)
    assert getattr(image, 'content', None) == _PNG
    assert getattr(image, 'private', None) is True
    assert getattr(image, 'mime_type', None) == 'image/png'
    assert getattr(image, 'artifact_ref', None) == ref
    assert private.seen == [(ref, 'session-42')]


def test_private_image_without_a_vault_client_is_unavailable() -> None:
    """A private ref cannot resolve when the invocation has no private client."""
    registry = _registry(private=None)
    registry.register((_private(),))
    with pytest.raises(ArtifactImageUnavailableError):
        _resolve(registry, 'private_image')


def test_ordinary_image_resolves_with_ordinary_custody() -> None:
    """An ordinary registered PNG resolves to its exact bytes, not private."""
    ordinary = _Ordinary()
    ref = _ordinary()
    registry = _registry(ordinary=ordinary)
    registry.register((ref,))
    image = _resolve(registry, ref.artifact_id)
    assert getattr(image, 'private', None) is False
    assert getattr(image, 'content', None) == _PNG
    assert ordinary.seen == [ref]


@pytest.mark.parametrize(
    ('ref', 'content'),
    [
        pytest.param(_ordinary(size=_MAX_IMAGE_BYTES + 1), _PNG, id='declared-too-large'),
        pytest.param(_ordinary(size=_MAX_IMAGE_BYTES), _PNG, id='declared-at-limit-ok'),
        pytest.param(_ordinary(), b'', id='empty-download'),
        pytest.param(
            _ordinary(
                kind='document',
                mime_type='application/pdf',
                safe_preview={'kind': 'document', 'page_count': 1},
            ),
            _PNG,
            id='not-an-image',
        ),
    ],
)
def test_ordinary_image_limits(ref: OrdinaryArtifactRef, content: bytes) -> None:
    """Declared size, empty bytes and non-image kinds all gate resolution."""
    ordinary = _Ordinary(content)
    registry = _registry(ordinary=ordinary)
    registry.register((ref,))
    if ref.size_bytes == _MAX_IMAGE_BYTES and content:
        assert getattr(_resolve(registry, ref.artifact_id), 'content', None) == content
        return
    with pytest.raises(ArtifactImageUnavailableError):
        _resolve(registry, ref.artifact_id)


def test_oversized_private_download_is_unavailable() -> None:
    """Downloaded bytes above eight MiB are refused after download."""
    registry = _registry(private=_Private(b'x' * (_MAX_IMAGE_BYTES + 1)))
    registry.register((_private(),))
    with pytest.raises(ArtifactImageUnavailableError):
        _resolve(registry, 'private_image')


def test_download_failure_hides_the_transport_error() -> None:
    """Transport failures become the value-free unavailable error with no cause."""

    class Failing:
        async def adownload(self, _ref: ArtifactRef) -> _Download:
            message = 'https://signed.example/?token=secret'
            raise RuntimeError(message)

    registry = _registry(ordinary=Failing())
    registry.register((_ordinary(),))
    with pytest.raises(ArtifactImageUnavailableError) as caught:
        _resolve(registry, 'image_one')
    assert caught.value.__cause__ is None
    assert caught.value.__suppress_context__ is True
    assert 'secret' not in str(caught.value)


@pytest.mark.parametrize(
    ('event_type', 'outcome'),
    [
        pytest.param('system_tool_start', {'status': 'ok'}, id='other-event'),
        pytest.param('system_tool_complete', ['not', 'a', 'dict'], id='non-mapping'),
        pytest.param('system_tool_complete', {'status': 'unknown'}, id='invalid-outcome'),
    ],
)
def test_observe_ignores_unusable_completion_events(event_type: str, outcome: object) -> None:
    """Only a valid typed outcome on completion events can register refs."""
    registry = _registry()
    registry.observe(_completion(event_type, outcome))
    assert registry.references == ()


def test_observe_registers_refs_from_an_ok_outcome_without_a_result() -> None:
    """An ok outcome missing its result still registers its typed artifact refs."""
    ref = _ordinary()
    registry = _registry()
    registry.observe(
        _completion(
            'system_tool_complete',
            {
                'call_id': 'call_images',
                'duration_ms': 1,
                'status': 'ok',
                'artifact_refs': [ref.model_dump(mode='json')],
            },
        )
    )
    assert registry.references == (ref,)
