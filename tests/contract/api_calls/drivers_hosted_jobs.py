"""Drive the explicit bounded job API through the real public SDK client."""

from maivn_contracts.scenarios import ScenarioAgentBinding

from maivn import HostedJobOptions, RunOptions
from tests.contract.api_calls.recording import FakeApi, Reply


async def run_hosted_jobs(api: FakeApi) -> None:
    """Record start, durable status and cancellation with every required bound."""
    session_id = 'job-' + 'a' * 32
    reply = {
        'session_id': session_id,
        'state': 'running',
        'deadline_at': '2026-10-22T23:00:00Z',
        'max_steps': 4,
        'steps_used': 1,
        'max_expense_usd': '0.50',
        'progress_position': 7,
        'terminal_reason': None,
        'final_message': None,
        'result': None,
    }
    api.answer('POST', '/v1/jobs', Reply(json=reply))
    api.answer('GET', '/v1/jobs/' + session_id, Reply(json=reply))
    api.answer(
        'POST',
        '/v1/jobs/' + session_id + '/cancel',
        Reply(json={**reply, 'state': 'cancelled', 'terminal_reason': 'cancelled'}),
    )
    client = api.client(private_data_store=None, auto_detect_timezone=False)
    job = await client.jobs.astart(
        ['Summarize supplied notes.'],
        job=HostedJobOptions(
            idempotency_key='bounded-once',
            max_steps=4,
            max_duration_seconds=30,
            registered_agent=ScenarioAgentBinding(agent_id='agt_1', version=1),
        ),
        options=RunOptions(max_expense_usd='0.50'),
    )
    await client.jobs.aget(job.session_id)
    await client.jobs.acancel(job.session_id)


DRIVERS = (run_hosted_jobs,)
