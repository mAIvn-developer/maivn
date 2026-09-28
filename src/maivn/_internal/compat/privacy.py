"""Private-data and redaction request models.

These are technical privacy controls, not certifications. mAIvn holds no
SOC 2, HIPAA, ISO 27001, or FedRAMP certification. In particular,
``phi_mode`` limits HIPAA Safe Harbor whitelist categories; it does not make
a deployment HIPAA-compliant, provide a BAA, or replace required audits and
organizational controls. Developers must evaluate and use these controls at
their own risk under the mAIvn Platform Disclaimer.
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from datetime import datetime, timezone
from typing import ClassVar, cast
from uuid import uuid4

from maivn_contracts.artifacts import ArtifactRef
from maivn_contracts.messages import (
    Attachment,
    ContentBlock,
    KnownPrivateData,
    Message,
    MetadataValue,
    RedactionConfig,
)
from maivn_contracts.messages import (
    PIIWhitelist as ContractPIIWhitelist,
)
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

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


def _new_message_id() -> str:
    return f'msg-{uuid4().hex}'


def _timestamp() -> str:
    return datetime.now(timezone.utc).isoformat().replace('+00:00', 'Z')


class PrivateData(BaseModel):
    """Structured known PII descriptor for enhanced redaction."""

    model_config: ClassVar[ConfigDict] = ConfigDict(frozen=True, extra='ignore')

    value: object
    name: str | None = None
    pii_type: str | None = None
    label: str | None = None
    description: str | None = None
    format: str | None = None


class PIIWhitelistEntry(BaseModel):
    """A single whitelist rule for detected PII spans."""

    model_config: ClassVar[ConfigDict] = ConfigDict(frozen=True, extra='forbid')

    entity_type: str | None = None
    pattern: str | None = None
    value: str | None = None
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

    content: str | list[ContentBlock]
    known_pii_values: list[str | PrivateData] | None = None
    pii_whitelist: PIIWhitelist | None = None
    attachments: list[Attachment] | None = None
    artifact_refs: tuple[ArtifactRef, ...] | None = None
    metadata: dict[str, MetadataValue] | None = None
    message_id: str = Field(default_factory=_new_message_id, min_length=1)
    ts: str = Field(default_factory=_timestamp)

    @field_validator('attachments', mode='before')
    @classmethod
    def _coerce_attachments(cls, value: object) -> object:
        """Accept the same attachment dictionary shapes as ``BaseMessage``."""
        if value is None:
            return None
        # Local import: maivn.messages imports RedactedMessage from this
        # module, so the shared coercion helper cannot be imported at module
        # scope without a cycle.
        from maivn.messages import coerce_attachment_input  # noqa: PLC0415

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
                known_pii_values=known_pii_values,
                pii_whitelist=pii_whitelist,
            ),
            metadata=self.metadata,
            ts=self.ts,
        )


class RedactionPreviewRequest(BaseModel):
    """Request body for previewing redaction without executing a run."""

    model_config: ClassVar[ConfigDict] = ConfigDict(extra='forbid', frozen=True)

    messages: list[RedactedMessage] = Field(default_factory=list)
    pii_whitelist: PIIWhitelist | None = None


class RedactionPreviewResponse(BaseModel):
    """Response body for a redaction preview."""

    model_config: ClassVar[ConfigDict] = ConfigDict(extra='allow', frozen=True)

    messages: list[RedactedMessage] = Field(default_factory=list)
    redactions: list[dict[str, object]] = Field(default_factory=list)


__all__ = [
    'HIPAA_SAFE_HARBOR_CATEGORIES',
    'PIIWhitelist',
    'PIIWhitelistEntry',
    'PrivateData',
    'RedactedMessage',
    'RedactionPreviewRequest',
    'RedactionPreviewResponse',
]
