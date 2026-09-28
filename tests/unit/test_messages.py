"""Unit tests for SDK-owned message helpers."""

from __future__ import annotations

from typing import Any

from maivn_contracts.messages import (
    DocumentContentBlock,
    ImageContentBlock,
    RedactionConfig,
    VideoContentBlock,
)

from maivn.messages import HumanMessage, to_contract_messages


def test_browser_video_attachment_becomes_native_video_content() -> None:
    """Studio-style attachment dictionaries retain native visual content."""
    contract = HumanMessage(
        content='Describe the clip',
        attachments=[{'name': 'clip.mp4', 'mime_type': 'video/mp4', 'content_base64': 'YWJj'}],
    ).to_contract()
    assert not isinstance(contract.content, str)
    video = contract.content[-1]
    assert isinstance(video, VideoContentBlock)
    assert contract.attachments
    assert video.attachment_id == contract.attachments[0].attachment_id
    assert HumanMessage.model_validate(contract.model_dump()).to_contract() == contract


def test_image_attachment_becomes_image_content_the_model_can_see() -> None:
    """An attached image is meant to be looked at, not filed away unseen."""
    contract = HumanMessage(
        content='How many red circles are in this picture?',
        attachments=[{'name': 'shapes.png', 'mime_type': 'image/png', 'content_base64': 'YWJj'}],
    ).to_contract()
    assert not isinstance(contract.content, str)
    image = contract.content[-1]
    assert isinstance(image, ImageContentBlock)
    assert contract.attachments
    assert image.attachment_id == contract.attachments[0].attachment_id
    assert image.mime_type == 'image/png'
    assert HumanMessage.model_validate(contract.model_dump()).to_contract() == contract


def test_pdf_attachment_becomes_document_content_titled_by_filename() -> None:
    """A PDF reaches the provider as a document block named after the file."""
    contract = HumanMessage(
        content='Summarise the contract',
        attachments=[
            {'name': 'lease.pdf', 'mime_type': 'application/pdf', 'content_base64': 'YWJj'}
        ],
    ).to_contract()
    assert not isinstance(contract.content, str)
    document = contract.content[-1]
    assert isinstance(document, DocumentContentBlock)
    assert document.title == 'lease.pdf'
    assert contract.attachments
    assert document.attachment_id == contract.attachments[0].attachment_id


def test_text_attachment_gets_no_content_block() -> None:
    """There is no provider block for plain text; the platform keeps it as a resource."""
    contract = HumanMessage(
        content='Read the notes',
        attachments=[{'name': 'notes.txt', 'mime_type': 'text/plain', 'text_content': 'hi'}],
    ).to_contract()
    assert contract.content == 'Read the notes'
    assert contract.attachments


def test_string_messages_become_user_messages() -> None:
    """Strings are accepted as the common single user-message shorthand."""
    messages = to_contract_messages('hello')

    assert len(messages) == 1
    assert messages[0].role == 'user'
    assert messages[0].content == 'hello'


def test_video_normalization_preserves_requested_redaction_boundary() -> None:
    """A shielded upload is left for server redaction rather than native forwarding."""
    contract = HumanMessage(
        content='Review privately',
        redaction=RedactionConfig(),
        attachments=[{'name': 'clip.mp4', 'mime_type': 'video/mp4', 'content_base64': 'YWJj'}],
    ).to_contract()
    assert contract.content == 'Review privately'
    assert contract.redaction is not None
    assert contract.attachments


def test_sequence_messages_preserve_order_and_metadata() -> None:
    """Mixed SDK message inputs are normalized without changing order."""
    first = HumanMessage(message_id='msg-1', content='first', metadata={'source': 'test'})
    second = HumanMessage(message_id='msg-2', content='second')

    messages = to_contract_messages([first, second])

    assert [message.message_id for message in messages] == ['msg-1', 'msg-2']
    assert messages[0].metadata == {'source': 'test'}


def test_human_message_accepts_v1_dict_attachments_and_none() -> None:
    """HumanMessage accepts v1 attachment dicts and nullable attachment input."""
    message = HumanMessage(
        content='review this attachment',
        attachments=[
            {
                'name': 'release-gate-policy.txt',
                'mime_type': 'text/plain',
                'text_content': 'ship only after release gates pass',
                'sharing_scope': 'project',
                'tags': ['release-gate'],
            }
        ],
    )

    contract = message.to_contract()
    nullable = HumanMessage(content='no attachments', attachments=None).to_contract()

    assert contract.attachments is not None
    assert contract.attachments[0].filename == 'release-gate-policy.txt'
    assert (
        contract.attachments[0].inline_base64 == 'c2hpcCBvbmx5IGFmdGVyIHJlbGVhc2UgZ2F0ZXMgcGFzcw=='
    )
    assert nullable.attachments is None


def test_human_message_accepts_nullable_plain_dict_attachment_variable() -> None:
    """The public constructor accepts the nullable plain-dict shape used by demos."""
    attachments: list[dict[str, Any]] | None = [
        {
            'filename': 'debug-note.txt',
            'mime_type': 'text/plain',
            'text_content': 'typed compatibility',
        }
    ]

    message = HumanMessage(content='typed attachment', attachments=attachments)

    assert message.to_contract().attachments is not None
