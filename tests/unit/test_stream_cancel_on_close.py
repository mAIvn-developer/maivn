"""Opt-in invocation cancellation uses the accepted root, independent of SSE delivery."""

from __future__ import annotations

import asyncio
import json
from http import HTTPStatus
from typing import TYPE_CHECKING

import httpx
import pytest

from maivn import Agent, Client
from maivn._internal import client as client_module

if TYPE_CHECKING:
    from collections.abc import AsyncIterator


_WAIT_SECONDS = 0.01
_ROOT = 'ses-accepted-root'
_CANCEL_PATH = f'/v1/sessions/{_ROOT}/cancel'


def _frame(event_type: str, *, nested: bool = False) -> bytes:
    data = {
        'type': event_type,
        'session_id': 'ses-child' if nested else _ROOT,
        'parent_session_id': _ROOT if nested else None,
        'payload': {'response': 'done'},
    }
    return f'id: 1\nevent: {event_type}\ndata: {json.dumps(data)}\n\n'.encode()


class _Sse(httpx.AsyncByteStream):
    """Stream can block before its first event or fail with a known exception."""

    def __init__(
        self, frame: bytes = b'', error: Exception | None = None, *, complete: bool = False
    ) -> None:
        self.started = asyncio.Event()
        self.closed = False
        self.frame = frame
        self.error = error
        self.complete = complete

    async def __aiter__(self) -> AsyncIterator[bytes]:
        self.started.set()
        if self.error is not None:
            raise self.error
        if self.frame:
            yield self.frame
        if not self.complete:
            await asyncio.Event().wait()

    async def aclose(self) -> None:
        self.closed = True


class _Transport:
    """Records authenticated cancellation and accepts only the expected public routes."""

    def __init__(
        self,
        stream: _Sse,
        cancel_status: HTTPStatus = HTTPStatus.OK,
        *,
        stall_cancel: bool = False,
    ) -> None:
        self.stream = stream
        self.cancel_status = cancel_status
        self.stall_cancel = stall_cancel
        self.cancels: list[httpx.Request] = []
        self.cancel_interrupted = False

    async def handle(self, request: httpx.Request) -> httpx.Response:
        if request.url.path == '/v1/invoke':
            assert 'cancel_on_close' not in json.loads(request.content)
            return httpx.Response(
                HTTPStatus.ACCEPTED, json={'session_id': _ROOT, 'stream_position': 0}
            )
        if request.url.path == f'/v1/sessions/{_ROOT}/events':
            return httpx.Response(
                HTTPStatus.OK,
                headers={'content-type': 'text/event-stream'},
                stream=self.stream,
            )
        assert request.url.path == _CANCEL_PATH
        assert request.method == 'POST'
        assert request.headers['authorization'] == 'Bearer test-key'
        assert json.loads(request.content) == {}
        self.cancels.append(request)
        if self.stall_cancel:
            try:
                await asyncio.Event().wait()
            finally:
                self.cancel_interrupted = True
        return httpx.Response(
            self.cancel_status,
            json={'code': 'invoke_not_running', 'detail': 'secret cancellation response'},
        )

    def client(self) -> Client:
        return Client(
            api_key='test-key',
            base_url='http://testserver',
            transport=httpx.MockTransport(self.handle),
        )


@pytest.mark.parametrize('agent_scope', [False, True])
def test_opt_in_early_close_cancels_the_accepted_root_once(*, agent_scope: bool) -> None:
    """A nested event cannot replace the accepted root used by Client or Agent cleanup."""
    transport = _Transport(_Sse(_frame('status', nested=True)))
    client = transport.client()
    owner = Agent(name='cancel-test', client=client) if agent_scope else client

    async def exercise() -> None:
        stream = owner.astream('hello', cancel_on_close=True)
        await anext(stream)
        await stream.aclose()
        await stream.aclose()

    asyncio.run(exercise())
    assert len(transport.cancels) == 1
    assert transport.stream.closed


@pytest.mark.parametrize('task_cancel', [False, True])
@pytest.mark.parametrize('cancel_status', [HTTPStatus.OK, HTTPStatus.INTERNAL_SERVER_ERROR])
def test_opt_in_cancels_after_acceptance_before_the_first_sse_event(
    cancel_status: HTTPStatus, *, task_cancel: bool
) -> None:
    """An idle timeout or task cancellation cannot strand a silently accepted invocation."""
    transport = _Transport(_Sse(), cancel_status)

    async def exercise() -> None:
        stream = transport.client().astream('hello', cancel_on_close=True)
        pending = asyncio.create_task(anext(stream))
        await asyncio.wait_for(transport.stream.started.wait(), timeout=1)
        if task_cancel:
            pending.cancel()
            with pytest.raises(asyncio.CancelledError):
                await pending
        else:
            with pytest.raises(asyncio.TimeoutError):
                await asyncio.wait_for(pending, timeout=_WAIT_SECONDS)
        await stream.aclose()

    asyncio.run(exercise())
    assert len(transport.cancels) == 1
    assert transport.stream.closed


@pytest.mark.parametrize('cancel_status', [HTTPStatus.CONFLICT, HTTPStatus.INTERNAL_SERVER_ERROR])
def test_failed_cancellation_preserves_the_original_stream_exception(
    cancel_status: HTTPStatus, caplog: pytest.LogCaptureFixture
) -> None:
    """A terminal race or failed cleanup neither hides the original error nor logs values."""
    original = httpx.ReadError('secret original stream failure')
    transport = _Transport(_Sse(error=original), cancel_status)

    async def exercise() -> None:
        stream = transport.client().astream('hello', cancel_on_close=True)
        with pytest.raises(httpx.ReadError) as caught:
            await anext(stream)
        assert caught.value is original
        await stream.aclose()

    asyncio.run(exercise())
    assert len(transport.cancels) == 1
    assert 'secret' not in caplog.text
    if cancel_status == HTTPStatus.INTERNAL_SERVER_ERROR:
        assert '500' in caplog.text


def test_cancellation_cleanup_is_bounded_and_leaves_no_pending_request(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A hung cancel POST is cancelled and awaited rather than abandoned in a background task."""
    monkeypatch.setattr(client_module, '_STREAM_CANCEL_TIMEOUT_SECONDS', _WAIT_SECONDS)
    transport = _Transport(_Sse(_frame('status')), stall_cancel=True)

    async def exercise() -> None:
        stream = transport.client().astream('hello', cancel_on_close=True)
        await anext(stream)
        await asyncio.wait_for(stream.aclose(), timeout=1)
        assert asyncio.all_tasks() == {asyncio.current_task()}

    asyncio.run(exercise())
    assert len(transport.cancels) == 1
    assert transport.cancel_interrupted
    assert transport.stream.closed


@pytest.mark.parametrize('event_type', ['final', 'error'])
@pytest.mark.parametrize('complete', [False, True])
def test_root_terminal_event_does_not_send_cancellation(event_type: str, *, complete: bool) -> None:
    """Closing immediately after a root terminal event must not cancel a completed invocation."""
    transport = _Transport(_Sse(_frame(event_type), complete=complete))

    async def exercise() -> None:
        stream = transport.client().astream('hello', cancel_on_close=True)
        if complete:
            assert [event async for event in stream]
        else:
            await anext(stream)
        await stream.aclose()

    asyncio.run(exercise())
    assert transport.cancels == []


def test_nested_final_event_does_not_exempt_the_active_root() -> None:
    """A child completion leaves the root invocation subject to opt-in cancellation."""
    transport = _Transport(_Sse(_frame('final', nested=True)))

    async def exercise() -> None:
        stream = transport.client().astream('hello', cancel_on_close=True)
        await anext(stream)
        await stream.aclose()

    asyncio.run(exercise())
    assert len(transport.cancels) == 1


def test_default_early_close_preserves_the_live_invocation_for_replay() -> None:
    """Existing Client callers still close the SSE connection without cancelling the run."""
    transport = _Transport(_Sse(_frame('status')))

    async def exercise() -> None:
        stream = transport.client().astream('hello')
        await anext(stream)
        await stream.aclose()

    asyncio.run(exercise())
    assert transport.cancels == []
    assert transport.stream.closed
