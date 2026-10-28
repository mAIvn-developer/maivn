"""V2 public platform HTTP client."""

from __future__ import annotations

import asyncio
import logging
import os
from http import HTTPStatus
from importlib import import_module
from typing import TYPE_CHECKING, cast
from uuid import uuid4

import httpx

from maivn._internal.api.async_stream import (
    close_async_iterator,
    run_blocking,
    stream_async_iterator,
)
from maivn._internal.artifacts import ArtifactsClient
from maivn._internal.automatic_vault import AUTO_VAULT, AutomaticVault, AutoVault, LocalVaultConfig
from maivn._internal.client_stream import (
    hydrated_client_event as _hydrated_client_event,
)
from maivn._internal.client_stream import (
    hydrated_client_events as _hydrated_client_events,
)
from maivn._internal.client_stream import (
    interrupt_response as _interrupt_response,
)
from maivn._internal.client_stream import (
    local_resolution_data as _local_resolution_data,
)
from maivn._internal.client_stream import (
    report_invoke_accepted as _report_invoke_accepted,
)
from maivn._internal.client_stream import (
    resolve_canonical_tool_interrupts as _resolve_canonical_tool_interrupts,
)
from maivn._internal.client_stream import (
    single_thread_message as _single_thread_message,
)
from maivn._internal.client_stream import (
    stream_with_local_tools as _stream_with_local_tools,
)
from maivn._internal.client_stream import (
    telemetry_client_events as _telemetry_client_events,
)
from maivn._internal.compat.invocation import response_from_stream_events
from maivn._internal.compat.privacy import (
    CoverRegion,
    PIIWhitelist,
    PrivateData,
    RedactedMessage,
    RedactionPreviewRequest,
    RedactionPreviewResponse,
    validate_approved_messages,
)
from maivn._internal.config import ClientConfig
from maivn._internal.error_diagnostics import attach_error_diagnostics, diagnostic_facts
from maivn._internal.errors import MaivnHTTPError
from maivn._internal.hosted_jobs import HostedJobsClient
from maivn._internal.inspection import capture_invoke_inspection
from maivn._internal.memory import MemoryClient
from maivn._internal.models import (
    ApprovalDecision,
    InvokeAccepted,
    InvokeResponse,
    JsonObject,
    ProviderUsage,
    RunOptions,
    StreamEvent,
    ThreadAccepted,
    ThreadState,
)
from maivn._internal.private_artifacts import PrivateArtifactsClient
from maivn._internal.private_data_store import (
    PrivateDataStore,
    merged_private_data,
    persistable_pairs,
)
from maivn._internal.reporting.stream_events import StreamEventProjector
from maivn._internal.resources import ResourcesClient
from maivn._internal.skills import SkillsClient
from maivn._internal.thread import BoundThread
from maivn._internal.tool_runtime import LocalToolRuntime
from maivn._internal.tracing import sdk_operation
from maivn._internal.transport.http import HttpJsonClient, close_stream_response
from maivn._internal.transport.leases import LoopLocalTransport
from maivn._internal.transport.sse_client import iter_sse_events
from maivn._internal.wire import (
    INVOKE_PATH,
    SESSION_CANCEL_PATH,
    SESSION_EVENTS_PATH,
    SESSION_USAGE_PATH,
    THREAD_APPROVALS_PATH,
    THREAD_EVENTS_PATH,
    THREAD_INTERRUPT_RESPONSES_PATH,
    THREAD_MESSAGES_PATH,
    THREAD_STATE_PATH,
    THREAD_TIME_TRAVEL_PATH,
    THREADS_PATH,
    invocation_options,
    invocation_tools,
    message_payloads,
    optional_run_options,
    resume_params,
    run_config_payload,
    skill_selection_payload,
    swarm_roster_payload,
)
from maivn.messages import (
    AttachmentInput,
    SdkMessagesInput,
    coerce_attachment_input,
    to_contract_messages,
)

if TYPE_CHECKING:
    from collections.abc import (
        AsyncGenerator,
        AsyncIterator,
        Callable,
        Generator,
        Mapping,
        Sequence,
    )
    from pathlib import Path

    from maivn._internal.models import ToolMetadata


logger = logging.getLogger(__name__)
_STREAM_CANCEL_TIMEOUT_SECONDS = 2.0


class Client:
    """HTTP client for the v2 mAIvn API surface."""

    def preview_redaction(  # noqa: PLR0913 - mirrors preview contract.
        self,
        prompt: str | RedactedMessage | list[RedactedMessage],
        *,
        attachments: list[AttachmentInput] | None = None,
        resources: list[str] | None = None,
        pii_whitelist: PIIWhitelist | None = None,
        known_pii_values: Sequence[str | PrivateData] | None = None,
        cover_regions: list[CoverRegion] | None = None,
        timeout_seconds: float = 300.0,
    ) -> RedactionPreviewResponse:
        """Preview the shielded copy without starting a run."""
        return run_blocking(
            lambda: self.apreview_redaction(
                prompt,
                attachments=attachments,
                resources=resources,
                pii_whitelist=pii_whitelist,
                known_pii_values=known_pii_values,
                cover_regions=cover_regions,
                timeout_seconds=timeout_seconds,
            )
        )

    async def apreview_redaction(  # noqa: PLR0913 - mirrors preview contract.
        self,
        prompt: str | RedactedMessage | list[RedactedMessage],
        *,
        attachments: list[AttachmentInput] | None = None,
        resources: list[str] | None = None,
        pii_whitelist: PIIWhitelist | None = None,
        known_pii_values: Sequence[str | PrivateData] | None = None,
        cover_regions: list[CoverRegion] | None = None,
        timeout_seconds: float = 300.0,
    ) -> RedactionPreviewResponse:
        """Preview the shielded copy asynchronously."""
        messages = to_contract_messages(prompt)
        wire_prompt = (
            prompt
            if isinstance(prompt, str)
            else (messages if isinstance(prompt, list) else messages[0])
        )
        extra_attachments = [coerce_attachment_input(a) for a in attachments or []]
        request = RedactionPreviewRequest.model_validate(
            {
                'prompt': wire_prompt,
                'attachments': extra_attachments,
                'resources': resources or [],
                'cover_regions': cover_regions,
                'pii_whitelist': pii_whitelist.model_dump(mode='json') if pii_whitelist else None,
                'known_pii_values': [
                    {**value.model_dump(mode='json', exclude_none=True), 'value': str(value.value)}
                    if isinstance(value, PrivateData)
                    else value
                    for value in known_pii_values
                ]
                if known_pii_values
                else None,
            }
        )
        return RedactionPreviewResponse.model_validate(
            await self._http(timeout_seconds=timeout_seconds).post(
                '/v1/redaction-previews',
                request.model_dump(mode='json', exclude_none=True),
            ),
            context={'messages': messages, 'attachments': extra_attachments},
        )

    def __init__(  # noqa: PLR0913 - v1-compatible constructor accepts public options.
        self,
        api_key: str | None = None,
        *,
        env_file: str | Path | None = None,
        api_key_file: str | Path | None = None,
        client_timezone: str | None = None,
        auto_detect_timezone: bool = True,
        base_url: str | None = None,
        config: ClientConfig | None = None,
        transport: httpx.AsyncBaseTransport | None = None,
        vault_transport: httpx.AsyncBaseTransport | None = None,
        timeout_seconds: float | None = None,
        timeout: float | None = None,
        thread_id: str | None = None,
        tool_execution_timeout: float | None = None,
        dependency_wait_timeout: float | None = None,
        total_execution_timeout: float | None = None,
        default_user_id: str | None = None,
        private_data_store: PrivateDataStore | AutoVault | None = AUTO_VAULT,
        local_vault: LocalVaultConfig | None = None,
        restore_private_values: bool = True,
        # Undocumented, underscore-prefixed test seam: lets internal tests give
        # the SDK's own internal request groupings distinct mock transports.
        # Never read from a public env var and never part of the documented
        # public surface -- every real request still targets one `base_url`.
        _internal_transport_overrides: tuple[
            httpx.AsyncBaseTransport | None, httpx.AsyncBaseTransport | None
        ] = (None, None),
    ) -> None:
        """Create a client from explicit config or SDK env-backed sources.

        ``timeout`` is the v1 spelling of ``timeout_seconds``. When both spellings
        are supplied they must agree. The remaining v1 execution settings are kept
        in :attr:`config`; only ``thread_id`` and the HTTP timeout are live against
        the current v2 wire contract.

        Set ``restore_private_values=False`` to receive placeholder-only final
        text and structured results. The API still resolves SDK tool-call
        arguments needed for local execution. The default keeps restored output.

        ``default_user_id`` (env ``MAIVN_DEFAULT_USER_ID``) is the customer id for runs
        that do not set ``RunOptions.user_id``. Use it only on a trusted backend that
        acts for one customer; an explicit per-call ``user_id`` always wins. It does
        not apply to ``.for_user()`` management facades, which stay explicit.
        """
        if timeout is not None and timeout_seconds is not None and timeout != timeout_seconds:
            message = 'timeout and timeout_seconds must match when both are provided'
            raise ValueError(message)
        resolved_timeout = timeout_seconds if timeout_seconds is not None else timeout
        self._client_timezone = client_timezone
        if self._client_timezone is None and auto_detect_timezone:
            self._client_timezone = self._detect_system_timezone()
        if config is None:
            self.config = ClientConfig.from_sources(
                api_key=api_key,
                env_file=env_file,
                api_key_file=api_key_file,
                base_url=base_url,
                timeout_seconds=resolved_timeout,
                thread_id=thread_id,
                tool_execution_timeout=tool_execution_timeout,
                dependency_wait_timeout=dependency_wait_timeout,
                total_execution_timeout=total_execution_timeout,
                default_user_id=default_user_id,
            )
        else:
            updates = {
                name: value
                for name, value in (
                    ('timeout_seconds', resolved_timeout),
                    ('thread_id', thread_id),
                    ('tool_execution_timeout', tool_execution_timeout),
                    ('dependency_wait_timeout', dependency_wait_timeout),
                    ('total_execution_timeout', total_execution_timeout),
                    ('default_user_id', default_user_id),
                )
                if value is not None
            }
            payload: dict[str, object] = config.model_dump()
            payload.update(updates)
            self.config = ClientConfig.model_validate(payload)
        # A bare AsyncClient() with no transport builds its own default
        # transport -- and therefore a fresh SSLContext plus a fresh TLS
        # handshake -- on every construction. `_http()`/`control_http()`/
        # `event_http()` build a new `HttpJsonClient` per call, so leaving
        # this `None` by default paid that cost on every single request.
        # Building one default transport here, reused by every plane unless
        # the caller injects its own, pays it once per event loop instead. It
        # keeps one pool per loop (see `LoopLocalTransport`): a shared pool made
        # a mid-run interrupt answer wait for the run's own open stream.
        default_transport = transport if transport is not None else LoopLocalTransport()
        self._transport = default_transport
        _control_transport, _event_transport = _internal_transport_overrides
        self._control_transport = (
            _control_transport if _control_transport is not None else default_transport
        )
        self._event_transport = (
            _event_transport if _event_transport is not None else default_transport
        )
        # `_http()` is not cached like `control_http()`/`event_http()` below:
        # some callers (e.g. the invoke stream's tool-result poster) enter its
        # result as `async with tool_http:` to get one connection pool for the
        # run's own lifetime, and `HttpJsonClient.__aenter__` refuses to be
        # entered twice at once -- sharing one cached instance across
        # concurrent runs on this `Client` would collide on that guard.
        self._control_http_client: HttpJsonClient | None = None
        self._event_http_client: HttpJsonClient | None = None
        if local_vault is not None and not isinstance(private_data_store, AutoVault):
            message = 'local_vault cannot be combined with an explicit private_data_store.'
            raise ValueError(message)
        self._automatic_vault = (
            AutomaticVault(self.control_http, local_vault)
            if isinstance(private_data_store, AutoVault)
            else None
        )
        self._private_data_store = (
            None if isinstance(private_data_store, AutoVault) else private_data_store
        )
        self._restore_private_values = restore_private_values
        self.jobs = HostedJobsClient(
            self._http, self._options, lambda: self.config.api_key, lambda: self.config.thread_id
        )
        self.memory = MemoryClient(self._http)
        self.artifacts = ArtifactsClient(self._http)
        self.resources = ResourcesClient(self._http)
        self.skills = SkillsClient(self._http)
        self.private_artifacts = PrivateArtifactsClient(
            self._http,
            vault_origin=self.config.base_url_text,
            vault_transport=vault_transport,
            timeout_seconds=self.config.timeout_seconds,
        )

    @property
    def client_timezone(self) -> str | None:
        """Return the v1 client timezone hint when provided."""
        return self._client_timezone

    @property
    def timeout(self) -> float:
        """Return the effective HTTP timeout in seconds."""
        return self.config.timeout_seconds

    @property
    def timeout_seconds(self) -> float:
        """Return the effective HTTP timeout using the v2 spelling."""
        return self.config.timeout_seconds

    @property
    def thread_id(self) -> str | None:
        """Return the default thread id used when an invoke omits one."""
        return self.config.thread_id

    @property
    def tool_execution_timeout(self) -> float | None:
        """Return the retained v1 per-tool timeout configuration."""
        return self.config.tool_execution_timeout

    @property
    def dependency_wait_timeout(self) -> float | None:
        """Return the retained v1 dependency wait timeout configuration."""
        return self.config.dependency_wait_timeout

    @property
    def total_execution_timeout(self) -> float | None:
        """Return the retained v1 total execution timeout configuration."""
        return self.config.total_execution_timeout

    def cancel(self, session_id: str) -> None:
        """Cancel one active server-owned invocation."""
        run_blocking(lambda: self.acancel(session_id))

    async def acancel(self, session_id: str) -> None:
        """Cancel one active server-owned invocation asynchronously."""
        path = SESSION_CANCEL_PATH.format(session_id=session_id)
        await self._http().post(path, {})

    async def _cancel_closed_invoke(self, session_id: str) -> None:
        """Attempt bounded cancellation without hiding the original stream failure."""
        try:
            await asyncio.wait_for(self.acancel(session_id), timeout=_STREAM_CANCEL_TIMEOUT_SECONDS)
        except MaivnHTTPError as exc:
            if exc.status_code == HTTPStatus.CONFLICT and exc.code == 'invoke_not_running':
                logger.debug('Stream close cancellation raced with invocation completion')
            else:
                logger.warning('Stream close cancellation failed: http_status=%d', exc.status_code)
        except (httpx.HTTPError, asyncio.TimeoutError, asyncio.CancelledError, ValueError) as exc:
            logger.warning('Stream close cancellation failed: failure_type=%s', type(exc).__name__)

    def thread(
        self,
        thread_id: str | None = None,
        *,
        options: RunOptions | None = None,
    ) -> BoundThread:
        """Return a fixed-identity handle for repeated operations on one thread."""
        return BoundThread(
            self,
            thread_id,
            options=options,
            session_events=lambda session_id, replay_options: self.stream_session(
                session_id, options=replay_options
            ),
        )

    def forget_private_data(self, thread_id: str) -> None:
        """Delete this thread's caller-declared local values; remote custody is unchanged."""
        run_blocking(lambda: self.aforget_private_data(thread_id))

    async def aforget_private_data(self, thread_id: str) -> None:
        """Delete remembered local values without creating a new vault."""
        if not thread_id.strip():
            message = 'thread_id must be a non-empty string.'
            raise ValueError(message)
        store = self._private_data_store
        if self._automatic_vault is not None:
            await self._automatic_vault.forget(thread_id)
            return
        if store is not None:
            await store.forget(thread_id)

    async def _thread_private_data(
        self,
        thread_id: str | None,
        supplied: Mapping[object, object] | None,
    ) -> Mapping[object, object] | None:
        """Merge the thread's persisted private_data under the caller's map.

        Without a configured store -- or without a thread to scope it to --
        the caller's map passes through untouched, which is exactly the
        pre-store behavior. With one, the pairs supplied this turn are
        remembered for the thread and every previously remembered pair is
        resolved into the call, the caller's explicit values winning.

        Only what the CALLER declared is ever remembered: ``persistable_pairs``
        reads the map passed to this call, never a map the server sent back. So
        the keys the shield allocates mid-run (``pii_email_1``) stay in server
        custody, which is the division the store's module docstring sets out.
        Once remembered, a thread's pairs ride along on every later call on that
        thread including ones supplying nothing; ``store.forget(thread_id)``
        ends it.
        """
        store = self._private_data_store
        if thread_id is not None and self._automatic_vault is not None:
            return await self._automatic_vault.merge(thread_id, supplied)
        if store is None or thread_id is None:
            return supplied
        pairs = persistable_pairs(supplied)
        if pairs:
            await store.remember(thread_id, pairs)
        stored = await store.resolve(thread_id, ())
        if not stored and not supplied:
            return supplied
        return merged_private_data(stored, supplied)

    @staticmethod
    def _detect_system_timezone() -> str | None:
        """Return a detected IANA timezone when an optional detector is installed."""
        try:
            module = import_module('tzlocal')
            detector = module.__dict__.get('get_localzone_name')
            if not callable(detector):
                return None
            value = cast('Callable[[], object]', detector)()
        except Exception:  # noqa: BLE001 - optional detector can fail on minimal systems.
            return None
        if isinstance(value, str) and value.strip():
            return value
        return None

    def invoke(  # noqa: PLR0913 - the invoke surface mirrors the wire contract.
        self,
        messages: SdkMessagesInput,
        *,
        options: RunOptions | None = None,
        tools: Sequence[ToolMetadata] = (),
        private_data: Mapping[object, object] | None = None,
        force_final_tool: bool = False,
        swarm_members: Sequence[object] = (),
    ) -> InvokeResponse:
        """Invoke the agent and wait for the final result."""
        return run_blocking(
            lambda: self.ainvoke(
                messages,
                options=options,
                tools=tools,
                private_data=private_data,
                force_final_tool=force_final_tool,
                swarm_members=swarm_members,
            )
        )

    async def ainvoke(  # noqa: PLR0913 - the invoke surface mirrors the wire contract.
        self,
        messages: SdkMessagesInput,
        *,
        options: RunOptions | None = None,
        tools: Sequence[ToolMetadata] = (),
        private_data: Mapping[object, object] | None = None,
        force_final_tool: bool = False,
        swarm_members: Sequence[object] = (),
    ) -> InvokeResponse:
        """Invoke the agent asynchronously and wait for the final result."""
        resolved_options = self._options(options)
        # A result-only invoke never renders live text, so let the server skip
        # persisting the delta stream unless the caller explicitly asked for it.
        if resolved_options.stream_deltas is None and not resolved_options.stream_response:
            resolved_options = resolved_options.model_copy(update={'stream_deltas': False})
        events = [
            event
            async for event in self.astream(
                messages,
                options=resolved_options,
                tools=tools,
                private_data=private_data,
                force_final_tool=force_final_tool,
                swarm_members=swarm_members,
            )
        ]
        response = response_from_stream_events(events)
        return _response_with_thread_id(response, resolved_options.thread_id)

    def stream(  # noqa: PLR0913 - the invoke surface mirrors the wire contract.
        self,
        messages: SdkMessagesInput,
        *,
        options: RunOptions | None = None,
        tools: Sequence[ToolMetadata] = (),
        private_data: Mapping[object, object] | None = None,
        force_final_tool: bool = False,
        swarm_members: Sequence[object] = (),
    ) -> Generator[StreamEvent, None, None]:
        """Stream events for a new invoke."""
        return stream_async_iterator(
            lambda: self.astream(
                messages,
                options=options,
                tools=tools,
                private_data=private_data,
                force_final_tool=force_final_tool,
                swarm_members=swarm_members,
            )
        )

    async def astream(  # noqa: PLR0913 - the invoke surface mirrors the wire contract.
        self,
        messages: SdkMessagesInput,
        *,
        options: RunOptions | None = None,
        tools: Sequence[ToolMetadata] = (),
        private_data: Mapping[object, object] | None = None,
        force_final_tool: bool = False,
        swarm_members: Sequence[object] = (),
        cancel_on_close: bool = False,
    ) -> AsyncGenerator[StreamEvent, None]:
        """Stream a new invoke; optionally cancel its root when closed before a terminal event.

        By default closing only disconnects SSE, retaining the invocation for replay.
        ``cancel_on_close`` is local policy and is never sent in the invoke payload.
        An interrupt awaiting input remains nonterminal and is cancelled on opt-in close.
        """
        resolved_options = self._options(options)
        messages = to_contract_messages(messages)
        validate_approved_messages(
            messages, preview_id=resolved_options.redaction_preview_id, private_data=private_data
        )
        if resolved_options.redaction_preview_id is None:
            private_data = await self._thread_private_data(
                resolved_options.thread_id,
                private_data,
            )
        messages, inspection_context = capture_invoke_inspection(
            messages,
            private_data=private_data,
            thread_id=resolved_options.thread_id,
            project_id=resolved_options.project_id,
            complete_history=(
                self.config.thread_id is None and (options is None or options.thread_id is None)
            ),
        )
        accepted = await self._start_invoke(
            messages,
            options=resolved_options,
            tools=tools,
            private_data=private_data,
            force_final_tool=force_final_tool,
            swarm_members=swarm_members,
        )
        terminal = False
        events: AsyncIterator[StreamEvent] | None = None
        try:
            # Report acceptance before consuming SSE so cancelled/failed runs remain traceable.
            _report_invoke_accepted(accepted.session_id)
            # Delegate agents' and swarm members' tools execute here too: the server runs
            # them as nested sessions, but their tool calls broker back over this stream.
            all_tools = invocation_tools(tools, swarm_members)
            # Local resolution only. This map is built AFTER the invoke was sent, so the
            # caller's named known-PII values never change what the server was told about
            # `private_data` - they only let this process recognise its own values coming
            # back under the server's `user_<name>` key.
            resolution_data = _local_resolution_data(messages, private_data)
            tool_runtime = LocalToolRuntime(
                all_tools,
                private_data=resolution_data,
                canonical_interrupts=True,
                thread_id=resolved_options.thread_id,
                inspection_context=(
                    inspection_context.accepted(accepted.session_id)
                    if inspection_context is not None
                    else None
                ),
            )
            tool_http = self._http(timeout_seconds=resolved_options.timeout)
            # Network ingestion and local tool execution must not inherit latency from a
            # caller's reporter or UI rendering. An unbounded in-process queue preserves
            # event order while the producer keeps the server's dependency graph moving.
            events = _hydrated_client_events(
                _telemetry_client_events(
                    _resolve_canonical_tool_interrupts(
                        _stream_with_local_tools(
                            events=self._stream_session_unobserved(
                                accepted.session_id,
                                options=resolved_options,
                            ),
                            tool_runtime=tool_runtime,
                            tool_http=tool_http,
                            session_id=accepted.session_id,
                            private_publisher=self.private_artifacts,
                            image_refs=tuple(
                                ref
                                for message in to_contract_messages(messages)
                                for ref in (message.artifact_refs or ())
                            ),
                        ),
                        client=self,
                        tools=all_tools,
                        options=resolved_options,
                    ),
                ),
                private_data=resolution_data if self._restore_private_values else None,
                session_id=accepted.session_id,
            )
            async for event in events:
                if (
                    event.event_type in {'final', 'error'}
                    and not event.data.get('parent_session_id')
                    and event.data.get('session_id', accepted.session_id) == accepted.session_id
                ):
                    terminal = True
                yield event
        finally:
            try:
                if cancel_on_close and not terminal:
                    await self._cancel_closed_invoke(accepted.session_id)
            finally:
                if events is not None:
                    await close_async_iterator(events)

    async def stream_session(
        self,
        session_id: str,
        *,
        options: RunOptions | None = None,
    ) -> AsyncGenerator[StreamEvent, None]:
        """Stream events asynchronously for an existing invoke session."""
        events = _telemetry_client_events(
            self._stream_session_unobserved(session_id, options=options)
        )
        try:
            async for event in events:
                yield event
        finally:
            await close_async_iterator(events)

    async def _stream_session_unobserved(
        self,
        session_id: str,
        *,
        options: RunOptions | None = None,
    ) -> AsyncIterator[StreamEvent]:
        """Stream a session without publishing for composed public run paths."""
        path = SESSION_EVENTS_PATH.format(session_id=session_id)
        identity: dict[str, object] = {'session_id': session_id}
        events = self._stream_path(
            path,
            options=self._options(options),
            redacted_output=not self._restore_private_values,
        )
        try:
            async for event in events:
                if not event.data.get('parent_session_id'):
                    facts = diagnostic_facts(event.payload, event.data)
                    facts.pop('error_code', None)
                    identity.update(facts)
                    identity['session_id'] = session_id
                yield event
        except (Exception, asyncio.CancelledError) as exc:
            code = (
                'cancelled'
                if isinstance(exc, asyncio.CancelledError)
                else 'stream_transport_error'
                if isinstance(exc, (httpx.TransportError, OSError))
                else None
            )
            attach_error_diagnostics(exc, identity, {'error_code': code})
            raise
        finally:
            await close_async_iterator(events)

    def start_thread(  # noqa: PLR0913 - mirrors invocation runtime options.
        self,
        messages: SdkMessagesInput,
        *,
        options: RunOptions | None = None,
        tools: Sequence[ToolMetadata] = (),
        private_data: Mapping[object, object] | None = None,
        force_final_tool: bool = False,
        swarm_members: Sequence[object] = (),
    ) -> ThreadAccepted:
        """Start a thread session."""
        return run_blocking(
            lambda: self.astart_thread(
                messages,
                options=options,
                tools=tools,
                private_data=private_data,
                force_final_tool=force_final_tool,
                swarm_members=swarm_members,
            )
        )

    async def astart_thread(  # noqa: PLR0913 - mirrors invocation runtime options.
        self,
        messages: SdkMessagesInput,
        *,
        options: RunOptions | None = None,
        tools: Sequence[ToolMetadata] = (),
        private_data: Mapping[object, object] | None = None,
        force_final_tool: bool = False,
        swarm_members: Sequence[object] = (),
    ) -> ThreadAccepted:
        """Start a thread session asynchronously."""
        resolved_options = self._options(options)
        private_data = await self._thread_private_data(
            resolved_options.thread_id,
            private_data,
        )
        invocation_payload = _thread_invocation_payload(
            resolved_options,
            tools=tools,
            private_data=private_data,
            force_final_tool=force_final_tool,
            swarm_members=swarm_members,
        )
        payload: dict[str, object] = {
            'messages': message_payloads(messages),
            'run_config': run_config_payload(resolved_options),
            **optional_run_options(resolved_options),
            **invocation_payload,
        }
        return ThreadAccepted.model_validate(
            await self._http(timeout_seconds=resolved_options.timeout).post(THREADS_PATH, payload),
        )

    def post_thread_message(  # noqa: PLR0913 - mirrors invocation runtime options.
        self,
        thread_id: str,
        message: SdkMessagesInput,
        *,
        options: RunOptions | None = None,
        tools: Sequence[ToolMetadata] = (),
        private_data: Mapping[object, object] | None = None,
        force_final_tool: bool = False,
        swarm_members: Sequence[object] = (),
    ) -> ThreadAccepted:
        """Continue a thread with a new message."""
        return run_blocking(
            lambda: self.apost_thread_message(
                thread_id,
                message,
                options=options,
                tools=tools,
                private_data=private_data,
                force_final_tool=force_final_tool,
                swarm_members=swarm_members,
            ),
        )

    async def apost_thread_message(  # noqa: PLR0913 - mirrors invocation runtime options.
        self,
        thread_id: str,
        message: SdkMessagesInput,
        *,
        options: RunOptions | None = None,
        tools: Sequence[ToolMetadata] = (),
        private_data: Mapping[object, object] | None = None,
        force_final_tool: bool = False,
        swarm_members: Sequence[object] = (),
    ) -> ThreadAccepted:
        """Continue a thread asynchronously with a new message."""
        resolved_options = self._options(options).model_copy(update={'thread_id': thread_id})
        private_data = await self._thread_private_data(thread_id, private_data)
        invocation_payload = _thread_invocation_payload(
            resolved_options,
            tools=tools,
            private_data=private_data,
            force_final_tool=force_final_tool,
            swarm_members=swarm_members,
        )
        payload = {
            'message': _single_thread_message(message),
            'run_config': run_config_payload(resolved_options),
            **optional_run_options(resolved_options),
            **invocation_payload,
        }
        path = THREAD_MESSAGES_PATH.format(thread_id=thread_id)
        with sdk_operation(
            'turn',
            attributes={
                'maivn.thread.id': thread_id,
                'maivn.project.id': resolved_options.project_id,
            },
        ):
            return ThreadAccepted.model_validate(
                await self._http(timeout_seconds=resolved_options.timeout).post(path, payload),
            )

    def time_travel_thread(  # noqa: PLR0913 - mirrors invocation runtime options.
        self,
        thread_id: str,
        *,
        checkpoint_id: str,
        message: SdkMessagesInput,
        branch_from_message_id: str | None = None,
        options: RunOptions | None = None,
        tools: Sequence[ToolMetadata] = (),
        private_data: Mapping[object, object] | None = None,
        force_final_tool: bool = False,
        swarm_members: Sequence[object] = (),
    ) -> ThreadAccepted:
        """Create a new active path from a prior checkpoint in the same thread."""
        return run_blocking(
            lambda: self.atime_travel_thread(
                thread_id,
                checkpoint_id=checkpoint_id,
                message=message,
                branch_from_message_id=branch_from_message_id,
                options=options,
                tools=tools,
                private_data=private_data,
                force_final_tool=force_final_tool,
                swarm_members=swarm_members,
            ),
        )

    async def atime_travel_thread(  # noqa: PLR0913 - carries invocation runtime options.
        self,
        thread_id: str,
        *,
        checkpoint_id: str,
        message: SdkMessagesInput,
        branch_from_message_id: str | None = None,
        options: RunOptions | None = None,
        tools: Sequence[ToolMetadata] = (),
        private_data: Mapping[object, object] | None = None,
        force_final_tool: bool = False,
        swarm_members: Sequence[object] = (),
    ) -> ThreadAccepted:
        """Asynchronously create a same-thread path from a prior checkpoint."""
        resolved_options = self._options(options).model_copy(update={'thread_id': thread_id})
        private_data = await self._thread_private_data(thread_id, private_data)
        return await self._dispatch_time_travel_thread(
            thread_id,
            checkpoint_id=checkpoint_id,
            message=message,
            branch_from_message_id=branch_from_message_id,
            options=resolved_options,
            tools=tools,
            private_data=private_data,
            force_final_tool=force_final_tool,
            swarm_members=swarm_members,
        )

    async def _dispatch_time_travel_thread(  # noqa: PLR0913 - shared wire dispatch.
        self,
        thread_id: str,
        *,
        checkpoint_id: str,
        message: SdkMessagesInput,
        branch_from_message_id: str | None,
        options: RunOptions,
        tools: Sequence[ToolMetadata],
        private_data: Mapping[object, object] | None,
        force_final_tool: bool,
        swarm_members: Sequence[object],
    ) -> ThreadAccepted:
        """Dispatch one time-travel request with already-resolved local custody."""
        invocation_payload = _thread_invocation_payload(
            options,
            tools=tools,
            private_data=private_data,
            force_final_tool=force_final_tool,
            swarm_members=swarm_members,
        )
        payload: dict[str, object] = {
            'checkpoint_id': checkpoint_id,
            'message': _single_thread_message(message),
            'run_config': run_config_payload(options),
            **optional_run_options(options),
            **invocation_payload,
        }
        if branch_from_message_id is not None:
            payload['branch_from_message_id'] = branch_from_message_id
        path = THREAD_TIME_TRAVEL_PATH.format(thread_id=thread_id)
        return ThreadAccepted.model_validate(
            await self._http(timeout_seconds=options.timeout).post(path, payload),
        )

    def time_travel_thread_stream(  # noqa: PLR0913 - mirrors the invoke stream contract.
        self,
        thread_id: str,
        *,
        checkpoint_id: str,
        message: SdkMessagesInput,
        branch_from_message_id: str | None = None,
        options: RunOptions | None = None,
        tools: Sequence[ToolMetadata] = (),
        private_data: Mapping[object, object] | None = None,
        force_final_tool: bool = False,
        swarm_members: Sequence[object] = (),
    ) -> Generator[StreamEvent, None, None]:
        """Rewind a thread and stream the replacement path to completion."""
        return stream_async_iterator(
            lambda: self.atime_travel_thread_stream(
                thread_id,
                checkpoint_id=checkpoint_id,
                message=message,
                branch_from_message_id=branch_from_message_id,
                options=options,
                tools=tools,
                private_data=private_data,
                force_final_tool=force_final_tool,
                swarm_members=swarm_members,
            ),
        )

    async def atime_travel_thread_stream(  # noqa: PLR0913 - mirrors the invoke stream contract.
        self,
        thread_id: str,
        *,
        checkpoint_id: str,
        message: SdkMessagesInput,
        branch_from_message_id: str | None = None,
        options: RunOptions | None = None,
        tools: Sequence[ToolMetadata] = (),
        private_data: Mapping[object, object] | None = None,
        force_final_tool: bool = False,
        swarm_members: Sequence[object] = (),
    ) -> AsyncGenerator[StreamEvent, None]:
        """Asynchronously rewind a thread and stream the accepted session."""
        resolved_options = self._options(options).model_copy(update={'thread_id': thread_id})
        private_data = await self._thread_private_data(thread_id, private_data)
        message, inspection_context = capture_invoke_inspection(
            message,
            private_data=private_data,
            thread_id=thread_id,
            project_id=resolved_options.project_id,
        )
        accepted = await self._dispatch_time_travel_thread(
            thread_id,
            checkpoint_id=checkpoint_id,
            message=message,
            branch_from_message_id=branch_from_message_id,
            options=resolved_options,
            tools=tools,
            private_data=private_data,
            force_final_tool=force_final_tool,
            swarm_members=swarm_members,
        )
        _report_invoke_accepted(accepted.session_id)
        all_tools = invocation_tools(tools, swarm_members)
        resolution_data = _local_resolution_data(message, private_data)
        tool_runtime = LocalToolRuntime(
            all_tools,
            private_data=resolution_data,
            canonical_interrupts=True,
            thread_id=resolved_options.thread_id,
            inspection_context=(
                inspection_context.accepted(accepted.session_id)
                if inspection_context is not None
                else None
            ),
        )
        tool_http = self._http(timeout_seconds=resolved_options.timeout)
        events = _hydrated_client_events(
            _telemetry_client_events(
                _resolve_canonical_tool_interrupts(
                    _stream_with_local_tools(
                        events=self._stream_session_unobserved(
                            accepted.session_id,
                            options=resolved_options,
                        ),
                        tool_runtime=tool_runtime,
                        tool_http=tool_http,
                        session_id=accepted.session_id,
                        private_publisher=self.private_artifacts,
                        image_refs=tuple(
                            ref
                            for item in to_contract_messages(message)
                            for ref in (item.artifact_refs or ())
                        ),
                    ),
                    client=self,
                    tools=all_tools,
                    options=resolved_options,
                ),
            ),
            private_data=resolution_data if self._restore_private_values else None,
            session_id=accepted.session_id,
        )
        try:
            async for event in events:
                yield event
        finally:
            await close_async_iterator(events)

    def submit_approval(
        self,
        thread_id: str,
        interrupt_id: str,
        *,
        decision: ApprovalDecision,
    ) -> ThreadAccepted:
        """Submit a thread approval decision."""
        return run_blocking(
            lambda: self.asubmit_approval(
                thread_id,
                interrupt_id,
                decision=decision,
            ),
        )

    async def asubmit_approval(
        self,
        thread_id: str,
        interrupt_id: str,
        *,
        decision: ApprovalDecision,
    ) -> ThreadAccepted:
        """Submit a thread approval decision asynchronously."""
        payload = decision.model_dump(mode='json', exclude_none=True)
        path = THREAD_APPROVALS_PATH.format(thread_id=thread_id, interrupt_id=interrupt_id)
        return ThreadAccepted.model_validate(await self._http().post(path, payload))

    def submit_interrupt_response(
        self,
        thread_id: str,
        interrupt_id: str,
        *,
        answer: object,
        responded_by: str,
        surface: str = 'sdk',
    ) -> ThreadAccepted:
        """Submit a typed non-approval interrupt answer."""
        return run_blocking(
            lambda: self.asubmit_interrupt_response(
                thread_id,
                interrupt_id,
                answer=answer,
                responded_by=responded_by,
                surface=surface,
            ),
        )

    async def asubmit_interrupt_response(
        self,
        thread_id: str,
        interrupt_id: str,
        *,
        answer: object,
        responded_by: str,
        surface: str = 'sdk',
    ) -> ThreadAccepted:
        """Asynchronously submit a typed non-approval interrupt answer."""
        typed_answer = _interrupt_response(answer)
        payload = {
            'answer': typed_answer.model_dump(mode='json'),
            'responded_by': responded_by,
            'surface': surface,
        }
        path = THREAD_INTERRUPT_RESPONSES_PATH.format(
            thread_id=thread_id,
            interrupt_id=interrupt_id,
        )
        return ThreadAccepted.model_validate(await self._http().post(path, payload))

    def session_usage(self, session_id: str) -> ProviderUsage:
        """Fetch provider-reported usage for a session and the work it started."""
        return run_blocking(lambda: self.asession_usage(session_id))

    async def asession_usage(self, session_id: str) -> ProviderUsage:
        """Fetch provider-reported usage for a session asynchronously.

        The session id it answers for is the one asked for, so the response echo
        is dropped rather than carried twice.
        """
        path = SESSION_USAGE_PATH.format(session_id=session_id)
        payload = dict(await self._http().get(path))
        _ = payload.pop('session_id', None)
        return ProviderUsage.model_validate(payload)

    def get_thread(self, thread_id: str) -> ThreadState:
        """Fetch thread state and history."""
        return run_blocking(lambda: self.aget_thread(thread_id))

    async def aget_thread(self, thread_id: str) -> ThreadState:
        """Fetch thread state and history asynchronously."""
        path = THREAD_STATE_PATH.format(thread_id=thread_id)
        return ThreadState.from_payload(await self._http().get(path))

    def thread_events(
        self,
        thread_id: str,
        *,
        options: RunOptions | None = None,
    ) -> Generator[StreamEvent, None, None]:
        """Stream events for an existing thread."""
        return stream_async_iterator(lambda: self.athread_events(thread_id, options=options))

    async def athread_events(
        self,
        thread_id: str,
        *,
        options: RunOptions | None = None,
    ) -> AsyncGenerator[StreamEvent, None]:
        """Stream events asynchronously for an existing thread."""
        path = THREAD_EVENTS_PATH.format(thread_id=thread_id)
        events = _telemetry_client_events(self._stream_path(path, options=self._options(options)))
        try:
            async for event in events:
                yield event
        finally:
            await close_async_iterator(events)

    async def _start_invoke(  # noqa: PLR0913 - the invoke surface mirrors the wire contract.
        self,
        messages: SdkMessagesInput,
        *,
        options: RunOptions,
        tools: Sequence[ToolMetadata] = (),
        private_data: Mapping[object, object] | None = None,
        force_final_tool: bool = False,
        swarm_members: Sequence[object] = (),
    ) -> InvokeAccepted:
        messages = to_contract_messages(messages)
        validate_approved_messages(
            messages, preview_id=options.redaction_preview_id, private_data=private_data
        )
        invocation_payload = invocation_options(
            tools,
            private_data=private_data,
            force_final_tool=force_final_tool,
        )
        payload: dict[str, object] = {
            'messages': message_payloads(messages),
            'run_config': run_config_payload(options),
            **optional_run_options(options),
            **skill_selection_payload(options),
            **invocation_payload,
        }
        _log_invocation_contract(invocation_payload)
        # Reject conflicting broker targets before any invoke is dispatched.
        invocation_tools(tools, swarm_members)
        _merge_swarm_roster(payload, swarm_members)
        with sdk_operation(
            'run.start',
            attributes={
                'maivn.project.id': options.project_id,
                'maivn.thread.id': options.thread_id,
            },
        ):
            return InvokeAccepted.model_validate(
                await self._http(timeout_seconds=options.timeout).post(INVOKE_PATH, payload),
            )

    async def _stream_path(
        self,
        path: str,
        *,
        options: RunOptions,
        redacted_output: bool = False,
    ) -> AsyncIterator[StreamEvent]:
        params: dict[str, int | str] = dict(resume_params(options) or {})
        if redacted_output:
            params['redacted_output'] = 'true'
        response = await self._http(timeout_seconds=options.timeout).stream(path, params=params)
        projector = StreamEventProjector()
        try:
            async for event in iter_sse_events(response.aiter_lines()):
                yield projector.project(event)
        finally:
            await close_stream_response(response)

    def _http(self, *, timeout_seconds: float | None = None) -> HttpJsonClient:
        """Return a fresh API client wrapper for one call or one run.

        Not cached (see the note on `self._control_http_client` in
        `__init__`): a caller may hold this wrapper open for a whole run via
        `async with`, so a new run must never be handed one another concurrent
        run already has open. It still builds a fresh `httpx.AsyncClient` per
        request underneath (safe across event loops and sync/async mixing --
        see `test_transport_leases`), but every wrapper shares this `Client`'s
        one lease-managed transport (see `self._transport` above) instead of
        each request paying to build and TLS-handshake its own.
        """
        return HttpJsonClient(
            base_url=self.config.base_url_text,
            api_key=self.config.api_key,
            timeout_seconds=(
                self.config.timeout_seconds if timeout_seconds is None else timeout_seconds
            ),
            transport=self._transport,
        )

    def control_http(self) -> HttpJsonClient:
        """Return a long-lived, authenticated client for the single public entry point."""
        if self._control_http_client is None:
            self._control_http_client = HttpJsonClient(
                base_url=self.config.base_url_text,
                api_key=self.config.api_key,
                timeout_seconds=self.config.timeout_seconds,
                transport=self._control_transport,
            )
        return self._control_http_client

    def event_http(self) -> HttpJsonClient:
        """Return a long-lived, authenticated client for the single public entry point."""
        if self._event_http_client is None:
            self._event_http_client = HttpJsonClient(
                base_url=self.config.base_url_text,
                api_key=self.config.api_key,
                timeout_seconds=self.config.timeout_seconds,
                transport=self._event_transport,
            )
        return self._event_http_client

    def _options(self, options: RunOptions | None) -> RunOptions:
        """Resolve client defaults without overriding explicit per-call options."""
        resolved = options if options is not None else RunOptions()
        if resolved.thread_id is None and self.config.thread_id is not None:
            thread_update: dict[str, object] = {'thread_id': self.config.thread_id}
        else:
            thread_update = {}
        defaults = {
            'timeout': self.config.timeout_seconds,
            'tool_execution_timeout': self.config.tool_execution_timeout,
            'dependency_wait_timeout': self.config.dependency_wait_timeout,
            'total_execution_timeout': self.config.total_execution_timeout,
            'client_timezone': self._client_timezone,
            'sdk_deployment_timezone': os.getenv('MAIVN_DEPLOYMENT_TIMEZONE'),
        }
        updates = {
            **thread_update,
            **{
                name: value
                for name, value in defaults.items()
                if getattr(resolved, name) is None and value is not None
            },
        }
        if updates:
            resolved = resolved.model_copy(update=updates)
        if self.config.default_user_id is not None and 'user_id' not in resolved.model_fields_set:
            resolved = resolved.model_copy(update={'user_id': self.config.default_user_id})
        if resolved.thread_id is not None:
            return resolved
        return resolved.model_copy(update={'thread_id': f'thr-{uuid4().hex}'})


def _log_invocation_contract(invocation_payload: Mapping[str, object]) -> None:
    """Log the executable contract without prompts, arguments, or private values."""
    raw_tools = invocation_payload.get('tools')
    tools = cast('list[object]', raw_tools) if isinstance(raw_tools, list) else []
    dependency_summary: dict[str, list[str]] = {}
    for raw_tool in tools:
        if not isinstance(raw_tool, dict):
            continue
        tool = cast('dict[str, object]', raw_tool)
        name = tool.get('name')
        raw_edges = tool.get('tool_dependencies')
        if not isinstance(name, str) or not isinstance(raw_edges, list):
            continue
        dependency_summary[name] = [
            dependency_name
            for raw_edge in cast('list[object]', raw_edges)
            if isinstance(raw_edge, dict)
            and isinstance(
                dependency_name := cast('dict[str, object]', raw_edge).get('tool_name'),
                str,
            )
        ]
    logger.info(
        'Prepared invocation contract: tool_count=%d final_tool_ids=%s '
        'force_final_tool=%s tool_dependencies=%s',
        len(tools),
        invocation_payload.get('final_tool_ids', []),
        invocation_payload.get('force_final_tool') is True,
        dependency_summary,
    )


def _thread_invocation_payload(
    options: RunOptions,
    *,
    tools: Sequence[ToolMetadata],
    private_data: Mapping[object, object] | None,
    force_final_tool: bool,
    swarm_members: Sequence[object],
) -> JsonObject:
    """Build executable fields shared by every explicit thread dispatch."""
    invocation_payload = invocation_options(
        tools,
        private_data=private_data,
        force_final_tool=force_final_tool,
    )
    payload = {
        **skill_selection_payload(options),
        **invocation_payload,
    }
    _log_invocation_contract(invocation_payload)
    # Reject conflicting broker targets before any invoke is dispatched.
    invocation_tools(tools, swarm_members)
    _merge_swarm_roster(payload, swarm_members)
    return payload


def _merge_swarm_roster(payload: dict[str, object], swarm_members: Sequence[object]) -> None:
    """Fold the swarm roster into the invoke body, merging with any delegate agents.

    Tool-level delegation and a swarm roster can name the same agent; the roster's spec
    wins because it carries the fuller declaration (final-output flag, peer edges).
    """
    roster = swarm_roster_payload(swarm_members)
    if not roster:
        return
    existing = payload.get('agents')
    existing_specs = cast('list[object]', existing) if isinstance(existing, list) else []
    roster_specs = cast('list[object]', roster['agents'])
    merged: dict[str, object] = {}
    for spec in [*existing_specs, *roster_specs]:
        if not isinstance(spec, dict):
            continue
        name = cast('dict[str, object]', spec).get('name')
        if isinstance(name, str):
            merged[name] = spec
    payload['agents'] = list(merged.values())
    payload['swarm_roster'] = True
    roster_log: list[dict[str, object]] = []
    for name, spec in merged.items():
        if not isinstance(spec, dict):
            continue
        typed_spec = cast('dict[str, object]', spec)
        roster_log.append(
            {
                'name': name,
                'use_as_final_output': typed_spec.get('use_as_final_output') is True,
            },
        )
    logger.debug(
        'Prepared swarm roster for transport: %s',
        roster_log,
    )


def _response_with_thread_id(response: InvokeResponse, thread_id: str | None) -> InvokeResponse:
    if response.thread_id is not None or thread_id is None:
        return response
    return response.model_copy(update={'thread_id': thread_id})


__all__ = [
    'Client',
    # Re-exported for callers that import these helpers from client.py.
    '_hydrated_client_event',
    '_hydrated_client_events',
    '_local_resolution_data',
    '_stream_with_local_tools',
    '_telemetry_client_events',
]
