"""Message helpers for the mAIvn SDK.

The v1 import style is preserved while the wire truth is the v2
``maivn_contracts.messages.Message`` contract.
"""

from __future__ import annotations

import base64
import hashlib
from binascii import Error as Base64Error
from collections.abc import Mapping, Sequence
from datetime import datetime, timezone
from typing import ClassVar, TypeAlias, cast
from uuid import uuid4

from maivn_contracts.artifacts import ArtifactRef
from maivn_contracts.messages import (
    Attachment,
    ContentBlock,
    DocumentContentBlock,
    ImageContentBlock,
    Message,
    MessageRole,
    MetadataValue,
    RedactionConfig,
    TextContentBlock,
    VideoContentBlock,
)
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from maivn._internal.compat.privacy import PrivateData, RedactedMessage


def _new_message_id() -> str:
    return f'msg-{uuid4().hex}'


def _timestamp() -> str:
    return datetime.now(timezone.utc).isoformat().replace('+00:00', 'Z')


AttachmentInput: TypeAlias = Attachment | Mapping[str, object]


class BaseMessage(BaseModel):
    """Convenience SDK message that can be converted to the owned wire contract."""

    model_config = ConfigDict(extra='forbid', frozen=True)

    content: str | list[ContentBlock]
    role: MessageRole
    message_id: str = Field(default_factory=_new_message_id, min_length=1)
    attachments: Sequence[AttachmentInput] | None = None
    artifact_refs: tuple[ArtifactRef, ...] | None = None
    redaction: RedactionConfig | None = None
    metadata: dict[str, MetadataValue] | None = None
    ts: str = Field(default_factory=_timestamp)

    # Subclasses fix this to their own role so BaseMessage.role stays the
    # unnarrowed MessageRole type (a subclass field re-annotated with a
    # narrower Literal is an invariant override under strict pyright since
    # pydantic fields are mutable); the validator below enforces the same
    # per-subtype restriction that the narrower Literal used to.
    _expected_role: ClassVar[MessageRole | None] = None

    @field_validator('attachments', mode='before')
    @classmethod
    def _coerce_attachments(cls, value: object) -> object:
        if value is None:
            return None
        if not isinstance(value, Sequence) or isinstance(value, str | bytes):
            message = 'attachments must be a list of Attachment objects or dictionaries'
            raise TypeError(message)
        return [
            _coerce_attachment(cast('AttachmentInput | object', item))
            for item in cast('Sequence[object]', value)
        ]

    @model_validator(mode='after')
    def _check_expected_role(self) -> BaseMessage:
        expected = self._expected_role
        if expected is not None and self.role != expected:
            message = f"{type(self).__name__}.role must be '{expected}'"
            raise ValueError(message)
        return self

    def to_contract(self) -> Message:
        """Return this SDK message as a v2 owned Message contract.

        An image, PDF or video attached to a plain message is something the
        model must see, so each one gets the content block that carries it to
        the provider. Other attachments (text, spreadsheets, archives) have no
        provider block; the platform keeps them as retrievable resources.
        """
        message = Message.model_validate(self.model_dump(mode='json', exclude_none=True))
        if message.role != 'user' or message.redaction is not None:
            return message
        blocks: list[ContentBlock] = (
            [TextContentBlock(type='text', text=message.content)]
            if isinstance(message.content, str)
            else list(message.content)
        )
        referenced = {
            block.attachment_id
            for block in blocks
            if isinstance(block, (ImageContentBlock, DocumentContentBlock, VideoContentBlock))
        }
        visual: list[ContentBlock] = []
        for item in message.attachments or ():
            if item.attachment_id in referenced:
                continue
            mime_type = item.mime_type.casefold()
            if mime_type.startswith('image/'):
                visual.append(
                    ImageContentBlock(
                        type='image', attachment_id=item.attachment_id, mime_type=item.mime_type
                    )
                )
            elif mime_type == 'application/pdf':
                visual.append(
                    DocumentContentBlock(
                        type='document',
                        attachment_id=item.attachment_id,
                        mime_type='application/pdf',
                        title=item.filename or None,
                    )
                )
            elif mime_type.startswith('video/'):
                visual.append(
                    VideoContentBlock(
                        type='video', attachment_id=item.attachment_id, mime_type=item.mime_type
                    )
                )
        return message.model_copy(update={'content': [*blocks, *visual]}) if visual else message


class HumanMessage(BaseMessage):
    """User-authored message."""

    role: MessageRole = 'user'
    _expected_role: ClassVar[MessageRole | None] = 'user'


class AIMessage(BaseMessage):
    """Assistant-authored message."""

    role: MessageRole = 'assistant'
    _expected_role: ClassVar[MessageRole | None] = 'assistant'


class SystemMessage(BaseMessage):
    """System instruction message."""

    role: MessageRole = 'system'
    _expected_role: ClassVar[MessageRole | None] = 'system'


class ToolMessage(BaseMessage):
    """Tool-result message."""

    role: MessageRole = 'tool'
    _expected_role: ClassVar[MessageRole | None] = 'tool'


SdkMessageInput: TypeAlias = str | Message | BaseMessage | RedactedMessage
SdkMessagesInput: TypeAlias = SdkMessageInput | Sequence[SdkMessageInput]


def to_contract_messages(messages: SdkMessagesInput) -> list[Message]:
    """Normalize SDK message inputs to v2 Message contracts."""
    if isinstance(messages, str | Message | BaseMessage | RedactedMessage):
        return [_to_contract_message(messages)]
    return [_to_contract_message(message) for message in messages]


def _to_contract_message(message: SdkMessageInput) -> Message:
    if isinstance(message, Message):
        return message
    if isinstance(message, BaseMessage | RedactedMessage):
        return message.to_contract()
    return HumanMessage(content=message).to_contract()


def coerce_attachment_input(value: object) -> Attachment:
    """Coerce an attachment object or dictionary to the contract type.

    Shared with ``RedactedMessage`` so both message families accept the same
    attachment dictionary shapes. Not part of the public ``__all__``.
    """
    return _coerce_attachment(value)


def _coerce_attachment(value: object) -> Attachment:
    if isinstance(value, Attachment):
        return value
    if isinstance(value, Mapping):
        return _attachment_from_mapping(cast('Mapping[str, object]', value))
    message = 'attachments entries must be Attachment objects or dictionaries'
    raise TypeError(message)


def _attachment_from_mapping(value: Mapping[str, object]) -> Attachment:
    payload = dict(value)
    if _looks_like_contract_attachment(payload):
        return Attachment.model_validate(payload)

    filename = _required_string(
        payload.get('filename') or payload.get('name'),
        'attachment filename',
    )
    mime_type = _required_string(
        payload.get('mime_type') or 'application/octet-stream',
        'mime_type',
    )
    attachment_id = str(payload.get('attachment_id') or f'att-{uuid4().hex}')

    storage_ref = payload.get('storage_ref')
    if isinstance(storage_ref, Mapping):
        return Attachment.model_validate(
            {
                'attachment_id': attachment_id,
                'filename': filename,
                'mime_type': mime_type,
                'size_bytes': _integer_value(payload.get('size_bytes')),
                'storage_ref': dict(cast('Mapping[str, object]', storage_ref)),
                'content_hash': _required_string(payload.get('content_hash'), 'content_hash'),
                'resource_id': _optional_string(payload.get('resource_id')),
            }
        )

    inline_base64, raw_bytes = _inline_payload(payload)
    return Attachment.model_validate(
        {
            'attachment_id': attachment_id,
            'filename': filename,
            'mime_type': mime_type,
            'size_bytes': _integer_value(payload.get('size_bytes'), default=len(raw_bytes)),
            'inline_base64': inline_base64,
            'content_hash': _optional_string(payload.get('content_hash'))
            or hashlib.sha256(raw_bytes).hexdigest(),
            'resource_id': _optional_string(payload.get('resource_id')),
        }
    )


def _looks_like_contract_attachment(payload: Mapping[str, object]) -> bool:
    required = {'attachment_id', 'filename', 'mime_type', 'size_bytes', 'content_hash'}
    has_source = payload.get('storage_ref') is not None or payload.get('inline_base64') is not None
    return required.issubset(payload) and has_source


def _inline_payload(payload: Mapping[str, object]) -> tuple[str, bytes]:
    encoded = payload.get('inline_base64') or payload.get('content_base64')
    if encoded is not None:
        inline_base64 = _required_string(encoded, 'inline_base64')
        try:
            raw_bytes = base64.b64decode(inline_base64, validate=False)
        except (Base64Error, ValueError) as exc:
            message = 'attachment inline_base64 must be valid base64'
            raise ValueError(message) from exc
        return inline_base64, raw_bytes

    if 'content_bytes' in payload:
        content_bytes = payload['content_bytes']
        if isinstance(content_bytes, bytes | bytearray):
            raw_bytes = bytes(content_bytes)
        else:
            raw_bytes = str(content_bytes).encode('utf-8')
        return base64.b64encode(raw_bytes).decode('ascii'), raw_bytes

    if 'text_content' in payload:
        raw_bytes = str(payload['text_content']).encode('utf-8')
        return base64.b64encode(raw_bytes).decode('ascii'), raw_bytes

    message = (
        'attachment dictionaries require inline_base64, content_base64, content_bytes, '
        'or text_content'
    )
    raise ValueError(message)


def _required_string(value: object, field_name: str) -> str:
    if isinstance(value, str) and value.strip():
        return value
    message = f'{field_name} must be a non-empty string'
    raise ValueError(message)


def _optional_string(value: object) -> str | None:
    if isinstance(value, str) and value.strip():
        return value
    return None


def _integer_value(value: object, *, default: int | None = None) -> int:
    if value is None:
        if default is not None:
            return default
        message = 'size_bytes is required for storage-backed attachments'
        raise ValueError(message)
    if isinstance(value, int):
        return value
    try:
        return int(str(value))
    except ValueError as exc:
        message = 'size_bytes must be an integer'
        raise ValueError(message) from exc


__all__ = [
    'AIMessage',
    'Attachment',
    'BaseMessage',
    'ContentBlock',
    'HumanMessage',
    'Message',
    'PrivateData',
    'RedactedMessage',
    'RedactionConfig',
    'SdkMessageInput',
    'SdkMessagesInput',
    'SystemMessage',
    'ToolMessage',
    'to_contract_messages',
]
