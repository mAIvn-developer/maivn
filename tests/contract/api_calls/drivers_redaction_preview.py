"""Record the redaction preview API."""

from maivn import (
    CoverRegion,
    PIIWhitelist,
    PIIWhitelistEntry,
    PrivateData,
    RedactionPreviewResponse,
)
from tests.contract.api_calls.recording import FakeApi, Reply


async def run_preview(api: FakeApi) -> None:
    """Send a preview request through the public client."""
    api.answer('POST', '/v1/redaction-previews', Reply(json={'inputs': []}))
    await api.client(private_data_store=None, auto_detect_timezone=False).apreview_redaction(
        'hello',
        cover_regions=[
            CoverRegion(attachment_id='doc', page=1, bbox=(0.1, 0.2, 0.3, 0.4), label='signature')
        ],
        known_pii_values=[PrivateData(value='Alice', name='candidate', label='person')],
        pii_whitelist=PIIWhitelist(
            entries=(PIIWhitelistEntry(value='Alice', justification='Allowed for review'),)
        ),
    )


async def run_review(api: FakeApi) -> None:
    """Drive key-only edits and durable approval through their public methods."""
    payload: dict[str, object] = {
        'inputs': [],
        'preview_id': 'rpv2_synthetic',
        'thread_id': 'review-thread',
        'user_id': 'customer',
    }
    api.answer('POST', '/v1/redaction-previews/review', Reply(json=payload))
    api.answer('POST', '/v1/redaction-previews/approve', Reply(json=payload))
    client = api.client(private_data_store=None, auto_detect_timezone=False)
    reviewed = await client.areview_redaction(
        RedactionPreviewResponse.model_validate(payload),
        merge_keys=[['first', 'second']],
        released_keys=['third'],
    )
    await client.aapprove_redaction(reviewed)


DRIVERS = (run_preview, run_review)
