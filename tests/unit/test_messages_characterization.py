"""Characterize SDK message helpers: role checks and attachment dictionary coercion."""

from __future__ import annotations

import base64
import hashlib
import re

import pytest
from pydantic import ValidationError

from maivn.messages import (
    AIMessage,
    Attachment,
    BaseMessage,
    HumanMessage,
    Message,
    SystemMessage,
    ToolMessage,
    coerce_attachment_input,
    to_contract_messages,
)

DIGEST = hashlib.sha256(b'hello').hexdigest()
STORAGE = {'bucket': 'attachments', 'path': 'p/hello.txt', 'sha256': DIGEST}


def _only(message: HumanMessage) -> Attachment:
    attachments = message.attachments
    assert attachments is not None
    assert len(attachments) == 1
    item = attachments[0]
    assert isinstance(item, Attachment)
    return item


def _value_error(error: pytest.ExceptionInfo[ValidationError]) -> str:
    details = error.value.errors()
    assert len(details) == 1
    return str(details[0]['msg'])


# --- roles --------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ('kind', 'role', 'wrong'),
    [
        (HumanMessage, 'user', 'assistant'),
        (AIMessage, 'assistant', 'user'),
        (SystemMessage, 'system', 'tool'),
        (ToolMessage, 'tool', 'system'),
    ],
)
def test_each_message_class_fixes_its_role(
    kind: type[HumanMessage | AIMessage | SystemMessage | ToolMessage],
    role: str,
    wrong: str,
) -> None:
    """Subclasses default to their role and refuse any other with a named error."""
    assert kind(content='hi').role == role
    with pytest.raises(ValidationError) as refused:
        _ = kind(content='hi', role=wrong)  # pyright: ignore[reportArgumentType]
    assert _value_error(refused) == f"Value error, {kind.__name__}.role must be '{role}'"


def test_base_message_accepts_any_role_and_generates_identity() -> None:
    """The base class has no fixed role; ids and timestamps are generated per message."""
    message = BaseMessage(content='hi', role='tool')
    assert message.role == 'tool'
    assert re.fullmatch(r'msg-[0-9a-f]{32}', message.message_id)
    assert re.fullmatch(r'\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d(\.\d+)?Z', message.ts)
    assert BaseMessage(content='hi', role='tool').message_id != message.message_id


# --- attachment containers ------------------------------------------------------------------


@pytest.mark.parametrize('value', ['a.txt', b'a.txt', 7, {'filename': 'a.txt'}])
def test_attachments_must_be_a_list(value: object) -> None:
    """Strings, bytes, numbers and single dictionaries are not attachment lists."""
    with pytest.raises(
        TypeError, match=r'^attachments must be a list of Attachment objects or dictionaries$'
    ):
        _ = HumanMessage(content='hi', attachments=value)  # pyright: ignore[reportArgumentType]


@pytest.mark.parametrize('entry', ['a.txt', 7, None, ['nested']])
def test_attachment_entries_must_be_objects_or_dictionaries(entry: object) -> None:
    """Each entry is an Attachment or a mapping; anything else is a type error."""
    with pytest.raises(
        TypeError, match=r'^attachments entries must be Attachment objects or dictionaries$'
    ):
        _ = HumanMessage(content='hi', attachments=[entry])  # pyright: ignore[reportArgumentType]


def test_attachment_objects_pass_through_unchanged() -> None:
    """An Attachment instance is kept as given, and None means no attachments."""
    attachment = Attachment(
        attachment_id='att-1',
        filename='hello.txt',
        mime_type='text/plain',
        size_bytes=5,
        inline_base64='aGVsbG8=',
        content_hash=DIGEST,
    )
    assert _only(HumanMessage(content='hi', attachments=[attachment])) == attachment
    assert HumanMessage(content='hi', attachments=None).attachments is None
    assert coerce_attachment_input(attachment) is attachment


def test_contract_shaped_dictionary_is_validated_as_is() -> None:
    """A dictionary already in contract form is validated without filling defaults."""
    payload = {
        'attachment_id': 'att-contract',
        'filename': 'hello.txt',
        'mime_type': 'text/plain',
        'size_bytes': 999,
        'inline_base64': 'aGVsbG8=',
        'content_hash': DIGEST,
    }
    assert coerce_attachment_input(payload) == Attachment.model_validate(payload)


# --- inline dictionaries --------------------------------------------------------------------


@pytest.mark.parametrize(
    'source',
    [
        {'content_bytes': b'hello'},
        {'content_bytes': bytearray(b'hello')},
        {'content_bytes': 'hello'},
        {'text_content': 'hello'},
        {'content_base64': 'aGVsbG8='},
        {'inline_base64': 'aGVsbG8='},
    ],
)
def test_inline_sources_become_base64_with_size_and_hash(source: dict[str, object]) -> None:
    """Every inline spelling yields the same base64, byte size and SHA-256."""
    item = coerce_attachment_input({'filename': 'hello.txt', 'mime_type': 'text/plain', **source})
    assert item.inline_base64 == 'aGVsbG8='
    assert item.size_bytes == len(b'hello')
    assert item.content_hash == DIGEST
    assert item.storage_ref is None
    assert item.resource_id is None
    assert re.fullmatch(r'att-[0-9a-f]{32}', item.attachment_id)


def test_inline_defaults_and_explicit_overrides() -> None:
    """`name` stands in for filename, mime defaults to octet-stream, explicit values win."""
    item = coerce_attachment_input(
        {
            'name': 'blob.bin',
            'mime_type': '',
            'text_content': 'hello',
            'attachment_id': 'att-chosen',
            'size_bytes': '42',
            'content_hash': 'f' * 64,
            'resource_id': 'res-1',
        }
    )
    assert (item.filename, item.mime_type, item.attachment_id) == (
        'blob.bin',
        'application/octet-stream',
        'att-chosen',
    )
    assert (item.size_bytes, item.content_hash, item.resource_id) == (42, 'f' * 64, 'res-1')


def test_blank_optional_strings_fall_back_to_computed_values() -> None:
    """Whitespace-only hash and resource id are treated as absent."""
    item = coerce_attachment_input(
        {'filename': 'a.txt', 'text_content': 'hello', 'content_hash': '  ', 'resource_id': ' '}
    )
    assert item.content_hash == DIGEST
    assert item.resource_id is None


def test_inline_base64_is_preferred_over_other_sources() -> None:
    """When several sources are present, inline_base64 is the one kept."""
    item = coerce_attachment_input(
        {'filename': 'a.txt', 'inline_base64': 'aGVsbG8=', 'text_content': 'other'}
    )
    assert item.inline_base64 == 'aGVsbG8='
    assert base64.b64decode(item.inline_base64) == b'hello'


@pytest.mark.parametrize(
    ('payload', 'message'),
    [
        ({'text_content': 'x'}, 'attachment filename must be a non-empty string'),
        ({'filename': '  ', 'text_content': 'x'}, 'attachment filename must be a non-empty string'),
        ({'filename': 7, 'text_content': 'x'}, 'attachment filename must be a non-empty string'),
        (
            {'filename': 'a', 'mime_type': ' ', 'text_content': 'x'},
            'mime_type must be a non-empty string',
        ),
        ({'filename': 'a', 'inline_base64': 7}, 'inline_base64 must be a non-empty string'),
        (
            {'filename': 'a', 'inline_base64': 'abc'},
            'attachment inline_base64 must be valid base64',
        ),
        (
            {'filename': 'a'},
            'attachment dictionaries require inline_base64, content_base64, content_bytes, '
            'or text_content',
        ),
        (
            {'filename': 'a', 'text_content': 'x', 'size_bytes': 'big'},
            'size_bytes must be an integer',
        ),
    ],
)
def test_unusable_inline_dictionaries_are_refused(payload: dict[str, object], message: str) -> None:
    """Each missing or malformed field is named; the shared helper raises it directly."""
    with pytest.raises(ValueError, match=f'^{re.escape(message)}$'):
        _ = coerce_attachment_input(payload)
    with pytest.raises(ValidationError) as refused:
        _ = HumanMessage(content='hi', attachments=[payload])
    assert _value_error(refused) == f'Value error, {message}'


# --- storage-backed dictionaries ------------------------------------------------------------


def test_storage_backed_dictionary_keeps_its_reference() -> None:
    """A storage reference is kept as given; size and hash come from the caller."""
    item = coerce_attachment_input(
        {
            'filename': 'hello.txt',
            'mime_type': 'text/plain',
            'storage_ref': STORAGE,
            'size_bytes': 5,
            'content_hash': DIGEST,
        }
    )
    assert item.storage_ref is not None
    assert item.storage_ref.model_dump() == STORAGE
    assert (item.size_bytes, item.content_hash, item.inline_base64) == (5, DIGEST, None)


@pytest.mark.parametrize(
    ('extra', 'message'),
    [
        ({'content_hash': DIGEST}, 'size_bytes is required for storage-backed attachments'),
        ({'content_hash': DIGEST, 'size_bytes': 'five'}, 'size_bytes must be an integer'),
        ({'size_bytes': 5}, 'content_hash must be a non-empty string'),
    ],
)
def test_storage_backed_dictionary_requires_size_and_hash(
    extra: dict[str, object], message: str
) -> None:
    """Bytes in storage cannot be measured locally, so size and hash are mandatory."""
    with pytest.raises(ValueError, match=f'^{re.escape(message)}$'):
        _ = coerce_attachment_input({'filename': 'a.txt', 'storage_ref': STORAGE, **extra})


# --- normalization --------------------------------------------------------------------------


def test_to_contract_messages_normalizes_every_input_kind() -> None:
    """Strings become user messages, contracts pass through, SDK messages convert."""
    contract = Message.model_validate(
        {'message_id': 'msg-given', 'role': 'assistant', 'content': 'earlier', 'ts': 'now'}
    )
    single = to_contract_messages('hello')
    assert [(item.role, item.content) for item in single] == [('user', 'hello')]

    mixed = to_contract_messages(['first', contract, SystemMessage(content='rules')])
    assert [(item.role, item.content) for item in mixed] == [
        ('user', 'first'),
        ('assistant', 'earlier'),
        ('system', 'rules'),
    ]
    assert mixed[1] is contract
    assert to_contract_messages(contract) == [contract]
