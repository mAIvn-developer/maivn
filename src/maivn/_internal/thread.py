"""A fixed-identity handle for repeated invocations on one durable thread."""

from __future__ import annotations

from contextlib import contextmanager
from threading import Lock
from typing import TYPE_CHECKING, Protocol, cast
from uuid import uuid4

from maivn._internal.api.async_stream import stream_async_iterator
from maivn._internal.models import InvokeResponse, RunOptions, StreamEvent, ThreadState

if TYPE_CHECKING:
    from collections.abc import (
        AsyncGenerator,
        AsyncIterator,
        Awaitable,
        Callable,
        Generator,
        Iterator,
    )

    from maivn.messages import SdkMessagesInput


class _SyncInvoke(Protocol):
    def __call__(
        self,
        messages: SdkMessagesInput,
        *,
        options: RunOptions | None,
        **kwargs: object,
    ) -> InvokeResponse: ...


class _AsyncInvoke(Protocol):
    def __call__(
        self,
        messages: SdkMessagesInput,
        *,
        options: RunOptions | None,
        **kwargs: object,
    ) -> Awaitable[InvokeResponse]: ...


class _SyncStream(Protocol):
    def __call__(
        self,
        messages: SdkMessagesInput,
        *,
        options: RunOptions | None,
        **kwargs: object,
    ) -> Iterator[StreamEvent]: ...


class _AsyncStream(Protocol):
    def __call__(
        self,
        messages: SdkMessagesInput,
        *,
        options: RunOptions | None,
        **kwargs: object,
    ) -> AsyncIterator[StreamEvent]: ...


class _SyncThreadEvents(Protocol):
    def __call__(self, thread_id: str, *, options: RunOptions) -> Iterator[StreamEvent]: ...


class _AsyncThreadEvents(Protocol):
    def __call__(self, thread_id: str, *, options: RunOptions) -> AsyncIterator[StreamEvent]: ...


class _GetThread(Protocol):
    def __call__(self, thread_id: str) -> ThreadState: ...


class _AGetThread(Protocol):
    def __call__(self, thread_id: str) -> Awaitable[ThreadState]: ...


class _ThreadOwner(Protocol):
    """The shared thread-capable subset of Client, Agent, and Swarm."""

    invoke: _SyncInvoke
    ainvoke: _AsyncInvoke
    stream: _SyncStream
    astream: _AsyncStream
    thread_events: _SyncThreadEvents
    athread_events: _AsyncThreadEvents
    get_thread: _GetThread
    aget_thread: _AGetThread


class _Close(Protocol):
    def __call__(self) -> None: ...


class _AsyncClose(Protocol):
    def __call__(self) -> Awaitable[None]: ...


class BoundThread:
    """Route repeated owner operations through one immutable thread id.

    A handle is local state only: construction never starts a backend thread.
    Its replay cursor belongs to the current active turn; make a fresh handle
    after another client changes that thread's active session. Do not consume
    two sends or streams concurrently from the same handle.
    """

    def __init__(
        self,
        owner: object,
        thread_id: str | None = None,
        *,
        options: RunOptions | None = None,
        pass_thread_id: bool = False,
        session_events: Callable[[str, RunOptions], AsyncIterator[StreamEvent]] | None = None,
    ) -> None:
        option_thread_id = None if options is None else options.thread_id
        for candidate in (thread_id, option_thread_id):
            if candidate is not None and not candidate.strip():
                message = 'thread_id must not be blank'
                raise ValueError(message)
        if thread_id is not None and option_thread_id is not None and thread_id != option_thread_id:
            message = 'explicit thread_id conflicts with options.thread_id'
            raise ValueError(message)
        selected_thread_id = thread_id or option_thread_id or f'thr-{uuid4()}'
        self._owner = cast('_ThreadOwner', owner)
        self._thread_id = selected_thread_id
        self._handle_options = options
        self._pass_thread_id = pass_thread_id
        self._session_events = session_events
        self._session_id: str | None = None
        self._resume_from_position = 0 if options is None else options.resume_from_position
        self._operation_lock = Lock()

    @property
    def thread_id(self) -> str:
        """Return the immutable durable thread identity."""
        return self._thread_id

    @property
    def resume_from_position(self) -> int:
        """Return the latest event position retained for the next replay."""
        return self._resume_from_position

    def send(
        self,
        messages: SdkMessagesInput,
        *,
        options: RunOptions | None = None,
        **invoke_kwargs: object,
    ) -> InvokeResponse:
        """Invoke the owner and wait for its full response on this thread."""
        kwargs = self._invoke_kwargs(invoke_kwargs)
        with self._active_operation():
            response = self._owner.invoke(
                messages,
                options=self._invocation_options(options),
                **self._bound_invoke_kwargs(kwargs),
            )
            self._record_response_session(response)
            return response

    async def asend(
        self,
        messages: SdkMessagesInput,
        *,
        options: RunOptions | None = None,
        **invoke_kwargs: object,
    ) -> InvokeResponse:
        """Invoke the owner asynchronously and wait for its full response."""
        kwargs = self._invoke_kwargs(invoke_kwargs)
        with self._active_operation():
            response = await self._owner.ainvoke(
                messages,
                options=self._invocation_options(options),
                **self._bound_invoke_kwargs(kwargs),
            )
            self._record_response_session(response)
            return response

    def stream(
        self,
        messages: SdkMessagesInput,
        *,
        options: RunOptions | None = None,
        **invoke_kwargs: object,
    ) -> Generator[StreamEvent, None, None]:
        """Stream an owner invocation while retaining the last observed position."""
        kwargs = self._invoke_kwargs(invoke_kwargs)

        def iterator() -> Generator[StreamEvent, None, None]:
            with self._active_operation():
                source = self._owner.stream(
                    messages,
                    options=self._invocation_options(options),
                    **self._bound_invoke_kwargs(kwargs),
                )
                try:
                    for event in source:
                        self._observe(event)
                        yield event
                finally:
                    _close_iterator(source)

        return iterator()

    async def astream(
        self,
        messages: SdkMessagesInput,
        *,
        options: RunOptions | None = None,
        **invoke_kwargs: object,
    ) -> AsyncGenerator[StreamEvent, None]:
        """Stream an owner invocation asynchronously with cursor retention."""
        kwargs = self._invoke_kwargs(invoke_kwargs)
        with self._active_operation():
            source = self._owner.astream(
                messages,
                options=self._invocation_options(options),
                **self._bound_invoke_kwargs(kwargs),
            )
            try:
                async for event in source:
                    self._observe(event)
                    yield event
            finally:
                await _aclose_iterator(source)

    def events(self, *, options: RunOptions | None = None) -> Generator[StreamEvent, None, None]:
        """Replay this thread's events from the retained cursor."""

        def iterator() -> Generator[StreamEvent, None, None]:
            with self._active_operation():
                resolved_options = self._event_options(options)
                source = self._event_source(resolved_options)
                try:
                    for event in source:
                        self._observe(event)
                        yield event
                finally:
                    _close_iterator(source)

        return iterator()

    async def aevents(
        self, *, options: RunOptions | None = None
    ) -> AsyncGenerator[StreamEvent, None]:
        """Replay this thread's events asynchronously from the retained cursor."""
        with self._active_operation():
            resolved_options = self._event_options(options)
            source = self._async_event_source(resolved_options)
            try:
                async for event in source:
                    self._observe(event)
                    yield event
            finally:
                await _aclose_iterator(source)

    def state(self) -> ThreadState:
        """Return the current durable state for this thread."""
        return self._owner.get_thread(self._thread_id)

    async def astate(self) -> ThreadState:
        """Return the current durable state for this thread asynchronously."""
        return await self._owner.aget_thread(self._thread_id)

    @contextmanager
    def _active_operation(self) -> Generator[None, None, None]:
        if not self._operation_lock.acquire(blocking=False):
            message = 'a BoundThread permits only one active send or stream operation'
            raise RuntimeError(message)
        try:
            yield
        finally:
            self._operation_lock.release()

    def _invocation_options(self, options: RunOptions | None) -> RunOptions | None:
        """Start each new owner invocation at the active session's first event."""
        resolved_options = (
            self._merged_explicit_options(options)
            if self._pass_thread_id
            else self._client_options(options, resume_from_position=0)
        )
        if resolved_options is not None:
            resolved_options = resolved_options.model_copy(update={'resume_from_position': 0})
        self._session_id = None
        self._resume_from_position = 0
        return resolved_options

    def _event_source(self, options: RunOptions) -> Iterator[StreamEvent]:
        """Return replay for the observed root session when one is available."""
        session_id = self._session_id
        session_events = self._session_events
        if session_id is None or session_events is None:
            return self._owner.thread_events(self._thread_id, options=options)
        return stream_async_iterator(lambda: session_events(session_id, options))

    def _async_event_source(self, options: RunOptions) -> AsyncIterator[StreamEvent]:
        """Return asynchronous replay pinned to the observed root session."""
        session_id = self._session_id
        session_events = self._session_events
        if session_id is None or session_events is None:
            return self._owner.athread_events(self._thread_id, options=options)
        return session_events(session_id, options)

    def _event_options(self, options: RunOptions | None) -> RunOptions:
        """Resume active-turn event replay, honoring an explicit caller reset."""
        requested_cursor = self._resume_from_position
        if options is not None and 'resume_from_position' in options.model_fields_set:
            requested_cursor = options.resume_from_position
        resolved_options = self._client_options(options, resume_from_position=requested_cursor)
        self._resume_from_position = requested_cursor
        return resolved_options

    def _client_options(
        self,
        options: RunOptions | None,
        *,
        resume_from_position: int,
    ) -> RunOptions:
        explicit = self._merged_explicit_options(options)
        if explicit is None:
            return RunOptions(thread_id=self._thread_id, resume_from_position=resume_from_position)
        return explicit.model_copy(
            update={'thread_id': self._thread_id, 'resume_from_position': resume_from_position}
        )

    def _merged_explicit_options(self, options: RunOptions | None) -> RunOptions | None:
        if options is not None and options.thread_id not in (None, self._thread_id):
            message = 'options.thread_id conflicts with this bound thread'
            raise ValueError(message)
        payload: dict[str, object] = {}
        for source in (self._handle_options, options):
            if source is None:
                continue
            for name in source.model_fields_set:
                if name == 'thread_id':
                    continue
                payload[name] = getattr(source, name)
        if not payload:
            return None
        return RunOptions.model_validate(payload)

    def _invoke_kwargs(self, invoke_kwargs: dict[str, object]) -> dict[str, object]:
        if 'thread_id' in invoke_kwargs:
            message = 'thread_id is fixed by BoundThread; use the handle identity instead'
            raise ValueError(message)
        return invoke_kwargs

    def _bound_invoke_kwargs(self, invoke_kwargs: dict[str, object]) -> dict[str, object]:
        if not self._pass_thread_id:
            return invoke_kwargs
        return {**invoke_kwargs, 'thread_id': self._thread_id}

    def _observe(self, event: StreamEvent) -> None:
        self._resume_from_position = max(self._resume_from_position, event.position)
        if event.data.get('parent_session_id') is None:
            self._record_session_id(event.data.get('session_id'))

    def _record_response_session(self, response: InvokeResponse) -> None:
        self._record_session_id(getattr(response, 'session_id', None))

    def _record_session_id(self, value: object) -> None:
        if isinstance(value, str) and value:
            self._session_id = value


def _close_iterator(iterator: Iterator[StreamEvent]) -> None:
    close = getattr(iterator, 'close', None)
    if callable(close):
        cast('_Close', close)()


async def _aclose_iterator(iterator: AsyncIterator[StreamEvent]) -> None:
    close = getattr(iterator, 'aclose', None)
    if callable(close):
        await cast('_AsyncClose', close)()


__all__ = ['BoundThread']
