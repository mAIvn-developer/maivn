"""Record the redaction preview API."""

from maivn import CoverRegion, PIIWhitelist, PIIWhitelistEntry, PrivateData
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


DRIVERS = (run_preview,)
