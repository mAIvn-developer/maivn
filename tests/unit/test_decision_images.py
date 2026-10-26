"""Public image Decisions use the authenticated platform path without losing evidence."""

# ruff: noqa: D103, PLR2004 - behavioral tests.
from __future__ import annotations

import pytest
from pydantic import ValidationError

from maivn.decisions import (
    DecisionAnswer,
    DecisionChoice,
    DecisionClient,
    DecisionConfig,
    DecisionImage,
    DecisionRequest,
    DecisionStatus,
)
from tests.unit.decision_platform import choosing

PNG = b'\x89PNG\r\n\x1a\n' + b'public image fixture'


def _request(image: DecisionImage | None = None, name: str = 'route') -> DecisionRequest:
    return DecisionRequest(
        name=name,
        state='Classify this public image',
        instructions='Choose',
        privacy='public',
        images=() if image is None else (image,),
        choices=(
            DecisionChoice(key='yes', description='yes'),
            DecisionChoice(key='no', description='no'),
        ),
    )


async def _baseline(request: DecisionRequest) -> DecisionAnswer:
    return DecisionAnswer(value=request.choices[0].key, provider='local', token_category=None)


def test_authorization_sees_exact_image_and_wire_retains_it() -> None:
    image = DecisionImage.from_bytes(PNG, 'image/png')
    platform = choosing(lambda _q, _s: ('no', 0.95))
    approved: list[DecisionRequest] = []

    def authorize(request: DecisionRequest) -> bool:
        approved.append(request)
        return request.images == (image,)

    client = DecisionClient(
        fallback=_baseline, config=DecisionConfig(), authorize=authorize, client=platform.client()
    )
    result = client.decide(_request(image))
    assert result.source == 'primary'
    assert approved[0].images == (image,)
    assert platform.calls[0]['images'] == [image.model_dump(mode='json')]


def test_refused_image_never_leaves_process() -> None:
    platform = choosing(lambda _q, _s: ('no', 0.95))
    client = DecisionClient(
        fallback=_baseline,
        config=DecisionConfig(),
        authorize=lambda _request: False,
        client=platform.client(),
    )
    result = client.decide(_request(DecisionImage.from_bytes(PNG, 'image/png')))
    assert result.primary.status == DecisionStatus.BLOCKED
    assert not platform.calls


@pytest.mark.parametrize('native_batch', [False, True])
def test_image_private_values_are_blocked_before_authorization(*, native_batch: bool) -> None:
    platform = choosing(lambda _q, _s: ('no', 0.95))

    def authorize(_request: DecisionRequest) -> bool:
        pytest.fail('private image must be blocked before authorization or transport')

    client = DecisionClient(
        fallback=_baseline, config=DecisionConfig(), authorize=authorize, client=platform.client()
    )
    request = _request(DecisionImage.from_bytes(PNG, 'image/png')).model_copy(
        update={'private_data': (('owner', 'private person'),)}
    )
    (result,) = client.batch([request], native_batch=native_batch)
    assert result.primary.status == DecisionStatus.BLOCKED
    assert not platform.calls


def test_native_batch_shares_only_identical_image_evidence() -> None:
    platform = choosing(lambda _q, _s: ('no', 0.95))
    client = DecisionClient(
        fallback=_baseline,
        config=DecisionConfig(),
        authorize=lambda _request: True,
        client=platform.client(),
    )
    image = DecisionImage.from_bytes(PNG, 'image/png')
    other = DecisionImage.from_bytes(PNG + b'other', 'image/png')
    results = client.batch(
        [_request(image, 'a'), _request(image, 'b'), _request(other, 'c')], native_batch=True
    )
    assert len(platform.calls) == 2
    assert sorted(len(call['questions']) for call in platform.calls) == [1, 2]
    assert all(result.source == 'primary' for result in results)


def test_empty_images_preserve_legacy_serialization_and_count_is_bounded() -> None:
    assert 'images' not in _request().model_dump()
    image = DecisionImage.from_bytes(PNG, 'image/png')
    with pytest.raises(ValidationError):
        DecisionRequest.model_validate({**_request().model_dump(), 'images': (image,) * 5})
