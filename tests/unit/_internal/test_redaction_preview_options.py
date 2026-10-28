"""Approved-copy tickets travel in run config only when explicitly supplied."""

from maivn import CoverRegion, PrivateData, RedactedMessage, RunOptions
from maivn._internal.wire import message_payloads, run_config_payload


def test_approved_copy_option_is_sent_and_hidden_from_repr() -> None:
    """Carry the ticket on the wire without showing it in diagnostic reprs."""
    options = RunOptions(redaction_preview_id='sealed-ticket', user_id='customer-1')
    assert run_config_payload(options)['redaction_preview_id'] == 'sealed-ticket'
    assert 'sealed-ticket' not in repr(options)


def test_ordinary_config_omits_preview_ticket() -> None:
    """Existing callers produce the same run config."""
    assert 'redaction_preview_id' not in run_config_payload(RunOptions(user_id='customer-1'))


def test_redacted_message_cover_regions_reach_run_payload() -> None:
    """Message-level boxes survive alongside the approved ticket in a run."""
    region = CoverRegion.model_validate(
        {'attachment_id': 'doc', 'page': 1, 'box': [0.1, 0.2, 0.3, 0.4], 'key': 'mark'}
    )
    message = RedactedMessage(
        content='review',
        cover_regions=[region],
        known_pii_values=[PrivateData(value='Alice', label='Candidate')],
    )
    payload = message_payloads(message)
    assert payload[0]['redaction']['cover_regions'][0]['name'] == 'mark'
    assert payload[0]['redaction']['cover_regions'][0]['bbox'] == [0.1, 0.2, 0.3, 0.4]
    assert payload[0]['redaction']['known_pii_values'][0]['label'] == 'Candidate'
    assert (
        run_config_payload(RunOptions(redaction_preview_id='ticket', user_id='customer-1'))[
            'redaction_preview_id'
        ]
        == 'ticket'
    )
