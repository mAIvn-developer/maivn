"""Preview transport and local-only value lookup."""

import asyncio
import base64
import json

import httpx
import pytest
from pydantic import ValidationError

from maivn import Client, CoverRegion, PIIWhitelist, PIIWhitelistEntry, PrivateData, RedactedMessage


def test_preview_transport_and_local_values() -> None:
    """Map the request and keep original values local."""

    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        assert request.url.path == '/v1/redaction-previews'
        assert body['prompt']['content'] == 'Hello Alice'
        assert body['known_pii_values'][0]['value'] == 'Alice'
        return httpx.Response(
            200,
            json={
                'preview_id': 'ticket',
                'inputs': [
                    {
                        'kind': 'prompt',
                        'message_index': 0,
                        'status': 'redacted',
                        'redacted_text': 'Hello {_name_}',
                    },
                    {
                        'kind': 'attachment',
                        'message_index': 0,
                        'attachment_id': 'doc',
                        'status': 'redacted',
                        'covered_image': {
                            'inline_base64': base64.b64encode(b'covered').decode(),
                            'media_type': 'image/png',
                            'sha256': 'hash',
                        },
                    },
                ],
                'items': [
                    {
                        'key': 'name',
                        'placeholder': '{_name_}',
                        'pii_type': 'person',
                        'source': 'detected',
                        'input_index': 0,
                        'spans': [{'start': 6, 'end': 11, 'redacted_start': 6, 'redacted_end': 14}],
                    }
                ],
            },
        )

    client = Client(
        api_key='test', base_url='https://example.test', transport=httpx.MockTransport(handler)
    )
    preview = client.preview_redaction(
        RedactedMessage(content='Hello Alice'), known_pii_values=[PrivateData(value='Alice')]
    )
    assert preview.local_values() == {'name': ['Alice']}
    assert 'Alice' not in repr(preview)
    assert 'Alice' not in preview.model_dump_json()
    assert preview.covered_bytes(1) == b'covered'
    assert preview.preview_id == 'ticket'


def test_async_preview() -> None:
    """Call the asynchronous method."""

    def handler(request: httpx.Request) -> httpx.Response:
        assert json.loads(request.content)['prompt'] == 'hello'
        return httpx.Response(200, json={'inputs': []})

    client = Client(
        api_key='test', base_url='https://example.test', transport=httpx.MockTransport(handler)
    )
    result = asyncio.run(client.apreview_redaction('hello'))
    assert result.local_values() == {}


def test_refusal_and_picture_regions_are_mapped() -> None:
    """Preserve value-free refusal details and page-fraction picture boxes."""

    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                'inputs': [
                    {
                        'kind': 'attachment',
                        'message_index': 0,
                        'attachment_id': 'bad',
                        'status': 'refused',
                        'refusal': {'code': 'unshieldable', 'reason': 'Unsupported'},
                    },
                    {
                        'kind': 'attachment',
                        'message_index': 0,
                        'attachment_id': 'picture',
                        'status': 'redacted',
                    },
                ],
                'items': [
                    {
                        'key': 'person_1',
                        'placeholder': '{_person_1_}',
                        'pii_type': 'person',
                        'label': 'signature',
                        'description': 'Signed',
                        'source': 'declared',
                        'confidence': 0.9,
                        'input_index': 1,
                        'regions': [{'page': 2, 'bbox': [0.1, 0.2, 0.3, 0.4]}],
                    }
                ],
            },
        )

    client = Client(
        api_key='test', base_url='https://example.test', transport=httpx.MockTransport(handler)
    )
    result = asyncio.run(client.apreview_redaction('review', resources=['res_1']))
    refusal = result.inputs[0].refusal
    assert result.inputs[0].status == 'refused'
    assert refusal is not None
    assert (refusal.code, refusal.reason) == ('unshieldable', 'Unsupported')
    item = result.items[0]
    assert (item.label, item.description, item.source, item.confidence) == (
        'signature',
        'Signed',
        'declared',
        0.9,
    )
    expected_page = 2
    assert item.regions[0].page == expected_page
    assert item.regions[0].bbox == (0.1, 0.2, 0.3, 0.4)
    assert result.local_values() == {}


def test_local_values_uses_each_original_input() -> None:
    """Spans refer to the correct message or inline text attachment."""

    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        assert len(body['prompt']) == len(messages)
        assert body['attachments'][0]['attachment_id'] == 'text'

        def item(key: str, index: int, end: int) -> dict[str, object]:
            return {
                'key': key,
                'placeholder': '{_' + key + '_}',
                'pii_type': 'person',
                'source': 'detected',
                'input_index': index,
                'spans': [{'start': 0, 'end': end, 'redacted_start': 0, 'redacted_end': 1}],
            }

        return httpx.Response(
            200,
            json={
                'inputs': [
                    {'kind': 'prompt', 'message_index': 0, 'status': 'redacted'},
                    {
                        'kind': 'attachment',
                        'message_index': 0,
                        'attachment_id': 'text',
                        'status': 'redacted',
                        'redacted_text': '{_attachment_}',
                    },
                    {'kind': 'prompt', 'message_index': 1, 'status': 'redacted'},
                    {'kind': 'resource', 'resource_id': 'res_1', 'status': 'unchanged'},
                ],
                'items': [
                    item('first', 0, 5),
                    item('attachment', 1, 3),
                    item('second', 2, 5),
                    item('resource', 3, 3),
                ],
            },
        )

    messages = [RedactedMessage(content='Alice'), RedactedMessage(content='Carol')]
    client = Client(
        api_key='test', base_url='https://example.test', transport=httpx.MockTransport(handler)
    )
    result = client.preview_redaction(
        messages,
        attachments=[
            {
                'filename': 'a.txt',
                'mime_type': 'text/plain',
                'attachment_id': 'text',
                'text_content': 'Bob',
            }
        ],
        resources=['res_1'],
    )
    assert result.local_values() == {'first': ['Alice'], 'attachment': ['Bob'], 'second': ['Carol']}
    for value in ('Alice', 'Bob', 'Carol'):
        assert value not in result.model_dump_json()
        assert value not in repr(result)


def test_preview_forwards_staff_covers_and_labeled_policy() -> None:
    """Sync and async previews preserve covers, labels and whitelist justifications."""
    region = CoverRegion.model_validate(
        {
            'attachment_id': 'doc',
            'page': 2,
            'box': [0.1, 0.2, 0.3, 0.4],
            'key': 'signature',
            'label': 'Signature',
            'description': 'Signed mark',
        }
    )

    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        assert body['cover_regions'] == [
            {
                'attachment_id': 'doc',
                'page': 2,
                'bbox': [0.1, 0.2, 0.3, 0.4],
                'name': 'signature',
                'pii_type': 'other',
                'label': 'Signature',
                'description': 'Signed mark',
            }
        ]
        assert body['known_pii_values'][0]['label'] == 'Candidate'
        assert body['pii_whitelist']['entries'][0]['value'] == 'Alice'
        assert body['pii_whitelist']['entries'][0]['justification'] == 'Allowed for review'
        return httpx.Response(
            200,
            json={
                'inputs': [
                    {
                        'kind': 'attachment',
                        'message_index': 0,
                        'attachment_id': 'doc',
                        'status': 'redacted',
                    }
                ],
                'items': [
                    {
                        'key': 'signature',
                        'placeholder': '{_signature_}',
                        'pii_type': 'other',
                        'label': 'Signature',
                        'source': 'caller_region',
                        'input_index': 0,
                        'regions': [{'page': 2, 'bbox': [0.1, 0.2, 0.3, 0.4]}],
                    }
                ],
            },
        )

    client = Client(
        api_key='test', base_url='https://example.test', transport=httpx.MockTransport(handler)
    )
    whitelist = PIIWhitelist(
        entries=(PIIWhitelistEntry(value='Alice', justification='Allowed for review'),)
    )
    values = [PrivateData(value='Alice', label='Candidate')]
    sync = client.preview_redaction(
        'review',
        cover_regions=[region],
        known_pii_values=values,
        pii_whitelist=whitelist,
    )
    asynchronous = asyncio.run(
        client.apreview_redaction(
            'review',
            cover_regions=[region],
            known_pii_values=values,
            pii_whitelist=whitelist,
        )
    )
    for result in (sync, asynchronous):
        assert result.items[0].source == 'caller_region'
        assert result.items[0].label == 'Signature'
        assert result.items[0].regions[0].page == region.page


def test_staff_cover_and_whitelist_validate_inputs() -> None:
    """Reject out-of-page covers and unjustified value exemptions."""
    with pytest.raises(ValidationError):
        CoverRegion.model_validate({'attachment_id': 'doc', 'page': 0, 'box': [0, 0, 1, 1]})
    with pytest.raises(ValidationError):
        CoverRegion.model_validate({'attachment_id': 'doc', 'page': 1, 'box': [0.8, 0, 0.3, 1]})
    with pytest.raises(ValidationError):
        PIIWhitelistEntry.model_validate({'value': 'Alice'})


def test_preview_timeout_default_and_override() -> None:
    """Both preview methods pass their own timeout to the transport."""
    timeouts: list[float] = []

    def handler(request: httpx.Request) -> httpx.Response:
        timeouts.append(request.extensions['timeout']['read'])
        return httpx.Response(200, json={'inputs': []})

    client = Client(
        api_key='test', base_url='https://example.test', transport=httpx.MockTransport(handler)
    )
    client.preview_redaction('hello')
    asyncio.run(client.apreview_redaction('hello', timeout_seconds=90))
    assert timeouts == [300.0, 90.0]
