"""Drive every run, session and thread route through the public `Client` and `BoundThread`.

Runs stream server-sent events, so the session and thread event routes answer with
`id:`/`event:` framed SSE (the SDK rejects a frame without an `id`). One stream runs two
local tools: one returns a plain result and one a declared generated file, so the tool
result batch and the tool artifact intake are recorded with the bodies a real run sends.
Request options are filled with non-default values so the recorded bodies carry every
field the SDK can send.
"""

from __future__ import annotations

import tempfile
from hashlib import sha256
from http import HTTPStatus
from pathlib import Path
from typing import Any

from maivn_contracts.artifacts import GeneratedFile

from maivn import (
    ApprovalDecision,
    InMemoryPrivateDataStore,
    RunOptions,
    compose_argument_policy,
    depends_on_interrupt,
    depends_on_tool,
)
from maivn._internal.models import ToolMetadata
from maivn.decisions import (
    DecisionAnswer,
    DecisionApp,
    DecisionChoice,
    DecisionClient,
    DecisionConfig,
    DecisionRequest,
)
from maivn.messages import HumanMessage
from tests.contract.api_calls.recording import FakeApi, Reply

TS = '2026-10-09T00:00:00Z'
DOCX_MIME = 'application/vnd.openxmlformats-officedocument.wordprocessingml.document'
REPORT_BYTES = b'PK\x03\x04contract-report'
ACCEPTED_JSON = {'thread_id': 'thr_1', 'session_id': 'ses_1', 'stream_position': 0}


def _options(**changes: Any) -> RunOptions:
    """Return run options with every wire-bearing field set to a non-default value."""
    values: dict[str, Any] = {
        'thread_id': 'thr_1',
        'session_id': 'ses_1',
        'invocation_id': 'inv_1',
        'user_id': 'usr_1',
        'project_id': 'prj_1',
        'agent_id': 'agt_1',
        'swarm_id': 'swm_1',
        'client_timezone': 'America/Toronto',
        'sdk_deployment_timezone': 'UTC',
        'model': 'balanced',
        'timeout': 30.0,
        'reasoning': 'high',
        'model_directive': 'balanced',
        'routing_preference': 'quality',
        'system_model_choices': {
            'thinking': {'tier': 'fast'},
            'compose_argument': {'tier': 'balanced'},
            'repl': {'tier': 'max'},
        },
        'planning': {'tier': 'standard'},
        'max_output': 2048,
        'output_schema': {
            'name': 'Answer',
            'schema': {'type': 'object', 'properties': {'text': {'type': 'string'}}},
        },
        'memory': {
            'enabled': True,
            'level': 'focus',
            'persistence_mode': 'vector_plus_graph',
            'retrieval': {
                'skills_enabled': True,
                'insights_enabled': True,
                'resources_enabled': True,
                'graph_enabled': True,
                'max_graph_assertions': 3,
                'max_skills': 3,
                'max_insights': 3,
                'max_resources': 3,
                'max_context_chars': 6000,
            },
            'skill_extraction': {'enabled': True, 'max_count': 2},
            'insight_extraction': {'enabled': True, 'max_count': 2},
            'graph_extraction': {'enabled': True, 'max_count': 2},
        },
        'system_tools_config': {
            'allowed_system_tools': ['think'],
            'approved_compose_argument_targets': ['plan_report.query'],
            'allow_private_data_in_system_tools': True,
            'allow_private_data_placeholders_in_system_tools': True,
        },
        'followup_questions': {
            'max_questions_per_run': 2,
            'max_questions_per_thread': 4,
            'response_schema': {'type': 'string'},
            'timeout_seconds': 60,
        },
        'stream_response': True,
        'stream_deltas': False,
        'attached_skill_ids': ('skl_1',),
        'auto_skills': True,
        'auto_skills_query': 'reports',
        'auto_skills_limit': 2,
        'auto_skills_skill_set': 'set_1',
        'origin': 'sdk',
    }
    values.update(changes)
    return RunOptions.model_validate(values)


def _lookup_account(question: str, account_id: str) -> dict[str, str]:
    """Answer one account question locally."""
    return {'answer': f'{question}: active', 'account_id': account_id}


def _approve(_prompt: str) -> str:
    return 'approved'


@depends_on_tool('lookup_account', 'account_summary', revision_policy='on_validation_error')
@depends_on_interrupt('reviewer_note', _approve, prompt='Add a reviewer note.')
@compose_argument_policy('query', mode='require', approval='explicit')
def _plan_report(query: str, account_summary: str, reviewer_note: str) -> dict[str, str]:
    """Plan a report; declared on the wire but never executed by these drivers."""
    return {'query': query, 'summary': account_summary, 'note': reviewer_note}


class _ReportOwner:
    """Owns the folder a generated file may be read from."""

    def __init__(self, root: Path) -> None:
        self._root = root

    def authorized_generated_file_roots(self) -> tuple[Path, ...]:
        return (self._root,)

    def render_report(self) -> dict[str, object]:
        path = self._root / 'report.docx'
        path.write_bytes(REPORT_BYTES)
        return GeneratedFile.model_validate(
            {
                'kind': 'generated_file',
                'artifact_kind': 'document',
                'filename': path.name,
                'path': str(path.resolve()),
                'mime_type': DOCX_MIME,
                'size_bytes': len(REPORT_BYTES),
                'sha256': sha256(REPORT_BYTES).hexdigest(),
                'custody': 'ordinary',
            }
        ).model_dump(mode='json', exclude_none=True)


LOOKUP_TOOL = ToolMetadata(
    name='lookup_account',
    description='Look up one account.',
    always_execute=True,
    input_schema={
        'type': 'object',
        'properties': {'question': {'type': 'string'}, 'account_id': {'type': 'string'}},
        'required': ['question', 'account_id'],
    },
    output_schema={'type': 'object', 'properties': {'answer': {'type': 'string'}}},
    metadata={'destructive': True},
    target=_lookup_account,
)
PLAN_TOOL = ToolMetadata(
    name='plan_report',
    description='Plan one report.',
    final_tool=True,
    input_schema={
        'type': 'object',
        'properties': {
            'query': {'type': 'string'},
            'account_summary': {'type': 'string'},
            'reviewer_note': {'type': 'string'},
        },
        'required': ['query', 'account_summary', 'reviewer_note'],
    },
    metadata={
        'execution_controls': {
            'reevaluate': [
                {
                    'tool_id': 'lookup_account',
                    'tool_name': 'lookup_account',
                    'timing': 'after',
                    'instance_control': 'all',
                }
            ]
        }
    },
    target=_plan_report,
)
TOOL_PHASE_STARTED = {'stage': 'tool_execution', 'phase': 'started'}
PRIVATE_DATA: dict[object, object] = {'account_id': 'acct_1'}


def _event(session_id: str, event_type: str, payload: dict[str, Any], index: int) -> dict[str, Any]:
    return {
        'event_id': f'evt_{session_id}_{index}',
        'ordinal': f'ord_{index}',
        'type': event_type,
        'session_id': session_id,
        'root_event_id': f'evt_{session_id}_root',
        'payload': payload,
        'ts': TS,
    }


def _final(session_id: str, index: int = 1) -> dict[str, Any]:
    return _event(
        session_id,
        'final',
        {
            'message': {
                'message_id': f'msg_{session_id}_final',
                'role': 'assistant',
                'content': 'Done.',
                'ts': TS,
            },
            'usage': {'input_tokens': 4, 'output_tokens': 3},
            'stop_reason': 'end_turn',
            'tool_calls': {'count': 0, 'names': []},
        },
        index,
    )


def _tool_start(
    session_id: str, call_id: str, tool_name: str, arguments: dict[str, Any], index: int
) -> dict[str, Any]:
    event = _event(
        session_id,
        'system_tool_start',
        {
            'stage': 'tool_start',
            'tool_name': tool_name,
            'tool_call': {
                'call_id': call_id,
                'spec_ref': {'tool_id': tool_name, 'namespace': 'sdk', 'version': 'v1'},
                'arguments': arguments,
                'lineage': {'session_id': session_id, 'invocation_id': 'inv_1'},
            },
        },
        index,
    )
    event['correlation_id'] = call_id
    return event


def _artifact_ref(session_id: str, call_id: str) -> dict[str, Any]:
    return {
        'artifact_id': 'art_1',
        'logical_output_id': f'{call_id}_0',
        'revision': 1,
        'kind': 'document',
        'mime_type': DOCX_MIME,
        'created_at': TS,
        'effective_retention': {
            'policy_snapshot_id': 'pol_1',
            'retention_class': 'artifact_30d',
            'expires_at': '2026-11-08T00:00:00Z',
        },
        'custody': 'ordinary',
        'state': 'available',
        'display_filename': 'report.docx',
        'size_bytes': len(REPORT_BYTES),
        'sha256': sha256(REPORT_BYTES).hexdigest(),
        'producer': {
            'producer_class': 'external_tool',
            'producer_id': 'sdk_local_tool',
            'root_invocation_id': 'inv_1',
            'session_id': session_id,
            'call_id': call_id,
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


async def run_invoke(api: FakeApi) -> None:
    """Invoke with every option, tool declaration and private value the SDK can send."""
    api.answer(
        'POST',
        '/v1/invoke',
        Reply(status=HTTPStatus.ACCEPTED, json={'session_id': 'ses_1', 'stream_position': 0}),
    )
    api.answer('GET', '/v1/sessions/ses_1/events', Reply.sse(_final('ses_1')))
    client = api.client(private_data_store=InMemoryPrivateDataStore(), auto_detect_timezone=False)

    await client.ainvoke(
        [HumanMessage(message_id='msg_1', content='Plan the account report.')],
        options=_options(resume_from_position=2),
        tools=(LOOKUP_TOOL, PLAN_TOOL),
        private_data=PRIVATE_DATA,
        force_final_tool=True,
    )


async def run_stream_with_local_tools(api: FakeApi) -> None:
    """Stream a run whose local tools execute, posting a file and the result batch.

    No private data rides on this run: any private value makes generated files take
    vault custody, which is the private-artifact route, not the ordinary intake.
    """
    session_id = 'ses_2'
    api.answer(
        'POST',
        '/v1/invoke',
        Reply(status=HTTPStatus.ACCEPTED, json={'session_id': session_id, 'stream_position': 0}),
    )
    api.answer(
        'GET',
        f'/v1/sessions/{session_id}/events',
        Reply.sse(
            _tool_start(
                session_id,
                'call_1',
                'lookup_account',
                {'question': 'status?', 'account_id': 'acct_1'},
                1,
            ),
            _tool_start(session_id, 'call_2', 'render_report', {}, 2),
            _event(session_id, 'system_tool_chunk', TOOL_PHASE_STARTED, 3),
            _final(session_id, 4),
        ),
    )
    api.answer(
        'POST',
        f'/v1/sessions/{session_id}/tools/call_2/artifacts/0',
        Reply(
            status=HTTPStatus.CREATED,
            json={
                'status': 'accepted',
                'artifact': _artifact_ref(session_id, 'call_2'),
                'source_archive': None,
            },
        ),
    )
    api.answer(
        'POST',
        f'/v1/sessions/{session_id}/tools/results',
        Reply(status=HTTPStatus.ACCEPTED, json={'accepted': 2}),
    )
    client = api.client(
        private_data_store=InMemoryPrivateDataStore(),
        auto_detect_timezone=False,
        restore_private_values=False,
    )
    with tempfile.TemporaryDirectory() as folder:
        owner = _ReportOwner(Path(folder))
        render_tool = ToolMetadata(
            name='render_report',
            description='Render the account report.',
            input_schema={'type': 'object', 'properties': {}},
            output_schema=GeneratedFile.model_json_schema(),
            target=owner.render_report,
        )
        events = client.astream(
            'Render the account report.',
            options=_options(session_id=session_id),
            tools=(LOOKUP_TOOL, render_tool),
        )
        async for _event_item in events:
            pass


async def run_cancel_on_close(api: FakeApi) -> None:
    """Close a stream before its terminal event so the SDK cancels the run itself."""
    session_id = 'ses_3'
    api.answer(
        'POST',
        '/v1/invoke',
        Reply(status=HTTPStatus.ACCEPTED, json={'session_id': session_id, 'stream_position': 0}),
    )
    api.answer(
        'GET',
        f'/v1/sessions/{session_id}/events',
        Reply.sse(
            _event(session_id, 'status_message_chunk', {'content_delta': 'Working.'}, 1),
            _final(session_id, 2),
        ),
    )
    api.answer('POST', f'/v1/sessions/{session_id}/cancel', Reply(status=HTTPStatus.ACCEPTED))
    client = api.client(private_data_store=InMemoryPrivateDataStore(), auto_detect_timezone=False)

    events = client.astream(
        'Start and stop.',
        options=RunOptions(thread_id='thr_3', session_id=session_id),
        cancel_on_close=True,
    )
    async for _event_item in events:
        break
    await events.aclose()


async def run_sessions(api: FakeApi) -> None:
    """Replay, cancel and read usage for an existing session."""
    api.answer('GET', '/v1/sessions/ses_4/events', Reply.sse(_final('ses_4')))
    api.answer('POST', '/v1/sessions/ses_4/cancel', Reply(status=HTTPStatus.ACCEPTED))
    api.answer(
        'GET',
        '/v1/sessions/ses_4/usage',
        Reply(
            json={
                'session_id': 'ses_4',
                'scope': 'session',
                'settled': True,
                'provider_counts_complete': True,
                'calls': 1,
                'input_tokens': 4,
                'output_tokens': 3,
                'cache_read_input_tokens': 0,
                'cache_creation_input_tokens': 0,
                'by_model': [
                    {
                        'provider': 'provider-1',
                        'model': 'model-1',
                        'calls': 1,
                        'input_tokens': 4,
                        'output_tokens': 3,
                        'cache_read_input_tokens': 0,
                        'cache_creation_input_tokens': 0,
                    }
                ],
                'as_of': TS,
            }
        ),
    )
    client = api.client(private_data_store=InMemoryPrivateDataStore(), auto_detect_timezone=False)

    async for _event_item in client.stream_session(
        'ses_4', options=RunOptions(thread_id='thr_4', resume_from_position=3)
    ):
        pass
    await client.acancel('ses_4')
    await client.asession_usage('ses_4')
    # Forgetting private data touches only the local store and sends nothing.
    await client.aforget_private_data('thr_4')


THREAD_STATE: dict[str, Any] = {
    'thread_id': 'thr_1',
    'status': 'interrupted',
    'history': [
        {'message_id': 'msg_1', 'role': 'user', 'content': 'Plan the report.', 'ts': TS},
    ],
    'active_session_id': 'ses_1',
    'active_checkpoint_id': 'chk_1',
    'active_branch_id': 'brn_1',
    'checkpoints': [
        {
            'checkpoint_id': 'chk_1',
            'parent_checkpoint_id': 'chk_0',
            'branch_id': 'brn_1',
            'branch_from_message_id': 'msg_0',
            'status': 'active',
            'message_count': 1,
            'created_at': TS,
            'updated_at': TS,
        }
    ],
    'interrupt': {'interrupt_id': 'int_1', 'kind': 'tool_approval'},
    'created_at': TS,
    'updated_at': TS,
}


async def run_threads(api: FakeApi) -> None:
    """Start, continue, rewind, answer, read and replay one thread."""
    accepted = Reply(status=HTTPStatus.ACCEPTED, json=ACCEPTED_JSON)
    api.answer('POST', '/v1/threads', accepted)
    api.answer('POST', '/v1/threads/thr_1/messages', accepted)
    api.answer('POST', '/v1/threads/thr_1/time-travel', accepted)
    api.answer('POST', '/v1/threads/thr_1/approvals/int_1', accepted)
    api.answer('POST', '/v1/threads/thr_1/interrupts/int_2/responses', accepted)
    api.answer('GET', '/v1/threads/thr_1', Reply(json=THREAD_STATE))
    api.answer('GET', '/v1/threads/thr_1/events', Reply.sse(_final('ses_1')))
    api.answer('GET', '/v1/sessions/ses_1/events', Reply.sse(_final('ses_1')))
    client = api.client(private_data_store=InMemoryPrivateDataStore(), auto_detect_timezone=False)
    message = HumanMessage(message_id='msg_2', content='Continue the report.')
    tools = (LOOKUP_TOOL, PLAN_TOOL)

    await client.astart_thread(
        [HumanMessage(message_id='msg_1', content='Plan the report.')],
        options=_options(),
        tools=tools,
        private_data=PRIVATE_DATA,
        force_final_tool=True,
    )
    await client.apost_thread_message(
        'thr_1',
        message,
        options=_options(),
        tools=tools,
        private_data=PRIVATE_DATA,
        force_final_tool=True,
    )
    await client.atime_travel_thread(
        'thr_1',
        checkpoint_id='chk_1',
        message=message,
        branch_from_message_id='msg_0',
        options=_options(),
        tools=tools,
        private_data=PRIVATE_DATA,
        force_final_tool=True,
    )
    async for _event_item in client.atime_travel_thread_stream(
        'thr_1',
        checkpoint_id='chk_1',
        message=message,
        branch_from_message_id='msg_0',
        options=_options(),
        tools=tools,
        private_data=PRIVATE_DATA,
        force_final_tool=True,
    ):
        pass
    await client.asubmit_approval(
        'thr_1',
        'int_1',
        decision=ApprovalDecision(approved=True, decided_by='usr_1', reason='Looks right.'),
    )
    for answer in ('US East', True, ['east', 'west'], {'region': 'east'}):
        await client.asubmit_interrupt_response(
            'thr_1', 'int_2', answer=answer, responded_by='usr_1', surface='studio'
        )
    await client.aget_thread('thr_1')
    async for _event_item in client.athread_events(
        'thr_1', options=RunOptions(thread_id='thr_1', resume_from_position=3)
    ):
        pass


async def run_bound_thread(api: FakeApi) -> None:
    """Send, stream, replay and read one thread through its bound handle."""
    api.answer(
        'POST',
        '/v1/invoke',
        Reply(status=HTTPStatus.ACCEPTED, json={'session_id': 'ses_1', 'stream_position': 0}),
    )
    api.answer('GET', '/v1/sessions/ses_1/events', Reply.sse(_final('ses_1')))
    api.answer('GET', '/v1/threads/thr_1/events', Reply.sse(_final('ses_1')))
    api.answer('GET', '/v1/threads/thr_1', Reply(json=THREAD_STATE))
    client = api.client(private_data_store=InMemoryPrivateDataStore(), auto_detect_timezone=False)

    fresh = client.thread('thr_1', options=RunOptions(thread_id='thr_1', resume_from_position=1))
    async for _event_item in fresh.aevents():
        pass
    await fresh.astate()

    bound = client.thread('thr_1')
    await bound.asend('Plan the report.', options=_options())
    async for _event_item in bound.astream('Continue the report.', options=_options()):
        pass
    async for _event_item in bound.aevents():
        pass


def _decision_request(name: str) -> DecisionRequest:
    return DecisionRequest(
        name=name,
        state='A public support ticket about billing.',
        instructions='Choose the queue.',
        choices=(
            DecisionChoice(key='billing', description='Billing queue'),
            DecisionChoice(key='support', description='Support queue'),
        ),
        privacy='public',
    )


def _decision_reply(count: int) -> Reply:
    return Reply(
        json={
            'status': 'decided',
            'model': 'catalog-decision-model',
            'answers': [
                {'name': f'q{index}', 'status': 'accepted', 'value': 'billing', 'confidence': 0.9}
                for index in range(count)
            ],
            'usage': {'input_tokens': 8, 'output_tokens': 2},
        }
    )


async def _baseline(_request: DecisionRequest) -> DecisionAnswer:
    return DecisionAnswer(value='support', provider='local')


async def run_decisions(api: FakeApi) -> None:
    """Decide one request, then a native batch, through the platform decision route."""
    api.answer('POST', '/v1/decisions', _decision_reply(1), _decision_reply(2))
    decisions = DecisionClient(
        fallback=_baseline,
        config=DecisionConfig(),
        authorize=lambda _request: True,
        client=api.client(private_data_store=None, auto_detect_timezone=False),
    )

    await decisions.adecide(_decision_request('queue'))
    await decisions.abatch(
        [_decision_request('queue'), _decision_request('queue_again')], native_batch=True
    )


async def run_decision_run(api: FakeApi) -> None:
    """Open a decision-only run, decide inside it and complete it."""
    api.answer('POST', '/v1/decisions/runs', Reply(status=201, json={'session_id': 'ses_1'}))
    api.answer('POST', '/v1/decisions', _decision_reply(1))
    api.answer('POST', '/v1/decisions/runs/ses_1/complete', Reply(json={}))
    client = api.client(private_data_store=None, auto_detect_timezone=False)
    decisions = DecisionClient(
        fallback=_baseline,
        config=DecisionConfig(),
        authorize=lambda _request: True,
        client=client,
    )

    async def report() -> str:
        return (await decisions.adecide(_decision_request('queue'))).value

    await DecisionApp(report, name='Queue triage').aexecute(client=client)


DRIVERS = (
    run_invoke,
    run_stream_with_local_tools,
    run_cancel_on_close,
    run_sessions,
    run_threads,
    run_bound_thread,
    run_decisions,
    run_decision_run,
)
