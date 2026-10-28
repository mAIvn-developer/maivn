"""Private-data and redaction request models.

These are technical privacy controls, not certifications. mAIvn holds no
SOC 2, HIPAA, ISO 27001, or FedRAMP certification. In particular,
``phi_mode`` limits HIPAA Safe Harbor whitelist categories; it does not make
a deployment HIPAA-compliant, provide a BAA, or replace required audits and
organizational controls. Developers must evaluate and use these controls at
their own risk under the mAIvn Platform Disclaimer.
"""

from __future__ import annotations

import base64
import hashlib
import json
import re
from collections.abc import Generator, Mapping, Sequence
from contextlib import contextmanager, suppress
from contextvars import ContextVar
from datetime import datetime, timezone
from typing import Any, ClassVar, cast
from uuid import uuid4

from maivn_contracts.artifacts import ArtifactRef
from maivn_contracts.messages import (
    Attachment,
    ContentBlock,
    CoverRegion,
    KnownPrivateData,
    Message,
    MetadataValue,
    RedactionConfig,
)
from maivn_contracts.messages import (
    PIIWhitelist as ContractPIIWhitelist,
)
from maivn_contracts.messages.redaction_preview import (
    RedactionPreviewRequest,
)
from maivn_contracts.messages.redaction_preview import (
    RedactionPreviewResponse as ContractPreviewResponse,
)
from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    PrivateAttr,
    SerializerFunctionWrapHandler,
    field_serializer,
    field_validator,
    model_serializer,
    model_validator,
)

_MIN_WHITELIST_VALUE_LENGTH = 3

HIPAA_SAFE_HARBOR_CATEGORIES: frozenset[str] = frozenset(
    {
        'person',
        'ssn',
        'phone',
        'fax',
        'email',
        'credit_card',
        'iban',
        'swift',
        'account_id',
        'medical_record_number',
        'health_plan_id',
        'certificate_id',
        'license_id',
        'vehicle_id',
        'device_id',
        'biometric_id',
        'ip_address',
        'url',
        'date',
        'datetime',
    },
)


PRIVATE_DESCRIPTOR_KEY = '__private__'
_REDACTED_VALUE = '<redacted>'
_descriptors_only: ContextVar[bool] = ContextVar('maivn_private_data_descriptors', default=False)


@contextmanager
def private_data_descriptors_only() -> Generator[None, None, None]:
    """Within this block every pydantic dump shows ``PrivateData`` as its descriptor.

    The event bridge serializes inside it, so a ``PrivateData`` reached by any dump -
    a model's, a private attribute exposed by a serializer, a non-pydantic object whose
    ``model_dump()`` delegates to pydantic - never shows its value.
    """
    token = _descriptors_only.set(True)
    try:
        yield
    finally:
        _descriptors_only.reset(token)


def _new_message_id() -> str:
    return f'msg-{uuid4().hex}'


def _timestamp() -> str:
    return datetime.now(timezone.utc).isoformat().replace('+00:00', 'Z')


class PrivateData(BaseModel):
    """Structured known PII descriptor for enhanced redaction."""

    model_config: ClassVar[ConfigDict] = ConfigDict(frozen=True, extra='ignore')

    # repr=False: a log line or traceback that formats the model never shows the value.
    value: object = Field(repr=False)
    name: str | None = None
    pii_type: str | None = None
    label: str | None = None
    description: str | None = None
    format: str | None = None

    @model_serializer(mode='wrap')
    def _dump_descriptor_on_request(  # noqa: ANN202 - an annotation would change the schema.
        self,
        handler: SerializerFunctionWrapHandler,
    ):
        """Dump as the value-free descriptor inside :func:`private_data_descriptors_only`."""
        if _descriptors_only.get():
            return private_data_descriptor(self)
        return handler(self)

    @field_serializer('value', mode='wrap')
    def _hide_value_on_request(  # noqa: ANN202 - an annotation would change the schema.
        self,
        value: object,
        handler: SerializerFunctionWrapHandler,
    ):
        """Mask the value too, for a subclass whose own model serializer replaces the above."""
        return _REDACTED_VALUE if _descriptors_only.get() else handler(value)


def private_data_descriptor(item: PrivateData) -> dict[str, object]:
    """Return a displayable descriptor for ``item``. NEVER includes ``value``."""
    return {
        PRIVATE_DESCRIPTOR_KEY: True,
        'label': item.label or item.name or item.pii_type or 'private data',
        'pii_type': item.pii_type,
    }


class PIIWhitelistEntry(BaseModel):
    """A single whitelist rule for detected PII spans."""

    model_config: ClassVar[ConfigDict] = ConfigDict(frozen=True, extra='forbid')

    entity_type: str | None = None
    pattern: str | None = None
    value: str | None = Field(default=None, repr=False)
    justification: str = Field(..., min_length=8, max_length=512)
    label: str | None = Field(default=None, max_length=128)

    @model_validator(mode='after')
    def _exactly_one_kind(self) -> PIIWhitelistEntry:
        provided = sum(
            1 for value in (self.entity_type, self.pattern, self.value) if value is not None
        )
        if provided != 1:
            message = 'PIIWhitelistEntry requires exactly one of entity_type, pattern, or value'
            raise ValueError(message)
        return self

    @field_validator('entity_type')
    @classmethod
    def _normalize_entity_type(cls, value: str | None) -> str | None:
        if value is None:
            return None
        normalized = value.strip().lower()
        if not normalized:
            message = 'entity_type cannot be blank'
            raise ValueError(message)
        return normalized

    @field_validator('pattern')
    @classmethod
    def _validate_pattern(cls, value: str | None) -> str | None:
        if value is None:
            return None
        if not value.strip():
            message = 'pattern cannot be blank'
            raise ValueError(message)
        try:
            _ = re.compile(value, re.IGNORECASE)
        except re.error as exc:
            message = f'invalid regex pattern: {exc}'
            raise ValueError(message) from exc
        return value

    @field_validator('value')
    @classmethod
    def _validate_value(cls, value: str | None) -> str | None:
        if value is None:
            return None
        candidate = value.strip()
        if len(candidate) < _MIN_WHITELIST_VALUE_LENGTH:
            message = 'value must be at least 3 characters after stripping'
            raise ValueError(message)
        return candidate


class PIIWhitelist(BaseModel):
    """Collection of PII whitelist entries plus privacy-control flags.

    ``phi_mode`` is a technical Safe Harbor category guard, not HIPAA
    certification or a compliance guarantee. mAIvn holds no SOC 2, HIPAA,
    ISO 27001, or FedRAMP certification and cannot provide a BAA; use this
    control at your own risk under the mAIvn Platform Disclaimer.
    """

    model_config: ClassVar[ConfigDict] = ConfigDict(frozen=True, extra='forbid')

    entries: tuple[PIIWhitelistEntry, ...] = ()
    phi_mode: bool = False

    @field_validator('entries', mode='before')
    @classmethod
    def _coerce_entries(cls, value: object) -> tuple[PIIWhitelistEntry, ...]:
        if value is None:
            return ()
        if isinstance(value, PIIWhitelistEntry):
            return (value,)
        if isinstance(value, list | tuple):
            entries: list[PIIWhitelistEntry] = []
            for item in cast('Sequence[object]', value):
                if isinstance(item, PIIWhitelistEntry):
                    entries.append(item)
                    continue
                if isinstance(item, dict):
                    entries.append(PIIWhitelistEntry.model_validate(item))
                    continue
                message = 'entries must contain PIIWhitelistEntry or dict items'
                raise TypeError(message)
            return tuple(entries)
        message = 'entries must be a list, tuple, PIIWhitelistEntry, or None'
        raise TypeError(message)

    @model_validator(mode='after')
    def _enforce_phi_mode(self) -> PIIWhitelist:
        if not self.phi_mode:
            return self
        blocked = [
            entry.entity_type
            for entry in self.entries
            if entry.entity_type is not None and entry.entity_type in HIPAA_SAFE_HARBOR_CATEGORIES
        ]
        if blocked:
            message = f'phi_mode=True forbids HIPAA Safe Harbor categories: {blocked!r}'
            raise ValueError(message)
        return self

    def is_empty(self) -> bool:
        """Return True when no entries are configured."""
        return len(self.entries) == 0


class RedactedMessage(BaseModel):
    """Human message carrying known PII and whitelist metadata.

    Shielding is overly restrictive by default: dates, times, names, and other
    detected values are replaced by placeholders before anything durable or
    provider-bound sees them. When a detection category IS your application's
    substance - scheduling dates, say - free it with ``pii_whitelist``::

        RedactedMessage(
            content='Move the review from 2026-09-01 to 2026-09-15.',
            pii_whitelist=PIIWhitelist(
                entries=(
                    PIIWhitelistEntry(
                        entity_type='datetime',
                        justification='Scheduling dates are the substance of this app.',
                    ),
                ),
            ),
        )

    Entries may free an ``entity_type``, a literal ``value``, or a ``pattern``,
    each with an auditable justification. A whitelist the server cannot honor
    refuses the run as ``private_whitelist_invalid`` - it is never silently
    ignored.

    Redaction reduces exposure but is not a certification or guarantee that
    every private value will be detected. mAIvn holds no SOC 2, HIPAA,
    ISO 27001, or FedRAMP certification; evaluate and use the control at your
    own risk under the mAIvn Platform Disclaimer.
    """

    model_config: ClassVar[ConfigDict] = ConfigDict(extra='forbid', frozen=True)

    # repr=False: the content and known values are the private text being protected.
    content: str | list[ContentBlock] = Field(repr=False)
    known_pii_values: list[str | PrivateData] | None = Field(default=None, repr=False)
    pii_whitelist: PIIWhitelist | None = None
    cover_regions: list[CoverRegion] | None = Field(default=None, repr=False)
    attachments: list[Attachment] | None = None
    artifact_refs: tuple[ArtifactRef, ...] | None = None
    metadata: dict[str, MetadataValue] | None = None
    message_id: str = Field(default_factory=_new_message_id, min_length=1)
    ts: str = Field(default_factory=_timestamp)

    @field_validator('known_pii_values', mode='before')
    @classmethod
    def _empty_known_values_mean_none(cls, value: object) -> object:
        """Treat an empty list as no known values; the contract refuses an empty list."""
        return None if value == [] else value

    @field_validator('attachments', mode='before')
    @classmethod
    def _coerce_attachments(cls, value: object) -> object:
        """Accept the same attachment dictionary shapes as ``BaseMessage``."""
        if value is None:
            return None
        # Local import: maivn.messages imports RedactedMessage from this
        # module, so the shared coercion helper cannot be imported at module
        # scope without a cycle.
        from maivn.messages import coerce_attachment_input  # noqa: PLC0415, RUF100

        if not isinstance(value, Sequence) or isinstance(value, str | bytes):
            message = 'attachments must be a list of Attachment objects or dictionaries'
            raise TypeError(message)
        return [coerce_attachment_input(item) for item in cast('Sequence[object]', value)]

    def to_contract(self) -> Message:
        """Return this redacted-message adapter as a v2 user message contract."""
        known_pii_values: list[str | KnownPrivateData] | None = None
        if self.known_pii_values is not None:
            known_pii_values = [
                KnownPrivateData.model_validate(
                    {
                        **value.model_dump(mode='json', exclude_none=True),
                        'value': str(value.value),
                    }
                )
                if isinstance(value, PrivateData)
                else value
                for value in self.known_pii_values
            ]
        pii_whitelist = (
            ContractPIIWhitelist.model_validate(
                self.pii_whitelist.model_dump(mode='json', exclude_none=True)
            )
            if self.pii_whitelist is not None
            else None
        )
        return Message(
            message_id=self.message_id,
            role='user',
            content=self.content,
            attachments=self.attachments,
            artifact_refs=self.artifact_refs,
            redaction=RedactionConfig(
                cover_regions=self.cover_regions,
                known_pii_values=known_pii_values,
                pii_whitelist=pii_whitelist,
            ),
            metadata=self.metadata,
            ts=self.ts,
        )


class _ApprovedMessage(Message):
    """A shielded wire message with a local, non-serializable approval binding."""

    _preview_id: str = PrivateAttr(default='')
    _approved_digest: str = PrivateAttr(default='')

    def with_approval(self, *, preview_id: str, digest: str) -> _ApprovedMessage:
        """Attach a private approval binding to a detached wire message."""
        self._preview_id = preview_id
        self._approved_digest = digest
        return self

    def matches(self, *, preview_id: str, digest: str) -> bool:
        """Check the ticket and the entire ordered message batch."""
        return self._preview_id == preview_id and self._approved_digest == digest


def _approved_digest(messages: Sequence[Message]) -> str:
    return hashlib.sha256(
        json.dumps(
            [m.model_dump(mode='json', exclude_none=True) for m in messages],
            ensure_ascii=False,
            sort_keys=True,
            separators=(',', ':'),
            allow_nan=False,
        ).encode('utf-8')
    ).hexdigest()


def validate_approved_messages(
    messages: Sequence[Message],
    *,
    preview_id: str | None,
    private_data: Mapping[object, object] | None,
) -> None:
    """Refuse changed copies or additions before any transport or privacy pass."""
    if preview_id is None and not any(isinstance(m, _ApprovedMessage) for m in messages):
        return
    digest = _approved_digest(messages)
    if (
        preview_id is None
        or not messages
        or private_data
        or any(
            not isinstance(m, _ApprovedMessage)
            or not m.matches(preview_id=preview_id, digest=digest)
            for m in messages
        )
    ):
        message = 'redaction_preview_mismatch'
        raise ValueError(message)


class RedactionPreviewResponse(ContractPreviewResponse):
    """Value-free preview with local access to spans in caller-owned text."""

    _original_texts: dict[int, str] = PrivateAttr(default_factory=dict)
    _approved_digest: str | None = PrivateAttr(default=None)
    _approved_preview_id: str | None = PrivateAttr(default=None)

    def model_post_init(self, context: Any, /) -> None:
        """Bind caller text from validation context without serializing it."""
        if self.preview_id is not None and self.approved_request is not None:
            self._approved_digest = _approved_digest(self.approved_request)
            self._approved_preview_id = self.preview_id
        if not isinstance(context, dict) or 'messages' not in context:
            return
        messages = cast('list[Message]', context['messages'])
        extra = cast('list[Attachment]', context['attachments'])
        for index, entry in enumerate(self.inputs):
            if entry.message_index is None or entry.message_index >= len(messages):
                continue
            message = messages[entry.message_index]
            if entry.kind == 'prompt':
                self._original_texts[index] = (
                    message.content
                    if isinstance(message.content, str)
                    else '\n'.join(block.text for block in message.content if block.type == 'text')
                )
            elif entry.kind == 'attachment':
                candidates = list(message.attachments or [])
                if entry.message_index == 0:
                    candidates.extend(extra)
                attachment = next(
                    (a for a in candidates if a.attachment_id == entry.attachment_id), None
                )
                if attachment and attachment.inline_base64 and entry.redacted_text is not None:
                    with suppress(ValueError, UnicodeDecodeError):
                        self._original_texts[index] = base64.b64decode(
                            attachment.inline_base64, validate=True
                        ).decode('utf-8')

    def approved_messages(self) -> list[Message]:
        """Return independent SDK messages bound to this preview's exact approved copy."""
        if self.preview_id is None or self.approved_request is None:
            message = 'redaction_preview_unavailable'
            raise ValueError(message)
        digest = _approved_digest(self.approved_request)
        if self.preview_id != self._approved_preview_id or digest != self._approved_digest:
            message = 'redaction_preview_mismatch'
            raise ValueError(message)
        return [
            _ApprovedMessage.model_validate(m.model_dump(mode='json')).with_approval(
                preview_id=self.preview_id, digest=digest
            )
            for m in self.approved_request
        ]

    def local_values(self) -> dict[str, list[str]]:
        """Extract span values locally; original text is never serialized."""
        values: dict[str, list[str]] = {}
        for item in self.items:
            original = self._original_texts.get(item.input_index)
            if original is not None:
                for span in item.spans:
                    if 0 <= span.start <= span.end <= len(original):
                        values.setdefault(item.key, []).append(original[span.start : span.end])
        return values

    def covered_bytes(self, input_index: int) -> bytes | None:
        """Decode a covered image or PDF rendering for an input."""
        image = self.inputs[input_index].covered_image
        return base64.b64decode(image.inline_base64, validate=True) if image else None


__all__ = [
    'HIPAA_SAFE_HARBOR_CATEGORIES',
    'CoverRegion',
    'PIIWhitelist',
    'PIIWhitelistEntry',
    'PrivateData',
    'RedactedMessage',
    'RedactionPreviewRequest',
    'RedactionPreviewResponse',
]
