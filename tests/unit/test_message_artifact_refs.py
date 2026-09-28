"""Public message adapters preserve exact immutable attachment identities."""

from __future__ import annotations

import asyncio
import hashlib
from typing import TYPE_CHECKING

import httpx
import pytest
from pydantic import ValidationError

from maivn import (
    Client,
    InvokeResponse,
    OrdinaryArtifactRef,
    PrivateArtifactRef,
    RedactedMessage,
    ThreadState,
)
from maivn.messages import AIMessage, HumanMessage, to_contract_messages

if TYPE_CHECKING:
    from pathlib import Path


def _refs() -> tuple[OrdinaryArtifactRef, PrivateArtifactRef]:
    common = {
        'artifact_id': 'artifact_revision_two',
        'logical_output_id': 'logical_report',
        'revision': 2,
        'supersedes_artifact_id': 'artifact_revision_one',
        'kind': 'document',
        'mime_type': 'application/pdf',
        'created_at': '2026-09-05T12:00:00Z',
        'effective_retention': {
            'policy_snapshot_id': 'policy_standard',
            'retention_class': 'standard',
            'expires_at': '2099-09-05T12:00:00Z',
        },
        'producer': {
            'producer_class': 'vault',
            'producer_id': 'vault_renderer',
            'root_invocation_id': 'root_invocation',
            'session_id': 'root_session',
        },
    }
    ordinary = OrdinaryArtifactRef.model_validate(
        {
            **common,
            'custody': 'ordinary',
            'state': 'available',
            'display_filename': 'report.pdf',
            'size_bytes': 5,
            'sha256': 'a' * 64,
            'validation_receipt': {
                'receipt_id': 'validation_receipt',
                'status': 'validated',
                'validator_profile': 'document-default',
                'validator_version': '1.0.0',
            },
            'retrieval_action': {'relation': 'artifact.download_authorization'},
        }
    )
    private = PrivateArtifactRef.model_validate(
        {
            **common,
            'artifact_id': 'private_revision_two',
            'custody': 'vault_private',
            'state': 'available_in_vault',
            'display_label': 'Private document',
            'creation_receipt_id': 'creation_receipt',
            'retrieval_action': {'relation': 'artifact.vault_download_authorization'},
        }
    )
    return ordinary, private


@pytest.mark.parametrize('adapter', [HumanMessage, RedactedMessage])
def test_message_input_preserves_mixed_exact_references(
    adapter: type[HumanMessage | RedactedMessage],
) -> None:
    """Dropping custody or revision lineage while converting loses the edit target."""
    refs = _refs()
    message = adapter(content='Update this file', artifact_refs=refs)

    converted = to_contract_messages(message)[0]

    assert converted.artifact_refs == refs
    assert converted.artifact_refs is not None
    assert converted.artifact_refs[1].revision == 2  # noqa: PLR2004
    assert converted.artifact_refs[1].supersedes_artifact_id == 'artifact_revision_one'
    assert isinstance(converted.artifact_refs[1], PrivateArtifactRef)


@pytest.mark.parametrize('adapter', [HumanMessage, RedactedMessage])
def test_message_reference_omission_and_empty_are_distinct(
    adapter: type[HumanMessage | RedactedMessage],
) -> None:
    """An omitted selection remains absent; an explicit empty selection survives."""
    omitted = adapter(content='No selection').to_contract()
    empty = adapter(content='Empty selection', artifact_refs=()).to_contract()

    assert omitted.artifact_refs is None
    assert 'artifact_refs' not in omitted.model_dump(mode='json', exclude_none=True)
    assert empty.model_dump(mode='json', exclude_none=True)['artifact_refs'] == []


@pytest.mark.parametrize('adapter', [HumanMessage, RedactedMessage])
def test_message_input_refuses_noncanonical_private_metadata(
    adapter: type[HumanMessage | RedactedMessage],
) -> None:
    """Private filenames cannot enter the message projection through a ref dictionary."""
    private = _refs()[1].model_dump(mode='json')
    private['display_filename'] = 'private-name.pdf'

    with pytest.raises(ValidationError):
        adapter.model_validate({'content': 'Update', 'artifact_refs': [private]})


@pytest.mark.parametrize('asynchronous', [False, True])
def test_ordinary_ref_download_saves_exact_verified_revision(
    tmp_path: Path,
    *,
    asynchronous: bool,
) -> None:
    """Selecting an old ref always downloads that revision and verifies its bytes."""
    content = b'%PDF-attachment'
    ref = OrdinaryArtifactRef.model_validate(
        {
            **_refs()[0].model_dump(mode='json'),
            'size_bytes': len(content),
            'sha256': hashlib.sha256(content).hexdigest(),
        }
    )
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(
            200,
            content=content,
            headers={
                'content-type': 'application/pdf',
                'content-disposition': 'attachment; filename="report.pdf"',
            },
        )

    client = Client(
        api_key='test-key', base_url='http://testserver', transport=httpx.MockTransport(handler)
    )
    destination = tmp_path / 'selected.pdf'
    if asynchronous:
        result = asyncio.run(client.artifacts.adownload_to(ref, destination))
    else:
        result = client.artifacts.download_to(ref, destination)

    assert result.content == content
    assert destination.read_bytes() == content
    assert requests[0].url.params['revision'] == '2'
    assert requests[0].url.path.endswith('/artifact_revision_two/download')
    assert list(tmp_path.iterdir()) == [destination]


@pytest.mark.parametrize('asynchronous', [False, True])
def test_ordinary_ref_download_failure_preserves_destination(
    tmp_path: Path,
    *,
    asynchronous: bool,
) -> None:
    """A different revision's response never overwrites an existing download."""
    ref = _refs()[0]
    destination = tmp_path / 'selected.pdf'
    destination.write_bytes(b'previous download')

    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            content=b'wrong',
            headers={
                'content-type': 'application/pdf',
                'content-disposition': 'attachment; filename="report.pdf"',
            },
        )

    client = Client(
        api_key='test-key', base_url='http://testserver', transport=httpx.MockTransport(handler)
    )
    if asynchronous:
        with pytest.raises(ValueError, match='artifact download response was invalid'):
            asyncio.run(client.artifacts.adownload_to(ref, destination))
    else:
        with pytest.raises(ValueError, match='artifact download response was invalid'):
            client.artifacts.download_to(ref, destination)

    assert destination.read_bytes() == b'previous download'
    assert list(tmp_path.iterdir()) == [destination]


def test_ordinary_ref_download_refuses_revision_override_before_transport() -> None:
    """An explicit reference cannot be redirected to a different revision."""
    client = Client(
        api_key='test-key',
        base_url='http://testserver',
        transport=httpx.MockTransport(lambda _: httpx.Response(200)),
    )
    with pytest.raises(ValueError, match='revision'):
        client.artifacts.download(_refs()[0], revision=1)


def test_public_result_and_history_preserve_empty_reply_attachments() -> None:
    """An empty assistant reply retains mixed references through result and history parsing."""
    refs = _refs()
    wire_message = AIMessage(content='', artifact_refs=refs).to_contract().model_dump(mode='json')
    result = InvokeResponse.from_payload(
        {
            'final_message': wire_message,
            'session_id': 'root_session',
            'root_event_id': 'root_event',
            'event_positions': [1],
        }
    )
    history = ThreadState.from_payload(
        {
            'thread_id': 'thread_report',
            'status': 'idle',
            'history': [wire_message],
            'created_at': '2026-09-05T12:00:00Z',
            'updated_at': '2026-09-05T12:00:00Z',
        }
    )

    assert result.response == ''
    assert result.artifacts == refs
    assert result.final_message.artifact_refs == refs
    assert history.history[0].artifact_refs == refs
    assert ThreadState.model_validate_json(history.model_dump_json()).history == history.history
