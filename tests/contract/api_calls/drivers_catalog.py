"""Drive the artifact, resource and skill routes through the public `Client` facades.

Request models are filled with non-default values so the recorded body carries
every field the SDK can send (the SDK drops defaults and `None` on the wire).
"""

from __future__ import annotations

import hashlib
import json
import tempfile
from pathlib import Path
from typing import Any

from maivn_contracts.artifacts import OrdinaryArtifactRef

from maivn import (
    ResourcePatch,
    ResourceScopeRequest,
    ResourceUploadOptions,
    Skill,
    SkillScopeRequest,
    SkillSet,
    SkillStep,
)
from tests.contract.api_calls.recording import FakeApi, Reply

DOCX_MIME = 'application/vnd.openxmlformats-officedocument.wordprocessingml.document'
ARTIFACT_BYTES = b'PK\x03\x04artifact-bytes'
ARTIFACT_SHA = hashlib.sha256(ARTIFACT_BYTES).hexdigest()
PREVIEW_BYTES = b'\x89PNG\r\n\x1a\npreview-bytes'
PREVIEW_SHA = hashlib.sha256(PREVIEW_BYTES).hexdigest()
ARTIFACT: dict[str, Any] = {
    'artifact_id': 'art_1',
    'revision': 2,
    'state': 'available',
    'invocation_id': 'inv_1',
    'logical_output_id': 'out_1',
    'kind': 'document',
    'mime_type': DOCX_MIME,
    'display_filename': 'report.docx',
    'byte_size': len(ARTIFACT_BYTES),
    'sha256': ARTIFACT_SHA,
    'created_at': '2026-10-09T00:00:00Z',
    'expires_at': '2026-11-09T00:00:00Z',
}
ARTIFACT_REF = OrdinaryArtifactRef.model_validate(
    {
        'artifact_id': 'art_1',
        'logical_output_id': 'out_1',
        'revision': 1,
        'kind': 'document',
        'mime_type': DOCX_MIME,
        'created_at': '2026-10-09T00:00:00Z',
        'effective_retention': {
            'policy_snapshot_id': 'pol_1',
            'retention_class': 'artifact_30d',
            'expires_at': '2026-11-09T00:00:00Z',
        },
        'custody': 'ordinary',
        'state': 'available',
        'display_filename': 'report.docx',
        'size_bytes': len(ARTIFACT_BYTES),
        'sha256': ARTIFACT_SHA,
        'producer': {
            'producer_class': 'external_tool',
            'producer_id': 'tool_1',
            'root_invocation_id': 'inv_1',
            'session_id': 'ses_1',
            'call_id': 'call_1',
        },
        'validation_receipt': {
            'receipt_id': 'rcp_1',
            'status': 'validated',
            'validator_profile': 'tool_file_document',
            'validator_version': '1',
        },
        'safe_preview': {'kind': 'document', 'page_count': 1},
        'retrieval_action': {'relation': 'artifact.download_authorization'},
    }
)
DOWNLOAD_HEADERS = {
    'content-type': DOCX_MIME,
    'content-disposition': 'attachment; filename="report.docx"',
}
PREVIEW_HEADERS = {
    'content-type': 'image/png',
    'content-disposition': 'inline; filename="preview.png"',
    'x-artifact-sha256': PREVIEW_SHA,
    'x-artifact-source-revision': '2',
    'cache-control': 'private, no-store',
    'x-content-type-options': 'nosniff',
}

RESOURCE: dict[str, Any] = {
    'memory_id': 'mem_res_1',
    'resource_id': 'res_1',
    'resource_thread_id': 'rth-' + '0' * 32,
    'scope': {'project_id': 'prj_1', 'sharing_scope': 'project'},
    'name': 'Handbook',
    'media_type': 'text/plain',
    'content_hash': '1' * 64,
    'size_bytes': 12,
    'status': 'registered',
    'processing_status': 'ready',
    'binding_type': 'portal',
    'deduplicated': False,
    'created_at': '2026-10-09T00:00:00Z',
    'updated_at': '2026-10-09T00:00:00Z',
}

SKILL_STEP = SkillStep(
    index=1,
    action='look it up',
    tool='lookup',
    parameters={'key': 'value'},
    expected_output='a value',
)
SKILL_SCOPE = SkillScopeRequest(
    project_id='prj_1',
    organization_id='org_1',
    agent_id='agt_1',
    swarm_id='swm_1',
    sharing_scope='swarm',
)
SKILL_RECORD: dict[str, Any] = {
    'kind': 'skill',
    'memory_id': 'skl_1',
    'scope': {'project_id': 'prj_1', 'sharing_scope': 'project'},
    'name': 'Lookup',
    'description': 'Look a value up.',
    'steps': [{'index': 1, 'action': 'look it up'}],
    'confidence': 0.9,
    'origin': 'user_defined',
    'status': 'active',
    'created_at': '2026-10-09T00:00:00Z',
    'updated_at': '2026-10-09T00:00:00Z',
}
SKILL_SET_RECORD: dict[str, Any] = {
    'kind': 'skill_set',
    'id': 'set_1',
    'scope': {'project_id': 'prj_1', 'sharing_scope': 'project'},
    'name': 'Research',
    'description': 'Research skills.',
    'member_skill_ids': ['skl_1'],
}


def _skill(*, memory_id: str | None = None) -> Skill:
    return Skill(
        memory_id=memory_id,
        scope=SKILL_SCOPE,
        name='Lookup',
        description='Look a value up.',
        steps=(SKILL_STEP,),
        preconditions={'ready': True},
        postconditions={'done': True},
        confidence=0.5,
        origin='ai_generated',
        status='deprecated',
        source_session_id='ses_1',
        source_invocation_id='inv_1',
    )


async def artifacts_metadata(api: FakeApi) -> None:
    """List and read artifact metadata."""
    api.answer('GET', '/v1/artifacts', Reply(json={'artifacts': [ARTIFACT], 'next_cursor': 'c2'}))
    api.answer('GET', '/v1/artifacts/art_1', Reply(json=ARTIFACT))
    artifacts = api.client().artifacts

    await artifacts.alist(invocation_id='inv_1', state='all', revision=2, cursor='c1', limit=10)
    await artifacts.aget('art_1', revision=2)


async def artifacts_bytes(api: FakeApi) -> None:
    """Download, preview and read the source of artifacts, in memory and to disk."""
    api.answer(
        'GET',
        '/v1/artifacts/art_1/download',
        Reply(content=ARTIFACT_BYTES, headers=DOWNLOAD_HEADERS),
    )
    api.answer(
        'GET',
        '/v1/artifacts/art_1/revisions/2/preview',
        Reply(content=PREVIEW_BYTES, headers=PREVIEW_HEADERS),
    )
    source = json.dumps({'output_sha256': ARTIFACT_SHA}).encode()
    api.answer(
        'POST',
        '/v1/artifacts/source',
        Reply(
            content=source,
            headers={
                'content-type': 'application/octet-stream',
                'x-maivn-source-format': 'maivn-source-archive-v1',
                'x-maivn-source-sha256': hashlib.sha256(source).hexdigest(),
            },
        ),
    )
    artifacts = api.client().artifacts

    await artifacts.adownload('art_1', revision=1)
    await artifacts.adownload(ARTIFACT_REF)
    await artifacts.apreview('art_1', revision=2)
    await artifacts.adownload_source(ARTIFACT_REF, session_id='ses_1')
    with tempfile.TemporaryDirectory() as directory:
        await artifacts.adownload_to('art_1', Path(directory) / 'report.docx', revision=1)
        await artifacts.apreview_to('art_1', Path(directory) / 'preview.png', revision=2)


async def resources_records(api: FakeApi) -> None:
    """Upload, list, read, patch, delete, bind, restore and rebind resources."""
    api.answer('GET', '/v1/resources', Reply(json={'items': [RESOURCE]}))
    api.answer('POST', '/v1/resources', Reply(status=201, json=RESOURCE))
    api.answer('GET', '/v1/resources/res_1', Reply(json=RESOURCE))
    api.answer('PATCH', '/v1/resources/res_1', Reply(json=RESOURCE))
    api.answer('DELETE', '/v1/resources/res_1', Reply(json={'success': True}))
    api.answer('POST', '/v1/resources/res_1/bind', Reply(json=RESOURCE))
    api.answer('POST', '/v1/resources/res_1/restore', Reply(json=RESOURCE))
    api.answer('POST', '/v1/resources/res_1/rebind', Reply(json=RESOURCE))
    resources = api.client().resources

    await resources.alist(query='handbook', status='all', processing_status='ready', limit=5)
    await resources.aupload(
        b'hello world!',
        media_type='text/plain',
        name='Handbook',
        options=ResourceUploadOptions(
            resource_id='res_1',
            scope=ResourceScopeRequest(
                project_id='prj_1', organization_id='org_1', sharing_scope='org'
            ),
            description='The team handbook.',
            tags=('team', 'docs'),
            metadata={'source': 'upload'},
        ),
    )
    await resources.aget('res_1')
    await resources.apatch(
        'res_1',
        ResourcePatch(
            name='Handbook',
            description='The team handbook.',
            tags=('team',),
            metadata={'source': 'patch'},
            sharing_scope='org',
        ),
    )
    await resources.adelete('res_1')
    await resources.abind('res_1', binding_type='agent', target_id='agt_1')
    await resources.arestore('res_1')
    await resources.arebind('res_1')


async def skills_records(api: FakeApi) -> None:
    """List, create and read skills."""
    api.answer('GET', '/v1/skills', Reply(json={'items': [SKILL_RECORD]}))
    api.answer('POST', '/v1/skills', Reply(status=201, json=SKILL_RECORD))
    api.answer('GET', '/v1/skills/skl_1', Reply(json=SKILL_RECORD))
    skills = api.client().skills

    await skills.alist(query='lookup', limit=5)
    await skills.acreate(_skill(memory_id='skl_1'))
    await skills.aget('skl_1')


async def skill_sets_records(api: FakeApi) -> None:
    """List, create, read, extend and search skill sets."""
    api.answer('GET', '/v1/skill-sets', Reply(json={'items': [SKILL_SET_RECORD]}))
    api.answer('POST', '/v1/skill-sets', Reply(status=201, json=SKILL_SET_RECORD))
    api.answer(
        'GET',
        '/v1/skill-sets/set_1/index',
        Reply(
            json={
                'skill_set': SKILL_SET_RECORD,
                'descendant_skill_sets': [
                    {
                        **SKILL_SET_RECORD,
                        'id': 'set_2',
                        'parent_set_id': 'set_1',
                        'member_skill_ids': [],
                    },
                ],
                'skills': [],
            },
        ),
    )
    api.answer('GET', '/v1/skills/skl_1', Reply(json=SKILL_RECORD))
    api.answer('POST', '/v1/skill-sets/set_1/skills/skl_1', Reply(json=SKILL_SET_RECORD))
    api.answer('POST', '/v1/skill-sets/set_1/skill-sets/set_2', Reply(json=SKILL_SET_RECORD))
    api.answer('GET', '/v1/skill-sets/set_1/skills', Reply(json={'items': [SKILL_RECORD]}))
    sets = api.client().skills.skill_sets

    await sets.alist(query='research', limit=5)
    await sets.acreate(
        SkillSet(
            id='set_1',
            scope=SKILL_SCOPE,
            name='Research',
            description='Research skills.',
            parent_set_id='set_0',
        )
    )
    await sets.aget('set_1')
    await sets.aadd_skill('set_1', 'skl_1')
    await sets.aadd_child_set('set_1', 'set_2')
    await sets.asearch_skills('set_1', query='lookup', limit=5)


async def skills_register_tree(api: FakeApi) -> None:
    """Register a skill-set tree: the set, its skills and its nested sets."""
    api.answer('POST', '/v1/skill-sets', Reply(status=201, json=SKILL_SET_RECORD))
    api.answer('POST', '/v1/skills', Reply(status=201, json=SKILL_RECORD))
    api.answer('POST', '/v1/skill-sets/set_1/skills/skl_1', Reply(json=SKILL_SET_RECORD))
    api.answer('POST', '/v1/skill-sets/set_1/skill-sets/set_1', Reply(json=SKILL_SET_RECORD))
    skills = api.client().skills
    child = SkillSet(name='Nested', description='A nested set.')
    tree = SkillSet(
        name='Research',
        description='Research skills.',
        children=(_skill(), child),
    )

    await skills.aregister_skill_set_tree(tree)


DRIVERS = (
    artifacts_metadata,
    artifacts_bytes,
    resources_records,
    skills_records,
    skill_sets_records,
    skills_register_tree,
)
