"""Approved preview messages reach invoke unchanged or fail before transport."""

from __future__ import annotations

import asyncio
import base64
import hashlib
import json

import httpx
import pytest
from maivn_contracts.messages import Message, TextContentBlock

from maivn import Client, RedactionPreviewResponse, RunOptions


def _preview() -> RedactionPreviewResponse:
    pixels = b'covered pixels'
    return RedactionPreviewResponse.model_validate(
        {
            'preview_id': 'sealed-ticket',
            'inputs': [],
            'approved_request': [
                {
                    'message_id': 'preview-message',
                    'role': 'user',
                    'ts': '2026-10-26T12:00:00Z',
                    'content': [
                        {'type': 'text', 'text': 'Review {_{candidate}_}.  \n'},
                        {'type': 'image', 'attachment_id': 'page', 'mime_type': 'image/png'},
                        {'type': 'text', 'text': 'signature: Handwritten mark'},
                    ],
                    'attachments': [
                        {
                            'attachment_id': 'page',
                            'filename': 'covered.png',
                            'mime_type': 'image/png',
                            'size_bytes': len(pixels),
                            'inline_base64': base64.b64encode(pixels).decode(),
                            'content_hash': hashlib.sha256(pixels).hexdigest(),
                        }
                    ],
                    'redaction': {
                        'known_pii_values': [{'value': '{_{candidate}_}', 'name': 'candidate'}],
                        'pii_whitelist': {
                            'entries': [
                                {'entity_type': 'datetime', 'justification': 'Public review dates'}
                            ]
                        },
                    },
                    'metadata': {'source': 'staff'},
                },
                {
                    'message_id': 'follow-up',
                    'role': 'user',
                    'ts': '2026-10-26T12:00:00Z',
                    'content': 'Return the result.',
                    'redaction': {},
                },
            ],
        }
    )


@pytest.mark.parametrize('asynchronous', [False, True])
def test_approved_copy_reaches_invoke_byte_for_byte(*, asynchronous: bool) -> None:
    """Sync and async runs preserve structured text, legend, attachments and policy."""
    preview = _preview()
    assert preview.approved_request is not None
    expected = [m.model_dump(mode='json', exclude_none=True) for m in preview.approved_request]
    paths: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        paths.append(request.url.path)
        if request.url.path == '/v1/redaction-previews':
            return httpx.Response(200, json=preview.model_dump(mode='json'))
        if request.url.path == '/v1/invoke':
            body = json.loads(request.content)
            assert (
                json.dumps(body['messages'], ensure_ascii=False).encode()
                == json.dumps(expected, ensure_ascii=False).encode()
            )
            assert body['run_config']['redaction_preview_id'] == preview.preview_id
            assert 'private_data' not in body
            return httpx.Response(202, json={'session_id': 'ses-approved', 'stream_position': 0})
        assert request.url.path == '/v1/sessions/ses-approved/events'
        return httpx.Response(
            200,
            headers={'content-type': 'text/event-stream'},
            content=(
                'id: 1\nevent: final\n'
                'data: {"event_id":"evt-final","ordinal":"ord-1","type":"final",'
                '"session_id":"ses-approved","root_event_id":"evt-root",'
                '"payload":{"message":{"message_id":"msg-final","role":"assistant",'
                '"content":"done","ts":"2026-10-26T12:00:00Z"},'
                '"assistant_id":"orchestrator_agent","stop_reason":"end_turn"},'
                '"ts":"2026-10-26T12:00:00Z"}\n\n'
            ),
        )

    client = Client(
        api_key='test', base_url='https://example.test', transport=httpx.MockTransport(handler)
    )
    returned = client.preview_redaction('original locally owned input')
    messages = returned.approved_messages()
    # Helpers return independent copies; edits to one never change another or the response.
    assert messages is not returned.approved_messages()
    assert 'sealed-ticket' not in repr(messages)
    assert 'sealed-ticket' not in messages[0].model_dump_json()
    options = RunOptions(redaction_preview_id=returned.preview_id, user_id='customer')
    response = (
        asyncio.run(client.ainvoke(messages, options=options))
        if asynchronous
        else client.invoke(messages, options=options)
    )
    assert response.response == 'done'
    assert paths == [
        '/v1/redaction-previews',
        '/v1/invoke',
        '/v1/sessions/ses-approved/events',
    ]


@pytest.mark.parametrize('asynchronous', [False, True])
@pytest.mark.parametrize(
    'change',
    [
        'text',
        'pixels',
        'policy',
        'metadata',
        'order',
        'missing',
        'extra',
        'unmarked',
        'ticket',
        'no_ticket',
        'additions',
    ],
)
def test_changed_copy_is_refused_locally(  # noqa: C901 - independent sealed-copy mutations.
    change: str, *, asynchronous: bool
) -> None:
    """Reject edits and wrong approvals without sending any request or exposing inputs."""
    preview = _preview()
    messages = preview.approved_messages()
    ticket = preview.preview_id
    private_data: dict[object, object] = {}
    if change == 'text':
        assert isinstance(messages[0].content, list)
        messages[0].content[0] = TextContentBlock(type='text', text='changed secret')
    elif change == 'pixels':
        assert messages[0].attachments is not None
        messages[0].attachments[0] = (
            messages[0]
            .attachments[0]
            .model_copy(update={'inline_base64': base64.b64encode(b'changed secret').decode()})
        )
    elif change == 'policy':
        messages[0] = messages[0].model_copy(update={'redaction': None})
    elif change == 'metadata':
        assert messages[0].metadata is not None
        messages[0].metadata['source'] = 'changed secret'
    elif change == 'order':
        messages.reverse()
    elif change == 'missing':
        messages.pop()
    elif change == 'extra':
        messages.append(messages[0])
    elif change == 'unmarked':
        messages = [Message.model_validate(m.model_dump()) for m in messages]
    elif change == 'ticket':
        ticket = 'another-ticket'
    elif change == 'no_ticket':
        ticket = None
    else:
        private_data = {'candidate': 'changed secret'}

    def handler(_request: httpx.Request) -> httpx.Response:
        pytest.fail('A mismatched approval reached the transport')

    client = Client(
        api_key='test', base_url='https://example.test', transport=httpx.MockTransport(handler)
    )
    options = RunOptions(redaction_preview_id=ticket, user_id='customer', thread_id='thr-existing')
    with pytest.raises(ValueError, match='redaction_preview_mismatch') as caught:
        (
            asyncio.run(client.ainvoke(messages, options=options, private_data=private_data))
            if asynchronous
            else client.invoke(messages, options=options, private_data=private_data)
        )
    assert 'changed secret' not in str(caught.value)
    assert 'sealed-ticket' not in str(caught.value)


@pytest.mark.parametrize('payload', [{'inputs': []}, {'inputs': [], 'preview_id': 'ticket'}])
def test_missing_approved_copy_is_not_reconstructed(payload: dict[str, object]) -> None:
    """Refused, resource and legacy responses cannot be converted to approved messages."""
    with pytest.raises(ValueError, match='redaction_preview_unavailable'):
        RedactionPreviewResponse.model_validate(payload).approved_messages()


def test_response_mutation_cannot_reapprove_changed_messages() -> None:
    """The helper binds the response as received, before callers can edit nested lists."""
    preview = _preview()
    assert preview.approved_request is not None
    preview.approved_request.reverse()
    with pytest.raises(ValueError, match='redaction_preview_mismatch'):
        preview.approved_messages()
