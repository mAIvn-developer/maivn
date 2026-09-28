"""Failures retain identity without changing exceptions or replaying admitted work."""

from __future__ import annotations

import asyncio
import json
import subprocess
import sys
from typing import TYPE_CHECKING

import httpx
import pytest
from pydantic import AnyUrl

from maivn import Agent, Client, ClientConfig, MaivnHTTPError, MaivnSDKError
from maivn._internal.compat.invocation import response_from_stream_events
from maivn._internal.error_diagnostics import (
    attach_error_diagnostics,
    diagnostic_facts,
    error_action_hint,
    safe_error_message,
)
from maivn._internal.models import StreamEvent
from maivn._internal.reporting.stream_events import StreamEventProjector
from maivn._internal.reporting.terminal_reporter.reporters.simple_reporter import SimpleReporter
from maivn.events import AppEvent, NormalizedEventForwardingState, forward_normalized_event
from maivn.events._models import NormalizedStreamState
from maivn.events._normalize.context import NormalizationOptions
from maivn.events._normalize.lifecycle_events import handle_error_event

if TYPE_CHECKING:
    from collections.abc import AsyncIterator


@pytest.mark.parametrize('after_frame', [False, True])
def test_public_invoke_keeps_same_transport_error_after_acceptance(*, after_frame: bool) -> None:
    """A failed GET or partial SSE is traceable even without a terminal error receipt."""
    failure = httpx.ReadError('connection interrupted')
    requests: list[str] = []

    class BrokenStream(httpx.AsyncByteStream):
        async def __aiter__(self) -> AsyncIterator[bytes]:
            if after_frame:
                frame: dict[str, object] = {
                    'session_id': 'ses-admitted',
                    'root_event_id': 'evt-root',
                    'payload': {},
                }
                yield f'id: 1\nevent: status\ndata: {json.dumps(frame)}\n\n'.encode()
                # A delegated child must never replace the accepted root identity.
                child = frame | {
                    'session_id': 'ses-child',
                    'root_event_id': 'evt-child',
                    'parent_session_id': 'ses-admitted',
                }
                yield f'id: 2\nevent: status\ndata: {json.dumps(child)}\n\n'.encode()
            raise failure

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request.method)
        if request.method == 'POST':
            return httpx.Response(202, json={'session_id': 'ses-admitted', 'stream_position': 0})
        if not after_frame:
            raise failure
        return httpx.Response(200, stream=BrokenStream())

    client = Client(
        config=ClientConfig(api_key='test-key', base_url=AnyUrl('http://testserver')),
        transport=httpx.MockTransport(handler),
    )
    agent = Agent(name='diagnostic', client=client)
    with pytest.raises(httpx.ReadError) as caught:
        asyncio.run(agent.ainvoke('hello'))
    assert caught.value is failure
    assert failure.args == ('connection interrupted',)
    assert getattr(failure, 'session_id', None) == 'ses-admitted'
    assert getattr(failure, 'root_event_id', None) == ('evt-root' if after_frame else None)
    assert getattr(failure, 'error_code', None) == 'stream_transport_error'
    assert requests == ['POST', 'GET']


def test_failure_before_acceptance_has_no_invented_identity() -> None:
    """The initial POST's original network failure has no accepted-session metadata."""
    failure = httpx.ConnectError('offline')

    def handler(request: httpx.Request) -> httpx.Response:
        del request
        raise failure

    client = Client(
        config=ClientConfig(api_key='test-key', base_url=AnyUrl('http://testserver')),
        transport=httpx.MockTransport(handler),
    )
    with pytest.raises(httpx.ConnectError) as caught:
        client.invoke('hello')
    assert caught.value is failure
    assert getattr(failure, 'session_id', None) is None
    assert getattr(failure, 'root_event_id', None) is None


def test_caller_cancellation_after_acceptance_retains_session() -> None:
    """Cancellation still propagates and carries accepted identity without replay."""
    started = asyncio.Event()

    class WaitingStream(httpx.AsyncByteStream):
        async def __aiter__(self) -> AsyncIterator[bytes]:
            started.set()
            await asyncio.Event().wait()
            yield b''

    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == 'POST':
            return httpx.Response(202, json={'session_id': 'ses-cancelled', 'stream_position': 0})
        return httpx.Response(200, stream=WaitingStream())

    client = Client(
        config=ClientConfig(api_key='test-key', base_url=AnyUrl('http://testserver')),
        transport=httpx.MockTransport(handler),
    )

    async def run() -> None:
        failures: list[asyncio.CancelledError] = []

        async def invoke() -> None:
            try:
                await client.ainvoke('hello')
            except asyncio.CancelledError as exc:
                failures.append(exc)
                raise

        task = asyncio.create_task(invoke())
        await started.wait()
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert task.cancelled()
        # Python may recreate CancelledError at Task.__await__; assert the
        # exception delivered by the SDK inside the invoking coroutine.
        assert getattr(failures[0], 'session_id', None) == 'ses-cancelled'
        assert getattr(failures[0], 'error_code', None) == 'cancelled'

    asyncio.run(run())


def test_existing_error_metadata_and_text_win() -> None:
    """Additive metadata cannot erase already attached optional public identifiers."""
    failure = MaivnSDKError('original detail')
    failure.session_id = 'ses-existing'
    failure.root_event_id = 'evt-existing'
    failure.error_code = 'existing_code'
    attach_error_diagnostics(
        failure,
        {
            'session_id': 'ses-other',
            'root_event_id': 'evt-other',
            'error_code': 'other_code',
            'attempt_id': 'attempt-1',
        },
    )
    assert failure.args == ('original detail',)
    assert (failure.session_id, failure.root_event_id, failure.error_code) == (
        'ses-existing',
        'evt-existing',
        'existing_code',
    )


def test_foreign_error_metadata_cannot_mask_original_failure() -> None:
    """Third-party exceptions may own these names or reject attribute writes."""

    class ForeignCorrelationError(Exception):
        correlation = 'provider-owned-value'

    class ReadOnlyIdentityError(Exception):
        @property
        def session_id(self) -> None:
            return None

    foreign = ForeignCorrelationError('original')
    for failure in (foreign, ReadOnlyIdentityError('original')):
        attach_error_diagnostics(failure, {'session_id': 'ses-accepted'})
        assert failure.args == ('original',)
    assert foreign.correlation == 'provider-owned-value'


def test_terminal_code_is_separate_from_explicit_error_detail() -> None:
    """The stable code can drive presentation while exception handlers keep their detail."""
    event = StreamEvent(
        position=2,
        event_type='error',
        data={
            'session_id': 'ses-terminal',
            'root_event_id': 'evt-root',
            'payload': {
                'message': 'original detail',
                'error_code': 'invalid_arguments',
                'attempt_id': 'attempt-1',
                'attempt_index': 1,
            },
        },
    )
    with pytest.raises(MaivnSDKError) as caught:
        response_from_stream_events([event])
    assert caught.value.args == ('original detail',)
    assert caught.value.error_code == 'invalid_arguments'
    assert caught.value.correlation is not None
    assert caught.value.correlation['attempt_id'] == 'attempt-1'


def test_valid_domain_refusal_result_stays_a_result() -> None:
    """An unsuccessful business result is valid output, never an infrastructure error."""
    refusal = {'approved': False, 'error_code': 'business_refused', 'reason': 'limit'}
    event = StreamEvent(
        position=2,
        event_type='final',
        data={
            'session_id': 'ses-business',
            'root_event_id': 'evt-root',
            'ts': '2026-09-15T00:00:00Z',
            'event_id': 'evt-final',
            'payload': {'message': {'role': 'assistant', 'content': ''}, 'result': refusal},
        },
    )
    assert response_from_stream_events([event]).result == refusal


def test_verbose_reporter_exposes_safe_identity_and_hint(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Human copy changes intentionally; canonical event data and raw detail stay intact."""
    event = StreamEvent(
        position=1,
        event_type='error',
        data={
            'session_id': 'ses-display',
            'root_event_id': 'evt-root',
            'payload': {'error_code': 'invalid_arguments', 'message': 'Traceback /secret/path'},
        },
    )
    SimpleReporter(enabled=True).report_event(StreamEventProjector().project(event))
    output = capsys.readouterr().out
    assert 'Check arguments and the expected output schema.' in output
    assert 'session_id=ses-display' in output
    assert '/secret/path' not in output
    assert event.data['payload']['message'] == 'Traceback /secret/path'


def test_http_diagnostics_preserve_existing_alias_and_detail() -> None:
    """HTTP code remains available under its existing name as well as the additive one."""
    error = MaivnHTTPError(status_code=403, reason='denied', code='forbidden', detail={'opaque': 1})
    assert error.code == error.error_code == 'forbidden'
    assert error.detail == {'opaque': 1}
    assert error.args == ('mAIvn API returned 403: denied',)


def test_only_structural_facts_are_disclosed() -> None:
    """Arbitrary detail fields, multiline identities and boolean indices are excluded."""
    assert diagnostic_facts(
        {
            'prompt': 'secret',
            'error_code': 'invalid_arguments',
            'attempt_id': 'secret\nvalue',
            'attempt_index': True,
        }
    ) == {
        'error_code': 'invalid_arguments',
    }
    long_message = 'x' * 10000
    assert len(safe_error_message(long_message)) < len(long_message)
    assert 'settlement' in error_action_hint('usage_pending')
    assert 'completed before cancellation' in error_action_hint('cancelled')


def test_sdk_import_needs_no_optional_observability() -> None:
    """An ordinary SDK install imports while optional server tracing packages are unavailable."""
    script = f"""import sys, builtins
sys.path[:0] = {sys.path!r}
original_import = builtins.__import__
def guarded_import(name, *args, **kwargs):
    if name.split('.')[0] in {{'maivn_observability', 'opentelemetry'}}:
        raise ModuleNotFoundError(name)
    return original_import(name, *args, **kwargs)
builtins.__import__ = guarded_import
import maivn
assert maivn.MaivnSDKError('detail').args == ('detail',)
"""
    result = subprocess.run(  # noqa: S603 - fixed interpreter and authored import-only script.
        [sys.executable, '-B', '-c', script],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr


def test_recovered_hop_presentation_is_successful(capsys: pytest.CaptureFixture[str]) -> None:
    """A fallback is visible and correlated without claiming the recovered run failed."""
    reporter = SimpleReporter(enabled=True)
    reporter.report_event(
        StreamEvent(
            position=1,
            event_type='model_routing',
            data={
                'payload': {
                    'mode': 'hop_recovered',
                    'model': 'model-backup',
                    'attempt_id': 'attempt-2',
                    'attempt_index': 1,
                },
            },
        )
    )
    output = capsys.readouterr().out
    assert 'Recovered using model-backup' in output
    assert 'attempt_id=attempt-2' in output
    assert 'Agent execution failed' not in output


def test_normalized_error_forwarding_preserves_code_and_safe_facts() -> None:
    """Legacy normalization cannot drop the stable code needed by its terminal consumer."""
    event = StreamEvent(
        position=1,
        event_type='error',
        data={
            'session_id': 'ses-normalized',
            'root_event_id': 'evt-root',
            'payload': {
                'message': 'Traceback /secret/path',
                'error_code': 'invalid_arguments',
                'attempt_id': 'attempt-1',
            },
        },
    )
    projected = StreamEventProjector().project(event)
    normalized = handle_error_event(
        projected.payload,
        NormalizedStreamState(),
        NormalizationOptions(),
    )[0]
    app_event = AppEvent.model_validate(normalized)
    received: list[tuple[str, dict[str, object] | None]] = []

    class Reporter:
        def print_event(
            self,
            event_type: str,
            message: str,
            details: dict[str, object] | None = None,
        ) -> None:
            assert event_type == 'error'
            received.append((message, details))

    asyncio.run(
        forward_normalized_event(
            app_event,
            reporter=Reporter(),
            state=NormalizedEventForwardingState(),
        )
    )
    assert 'Check arguments and the expected output schema.' in received[0][0]
    assert '/secret/path' not in received[0][0]
    assert received[0][1] is not None
    assert received[0][1]['error_code'] == 'invalid_arguments'
    assert received[0][1]['session_id'] == 'ses-normalized'


@pytest.mark.parametrize('complete', [True, False])
def test_terminal_provider_counts_do_not_claim_settlement(
    capsys: pytest.CaptureFixture[str],
    *,
    complete: bool,
) -> None:
    """Complete provider counts in a run block still leave later session settlement pending."""
    provider: dict[str, object] = {
        'scope': 'run',
        'settled': False,
        'provider_counts_complete': complete,
        'calls': 1,
        'input_tokens': 1,
        'output_tokens': 1,
        'cache_read_input_tokens': 0,
        'cache_creation_input_tokens': 0,
        'by_model': [],
    }
    event = StreamEvent(
        position=2,
        event_type='final',
        data={
            'session_id': 'ses-usage',
            'root_event_id': 'evt-root',
            'ts': '2026-09-15T00:00:00Z',
            'event_id': 'evt-final',
            'payload': {
                'message': {'role': 'assistant', 'content': 'done'},
                'usage': {'provider': provider},
            },
        },
    )
    SimpleReporter(enabled=True).report_response(response_from_stream_events([event]))
    output = capsys.readouterr().out
    assert (
        f'Provider counts {"complete" if complete else "incomplete"}; settlement pending.' in output
    )
