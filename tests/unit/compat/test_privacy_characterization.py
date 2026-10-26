"""Characterization tests for the v1-compatible private-data request models.

These pin the client-side privacy policy exposed through ``maivn``: whitelist
entry validation, the ``phi_mode`` Safe Harbor guard, coercion of whitelist
entries, and the exact v2 message contract a ``RedactedMessage`` becomes.
"""

from __future__ import annotations

import re

import pytest
from pydantic import ValidationError

from maivn import (
    HIPAA_SAFE_HARBOR_CATEGORIES,
    PIIWhitelist,
    PIIWhitelistEntry,
    PrivateData,
    RedactedMessage,
    RedactionPreviewRequest,
    RedactionPreviewResponse,
)

_JUSTIFICATION = 'Public support inbox'
_EXACTLY_ONE = (
    'Value error, PIIWhitelistEntry requires exactly one of entity_type, pattern, or value'
)
_MESSAGE_ID = re.compile(r'^msg-[0-9a-f]{32}$')
_TIMESTAMP = re.compile(r'^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\.\d{6}Z$')


def _only_error(excinfo: pytest.ExceptionInfo[ValidationError]) -> tuple[tuple[object, ...], str]:
    errors = excinfo.value.errors()
    assert len(errors) == 1
    return tuple(errors[0]['loc']), errors[0]['msg']


def test_safe_harbor_categories_are_the_published_twenty() -> None:
    """The guarded category set is exactly the published Safe Harbor list."""
    assert (
        frozenset(
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
        == HIPAA_SAFE_HARBOR_CATEGORIES
    )


def test_private_data_keeps_descriptor_fields_and_ignores_unknown_ones() -> None:
    """``PrivateData`` is a frozen descriptor that drops unknown keys."""
    data = PrivateData.model_validate(
        {'value': 'Maria', 'name': 'customer', 'pii_type': 'person', 'extra': 'dropped'},
    )
    assert data.model_dump() == {
        'value': 'Maria',
        'name': 'customer',
        'pii_type': 'person',
        'label': None,
        'description': None,
        'format': None,
    }
    with pytest.raises(ValidationError, match='frozen'):
        data.value = 'other'


@pytest.mark.parametrize(
    ('kwargs', 'expected'),
    [
        ({'entity_type': ' Person '}, ('person', None, None)),
        ({'pattern': r'\bACME-\d+\b'}, (None, r'\bACME-\d+\b', None)),
        ({'value': '  abc  '}, (None, None, 'abc')),
    ],
)
def test_entry_accepts_exactly_one_kind_and_normalizes_it(
    kwargs: dict[str, str],
    expected: tuple[str | None, str | None, str | None],
) -> None:
    """Entity types are lowercased and stripped, values stripped, patterns kept verbatim."""
    entry = PIIWhitelistEntry(justification=_JUSTIFICATION, **kwargs)
    assert (entry.entity_type, entry.pattern, entry.value) == expected
    assert entry.label is None


@pytest.mark.parametrize(
    'kwargs',
    [
        {},
        {'entity_type': 'email', 'value': 'abc'},
        {'entity_type': 'email', 'pattern': 'x+'},
        {'pattern': 'x+', 'value': 'abc'},
        {'entity_type': 'email', 'pattern': 'x+', 'value': 'abc'},
    ],
)
def test_entry_rejects_zero_or_several_kinds(kwargs: dict[str, str]) -> None:
    """An entry must name exactly one of entity_type, pattern, or value."""
    with pytest.raises(ValidationError) as excinfo:
        _ = PIIWhitelistEntry(justification=_JUSTIFICATION, **kwargs)
    assert _only_error(excinfo) == ((), _EXACTLY_ONE)


@pytest.mark.parametrize(
    ('kwargs', 'loc', 'message'),
    [
        ({'entity_type': '   '}, 'entity_type', 'Value error, entity_type cannot be blank'),
        ({'entity_type': ''}, 'entity_type', 'Value error, entity_type cannot be blank'),
        ({'pattern': '  '}, 'pattern', 'Value error, pattern cannot be blank'),
        (
            {'pattern': '('},
            'pattern',
            'Value error, invalid regex pattern: missing ), unterminated subpattern at position 0',
        ),
        (
            {'value': ' ab '},
            'value',
            'Value error, value must be at least 3 characters after stripping',
        ),
    ],
)
def test_entry_field_validators_reject_blank_short_and_invalid_inputs(
    kwargs: dict[str, str],
    loc: str,
    message: str,
) -> None:
    """Blank kinds, unparseable patterns and too-short literals are refused."""
    with pytest.raises(ValidationError) as excinfo:
        _ = PIIWhitelistEntry(justification=_JUSTIFICATION, **kwargs)
    assert _only_error(excinfo) == ((loc,), message)


def test_entry_value_of_exactly_three_characters_is_accepted() -> None:
    """Three characters after stripping is the minimum literal length."""
    assert PIIWhitelistEntry(value=' abc ', justification=_JUSTIFICATION).value == 'abc'


@pytest.mark.parametrize(
    ('justification', 'loc', 'message'),
    [
        ('1234567', ('justification',), 'String should have at least 8 characters'),
        ('x' * 513, ('justification',), 'String should have at most 512 characters'),
    ],
)
def test_entry_justification_must_be_auditable_length(
    justification: str,
    loc: tuple[str, ...],
    message: str,
) -> None:
    """Justifications are between 8 and 512 characters."""
    with pytest.raises(ValidationError) as excinfo:
        _ = PIIWhitelistEntry(value='abc', justification=justification)
    assert _only_error(excinfo) == (loc, message)


def test_entry_justification_boundaries_and_label_limit() -> None:
    """8 and 512 character justifications pass; labels are capped at 128."""
    assert PIIWhitelistEntry(value='abc', justification='x' * 8).justification == 'x' * 8
    assert PIIWhitelistEntry(value='abc', justification='x' * 512).justification == 'x' * 512
    assert PIIWhitelistEntry(value='abc', justification='x' * 8, label='l' * 128).label == 'l' * 128
    with pytest.raises(ValidationError) as excinfo:
        _ = PIIWhitelistEntry(value='abc', justification='x' * 8, label='l' * 129)
    assert _only_error(excinfo) == (('label',), 'String should have at most 128 characters')


def test_entry_forbids_extra_fields_and_is_frozen() -> None:
    """Unknown entry fields are refused and entries are immutable."""
    with pytest.raises(ValidationError) as excinfo:
        _ = PIIWhitelistEntry.model_validate(
            {'value': 'abc', 'justification': _JUSTIFICATION, 'scope': 'all'},
        )
    assert _only_error(excinfo) == (('scope',), 'Extra inputs are not permitted')
    entry = PIIWhitelistEntry(value='abc', justification=_JUSTIFICATION)
    with pytest.raises(ValidationError, match='frozen'):
        entry.value = 'abcd'


def test_whitelist_defaults_to_empty_without_phi_mode() -> None:
    """A bare whitelist has no entries and phi_mode off."""
    whitelist = PIIWhitelist()
    assert whitelist.entries == ()
    assert whitelist.phi_mode is False
    assert whitelist.is_empty() is True
    assert PIIWhitelist.model_validate({'entries': None}).entries == ()


def test_whitelist_coerces_single_entry_lists_and_dicts_into_a_tuple() -> None:
    """Entries arrive as one entry, a list or tuple of entries, or dicts."""
    entry = PIIWhitelistEntry(value='abc', justification=_JUSTIFICATION)
    single = PIIWhitelist(entries=entry)  # pyright: ignore[reportArgumentType]
    assert single.entries == (entry,)
    assert single.is_empty() is False
    mixed = PIIWhitelist.model_validate(
        {'entries': [entry, {'entity_type': 'ORG', 'justification': _JUSTIFICATION}]},
    )
    assert mixed.entries == (
        entry,
        PIIWhitelistEntry(entity_type='org', justification=_JUSTIFICATION),
    )
    assert PIIWhitelist(entries=(entry,)).entries == (entry,)


def test_whitelist_dict_entries_are_fully_validated() -> None:
    """A dict entry goes through the same validation as a constructed entry."""
    with pytest.raises(ValidationError) as excinfo:
        _ = PIIWhitelist.model_validate({'entries': [{'justification': _JUSTIFICATION}]})
    assert _only_error(excinfo) == (('entries',), _EXACTLY_ONE)


def test_whitelist_rejects_non_sequence_entries() -> None:
    """Anything other than an entry, list, tuple or None is a type error."""
    with pytest.raises(TypeError, match=r'^') as excinfo:
        _ = PIIWhitelist.model_validate({'entries': 'abc'})
    assert str(excinfo.value) == 'entries must be a list, tuple, PIIWhitelistEntry, or None'


def test_whitelist_rejects_foreign_items_inside_a_list() -> None:
    """Every list item must be an entry or a dict."""
    with pytest.raises(TypeError, match=r'^') as excinfo:
        _ = PIIWhitelist.model_validate({'entries': [5]})
    assert str(excinfo.value) == 'entries must contain PIIWhitelistEntry or dict items'


def test_whitelist_forbids_extra_fields() -> None:
    """Unknown whitelist fields are refused rather than ignored."""
    with pytest.raises(ValidationError) as excinfo:
        _ = PIIWhitelist.model_validate({'entries': [], 'strict': True})
    assert _only_error(excinfo) == (('strict',), 'Extra inputs are not permitted')


def test_phi_mode_refuses_every_safe_harbor_entity_type_in_order() -> None:
    """phi_mode lists every blocked category, normalized and in entry order."""
    with pytest.raises(ValidationError) as excinfo:
        _ = PIIWhitelist(
            entries=(
                PIIWhitelistEntry(entity_type=' SSN ', justification=_JUSTIFICATION),
                PIIWhitelistEntry(entity_type='organization', justification=_JUSTIFICATION),
                PIIWhitelistEntry(entity_type='Date', justification=_JUSTIFICATION),
            ),
            phi_mode=True,
        )
    assert _only_error(excinfo) == (
        (),
        "Value error, phi_mode=True forbids HIPAA Safe Harbor categories: ['ssn', 'date']",
    )


@pytest.mark.parametrize('category', sorted(HIPAA_SAFE_HARBOR_CATEGORIES))
def test_phi_mode_refuses_each_safe_harbor_category(category: str) -> None:
    """No single Safe Harbor category can be whitelisted under phi_mode."""
    entry = PIIWhitelistEntry(entity_type=category, justification=_JUSTIFICATION)
    with pytest.raises(ValidationError) as excinfo:
        _ = PIIWhitelist(entries=(entry,), phi_mode=True)
    assert _only_error(excinfo) == (
        (),
        f'Value error, phi_mode=True forbids HIPAA Safe Harbor categories: [{category!r}]',
    )
    assert PIIWhitelist(entries=(entry,)).entries == (entry,)


def test_phi_mode_allows_categories_outside_safe_harbor() -> None:
    """Non-Safe-Harbor entity types remain whitelistable under phi_mode."""
    entry = PIIWhitelistEntry(entity_type='organization', justification=_JUSTIFICATION)
    whitelist = PIIWhitelist(entries=(entry,), phi_mode=True)
    assert whitelist.entries == (entry,)
    assert whitelist.phi_mode is True


def test_redacted_message_generates_ids_and_utc_timestamps() -> None:
    """Defaults give a unique ``msg-<hex>`` id and a Z-suffixed UTC timestamp."""
    first = RedactedMessage(content='hello')
    second = RedactedMessage(content='hello')
    assert _MESSAGE_ID.match(first.message_id)
    assert first.message_id != second.message_id
    assert _TIMESTAMP.match(first.ts)
    assert first.known_pii_values is None
    assert first.pii_whitelist is None
    assert first.attachments is None


def test_redacted_message_refuses_an_empty_message_id_and_extra_fields() -> None:
    """Message ids must be non-empty and unknown fields are refused."""
    with pytest.raises(ValidationError) as excinfo:
        _ = RedactedMessage(content='hi', message_id='')
    assert _only_error(excinfo) == (('message_id',), 'String should have at least 1 character')
    with pytest.raises(ValidationError) as extra:
        _ = RedactedMessage.model_validate({'content': 'hi', 'role': 'assistant'})
    assert _only_error(extra) == (('role',), 'Extra inputs are not permitted')


@pytest.mark.parametrize('attachments', ['abc', b'abc', 5])
def test_redacted_message_rejects_non_list_attachments(attachments: object) -> None:
    """Strings, bytes and scalars are not attachment lists."""
    with pytest.raises(TypeError, match=r'^') as excinfo:
        _ = RedactedMessage.model_validate({'content': 'hi', 'attachments': attachments})
    assert str(excinfo.value) == 'attachments must be a list of Attachment objects or dictionaries'


def test_redacted_message_contract_carries_redaction_config() -> None:
    """``to_contract`` stringifies descriptor values and copies the whitelist."""
    message = RedactedMessage(
        content='Call Maria about invoice 42',
        known_pii_values=[
            PrivateData(value=42, name='invoice', pii_type='account_id'),
            'maria@example.com',
        ],
        pii_whitelist=PIIWhitelist(
            entries=(PIIWhitelistEntry(value=' ACME ', justification=_JUSTIFICATION),),
            phi_mode=True,
        ),
        metadata={'channel': 'email'},
        message_id='msg-fixed',
        ts='2026-01-02T03:04:05Z',
    )
    contract = message.to_contract()
    assert contract.model_dump(mode='json') == {
        'message_id': 'msg-fixed',
        'role': 'user',
        'content': 'Call Maria about invoice 42',
        'attachments': None,
        'artifact_refs': None,
        'redaction': {
            'pii_whitelist': {
                'entries': [
                    {
                        'entity_type': None,
                        'pattern': None,
                        'value': 'ACME',
                        'justification': _JUSTIFICATION,
                        'label': None,
                    },
                ],
                'phi_mode': True,
            },
            'known_pii_values': [
                {
                    'value': '42',
                    'name': 'invoice',
                    'pii_type': 'account_id',
                    'label': None,
                    'description': None,
                    'format': None,
                },
                'maria@example.com',
            ],
            'private_data_schema': None,
        },
        'metadata': {'channel': 'email'},
        'ts': '2026-01-02T03:04:05Z',
    }


def test_redacted_message_contract_without_redaction_inputs() -> None:
    """Absent known values and whitelist stay absent in the contract."""
    contract = RedactedMessage(content='plain', message_id='m', ts='t').to_contract()
    assert contract.model_dump(mode='json') == {
        'message_id': 'm',
        'role': 'user',
        'content': 'plain',
        'attachments': None,
        'artifact_refs': None,
        'redaction': {
            'pii_whitelist': None,
            'known_pii_values': None,
            'private_data_schema': None,
        },
        'metadata': None,
        'ts': 't',
    }


def test_empty_known_pii_values_mean_no_known_values() -> None:
    """``known_pii_values=[]`` behaves as ``None`` instead of failing at invoke (U05-D9)."""
    message = RedactedMessage(content='plain', known_pii_values=[], message_id='m', ts='t')

    assert message.known_pii_values is None
    redaction = message.to_contract().redaction
    assert redaction is not None
    assert redaction.known_pii_values is None


def test_redaction_preview_request_and_response_defaults() -> None:
    """Preview bodies default to empty lists; the response keeps server extras."""
    request = RedactionPreviewRequest()
    assert request.model_dump() == {'messages': [], 'pii_whitelist': None}
    with pytest.raises(ValidationError) as excinfo:
        _ = RedactionPreviewRequest.model_validate({'messages': [], 'dry_run': True})
    assert _only_error(excinfo) == (('dry_run',), 'Extra inputs are not permitted')
    response = RedactionPreviewResponse.model_validate(
        {'redactions': [{'span': [0, 5], 'entity_type': 'person'}], 'engine': 'v2'},
    )
    assert response.model_dump() == {
        'messages': [],
        'redactions': [{'span': [0, 5], 'entity_type': 'person'}],
        'engine': 'v2',
    }


def test_explicit_none_kinds_count_as_absent() -> None:
    """Passing None for the other kinds is the same as omitting them."""
    entry = PIIWhitelistEntry(
        entity_type=None,
        pattern=None,
        value='abc',
        justification=_JUSTIFICATION,
    )
    assert (entry.entity_type, entry.pattern, entry.value) == (None, None, 'abc')
    typed = PIIWhitelistEntry(entity_type='org', value=None, justification=_JUSTIFICATION)
    assert (typed.entity_type, typed.pattern, typed.value) == ('org', None, None)


def test_redacted_message_accepts_explicit_none_attachments() -> None:
    """An explicit None attachment list stays None."""
    message = RedactedMessage.model_validate({'content': 'hi', 'attachments': None})
    assert message.attachments is None


def test_redacted_message_coerces_attachment_dictionaries_into_the_contract() -> None:
    """Attachment dictionaries use the shared message coercion and reach the contract."""
    message = RedactedMessage.model_validate(
        {
            'content': 'see attached',
            'attachments': [
                {
                    'name': 'notes.txt',
                    'mime_type': 'text/plain',
                    'text_content': 'hi',
                    'attachment_id': 'att-1',
                },
            ],
            'message_id': 'm',
            'ts': 't',
        },
    )
    attachments = message.to_contract().model_dump(mode='json', exclude_none=True)['attachments']
    assert attachments == [
        {
            'attachment_id': 'att-1',
            'filename': 'notes.txt',
            'mime_type': 'text/plain',
            'size_bytes': 2,
            'inline_base64': 'aGk=',
            'content_hash': '8f434346648f6b96df89dda901c5176b10a6d83961dd3c1ac88b59b2dc327aa4',
        },
    ]
