"""Announcing a locally running agent to the platform, and staying announced.

Owner ruling 2026-09-04: we do not host agents. Triggers are monitored by the
platform, but the agent itself runs in the developer's own environment, which
has to be running for a trigger to reach anything. "Publishing a version" is
therefore *this* - a running process announcing itself - and never an upload of
a definition.

What crosses the wire is deliberately thin: an identity, an algorithm-tagged
fingerprint of the whole runnable definition, a transport, and a display subset
small enough to read in Studio. The instructions, the model settings and the
tool schemas stay on the developer's machine. The platform can tell *that* a
running agent has drifted from what registered, and never what it says.

The fingerprint comes from :func:`maivn_contracts.agents.fingerprint.definition_digest`
rather than a second hash written here: two implementations that disagree would
report drift that is not there, which is worse than reporting none at all.
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from functools import lru_cache
from http import HTTPStatus
from typing import TYPE_CHECKING, Final, Literal, Protocol, TypeAlias
from urllib.parse import quote
from uuid import UUID, uuid4

import httpx
from maivn_contracts.agents import ServedVersion, ServingManifest, ServingTransport
from maivn_contracts.agents.fingerprint import definition_digest

from maivn.__version__ import __version__
from maivn._internal.claiming import ClaimListener, WorkClaimer, WorkLoop, WorkRunner
from maivn._internal.compat.logging import get_logger
from maivn._internal.config import PROJECT_ID_ENV_VAR
from maivn._internal.errors import ConfigurationError, MaivnHTTPError, MaivnSDKError
from maivn._internal.plan_limits import is_plan_limit_error
from maivn._internal.tracing import sdk_operation
from maivn._internal.transport.http import JsonObject

if TYPE_CHECKING:
    from types import TracebackType

    from typing_extensions import Self

    from maivn._internal.client import Client
    from maivn._internal.config import ClientConfig
    from maivn._internal.models import ToolMetadata
    from maivn.messages import SystemMessage

# How often a serving process tells the platform it is still alive.
#
# The platform forgets a process it has not heard from within
# `DEFAULT_STALE_AFTER` - 90 seconds, in the server's own
# `agents/serving.py`. At 25 seconds the THIRD consecutive attempt still lands
# at t+75s, inside that window: two missed beats in a row - one Wi-Fi blip plus
# one slow reconnect - never flicker a healthy agent to `stale` in the UI, and
# roughly 15 seconds of the window is still left over for clock skew and
# request latency. 30 seconds would spend exactly that slack (three beats land
# on the boundary), and anything much smaller just adds chatter for no extra
# safety.
HEARTBEAT_INTERVAL_SECONDS: Final = 25.0

# Retry budget for one serving call. The heartbeat interval is the outer
# backoff, so a beat that exhausts these simply waits and tries again - a
# developer's agent must not stop serving because their Wi-Fi blipped.
INITIAL_RETRY_BACKOFF_SECONDS: Final = 0.5
MAX_RETRY_BACKOFF_SECONDS: Final = 8.0
MAX_RETRY_ATTEMPTS: Final = 5
# A clean stop gets a much smaller budget: the process is leaving, and making a
# developer watch fifteen seconds of retries after Ctrl-C is its own bug.
STOP_RETRY_ATTEMPTS: Final = 2

SERVING_TRANSPORTS: Final[tuple[ServingTransport, ...]] = ('worker', 'webhook')

# Statuses below 500 that still mean "try again shortly" rather than "you are
# wrong". Everything at or above 500 is transient by definition.
_RETRYABLE_STATUSES: Final[frozenset[int]] = frozenset(
    {HTTPStatus.REQUEST_TIMEOUT, HTTPStatus.TOO_MANY_REQUESTS},
)
# The platform said no, and saying it again will not help.
_TERMINAL_STATUSES: Final[frozenset[int]] = frozenset(
    {HTTPStatus.UNAUTHORIZED, HTTPStatus.FORBIDDEN},
)

ServingEventKind: TypeAlias = Literal[
    'announced',
    're_announced',
    'heartbeat',
    'retrying',
    'unreachable',
    'stopped',
]


@dataclass(frozen=True, slots=True)
class ServingEvent:
    """One thing that happened while a local process served an agent version.

    Carries identity and liveness only. Never the API key, never an endpoint
    credential, never anything from the definition - a listener is usually a
    terminal printer, and what it is handed is what ends up on screen.
    """

    kind: ServingEventKind
    agent_id: str
    version: int
    instance_id: str
    process_id: str | None = None
    detail: str | None = None


ServingListener: TypeAlias = Callable[[ServingEvent], None]
_ControlPlaneCall: TypeAlias = Callable[[], Awaitable[JsonObject]]


class ServableScope(Protocol):
    """The authoring-scope surface a serving manifest is built from."""

    name: str
    description: str | None
    system_prompt: str | SystemMessage | None
    model: str

    def compile_tools(self) -> list[ToolMetadata]:
        """Return the scope's compiled local tool metadata."""
        ...


@lru_cache(maxsize=1)
def process_instance_id() -> UUID:
    """Return the id that identifies THIS process to the platform.

    Generated once and reused by every announcement and re-announcement the
    process makes. Two machines serving the same version have to stay two rows
    on the platform: an earlier design let the platform invent this, and it
    silently collapsed distinct machines into one. It is never regenerated
    mid-process - a restart is what earns a new id.
    """
    return uuid4()


def build_serving_manifest(
    scope: ServableScope,
    *,
    agent_id: str,
    version: int,
    transport: ServingTransport = 'worker',
    endpoint_url: str | None = None,
) -> ServingManifest:
    """Build what one running process announces about the version it serves.

    The display fields are the deliberately small subset the contract allows: a
    name, a description, and tool *names*. The instructions, the model settings
    and the tool schemas reach only :func:`definition_digest`, which hashes them
    and discards them.
    """
    _require_transport_pairing(transport, endpoint_url)
    tool_names = tuple(tool.name for tool in scope.compile_tools())
    digest = definition_digest(
        instructions=instruction_text(scope.system_prompt),
        # Mirrors `ModelSettings.response`, the one model setting an
        # SDK-authored scope carries today. Adding a key here silently changes
        # every fingerprint at once, so it belongs with a FINGERPRINT_ALGORITHM
        # bump rather than a quiet edit - otherwise an algorithm change reads as
        # every agent drifting on the same day.
        model={'response': scope.model},
        tool_names=tool_names,
    )
    return ServingManifest(
        agent_id=agent_id,
        version=version,
        name=scope.name,
        description=scope.description or None,
        transport=transport,
        definition_digest=digest,
        tool_names=tool_names,
        endpoint_url=endpoint_url,
        sdk_version=__version__,
    )


def instruction_text(system_prompt: str | SystemMessage | None) -> str:
    """Reduce a scope's system prompt to the exact text the fingerprint covers.

    A message object is reduced to its content and nothing else: its generated
    ``message_id`` and ``ts`` change on every construction, and folding those
    into the digest would report drift on every restart.
    """
    if system_prompt is None:
        return ''
    if isinstance(system_prompt, str):
        return system_prompt
    content = system_prompt.content
    if isinstance(content, str):
        return content
    return json.dumps(
        [block.model_dump(mode='json') for block in content],
        sort_keys=True,
        separators=(',', ':'),
    )


def resolve_project_id(explicit: str | None, config: ClientConfig) -> str:
    """Resolve which project a process announces into, or say what is missing.

    Never guessed and never silently skipped: announcing into the wrong project
    publishes an agent where nobody is looking for it, and doing nothing at all
    leaves a developer watching a process that will never be reached.
    """
    for candidate in (explicit, config.project_id):
        if candidate is not None and candidate.strip():
            return candidate.strip()
    message = (
        'serving requires a project id: pass project_id= to serve(), '
        f'or set the {PROJECT_ID_ENV_VAR} environment variable'
    )
    raise ConfigurationError(message)


class ServingSession:
    """One local process announced to the platform for one agent version.

    Announce, heartbeat, stop. Usable as an async context manager so that the
    stop is owed to a ``finally`` rather than to the developer remembering:
    Ctrl-C then becomes a clean stop instead of a row the platform has to age
    out.
    """

    def __init__(  # noqa: PLR0913 - one keyword per independent serving fact; a config object would only rename them.
        self,
        client: Client,
        *,
        project_id: str,
        manifest: ServingManifest,
        heartbeat_interval_seconds: float = HEARTBEAT_INTERVAL_SECONDS,
        instance_id: UUID | None = None,
        listener: ServingListener | None = None,
        runner: WorkRunner | None = None,
        claim_listener: ClaimListener | None = None,
    ) -> None:
        """Bind one manifest and one project to the client that announces it."""
        self._client = client
        self._project_id = project_id
        self._manifest = manifest
        self._heartbeat_interval_seconds = heartbeat_interval_seconds
        self._instance_id = instance_id if instance_id is not None else process_instance_id()
        self._listener = listener
        self._runner = runner
        self._claim_listener = claim_listener
        self._process_id: str | None = None
        self._served: ServedVersion | None = None
        self._work: WorkLoop | None = None

    @property
    def instance_id(self) -> str:
        """Return the stable per-process identity carried by every announcement."""
        return str(self._instance_id)

    @property
    def process_id(self) -> str | None:
        """Return the platform's id for this process, or None before announcing."""
        return self._process_id

    @property
    def served(self) -> ServedVersion | None:
        """Return what the platform said about this version at the last announcement."""
        return self._served

    @property
    def manifest(self) -> ServingManifest:
        """Return the manifest this process announces."""
        return self._manifest

    @property
    def project_id(self) -> str:
        """Return the project this process announces into."""
        return self._project_id

    async def announce(self) -> ServedVersion:
        """Tell the platform this process is serving its version."""
        with sdk_operation(
            'serve.announce',
            attributes={
                'maivn.project.id': self._project_id,
                'maivn.serve.cycle': 'announce',
            },
        ):
            return await self._announce('announced')

    async def heartbeat(self) -> bool:
        """Tell the platform this process is still alive.

        Returns whether the beat landed. A transient failure returns False
        rather than raising, so one bad network moment costs a beat and not the
        serving loop. A ``401``/``403`` is terminal and propagates: retrying a
        refusal only delays telling the developer why.
        """
        try:
            with sdk_operation(
                'serve.heartbeat',
                attributes={
                    'maivn.project.id': self._project_id,
                    'maivn.serve.cycle': 'heartbeat',
                },
            ):
                return await self._heartbeat_or_re_announce()
        except MaivnHTTPError as exc:
            if _is_terminal(exc):
                raise
            self._emit('unreachable', detail=_detail(exc))
            return False
        except httpx.HTTPError as exc:
            self._emit('unreachable', detail=_detail(exc))
            return False

    async def stop(self) -> None:
        """Tell the platform this process is going away. Idempotent.

        A failed stop never raises: this runs on the way out, usually from a
        ``finally``, and an exception here would replace whatever actually ended
        the run. The DELETE goes out before any backoff sleep, so it still
        reaches the platform when the reason for stopping was Ctrl-C.
        """
        process_id = self._process_id
        if process_id is None:
            return
        self._process_id = None
        try:
            _ = await self._with_retry(
                lambda: self._delete(process_id),
                attempts=STOP_RETRY_ATTEMPTS,
            )
        except MaivnHTTPError as exc:
            # A 404 is the outcome we wanted: the platform has already forgotten
            # this process.
            if exc.status_code != HTTPStatus.NOT_FOUND:
                self._emit('stopped', detail=_detail(exc))
                return
        except httpx.HTTPError as exc:
            self._emit('stopped', detail=_detail(exc))
            return
        self._emit('stopped')

    async def run(self) -> None:
        """Announce, then stay announced and take work until cancelled.

        Two things run side by side here, because a serving process owes the
        platform two different things. The heartbeat keeps this process
        *reachable*: stop it and the platform ages the process out and stops
        routing to it. The claim loop is what actually *receives* the work -
        the worker transport is pull by default, so nothing arrives unless
        this process asks. Announcing alone leaves an agent visible and idle.

        Neither loop is allowed to take the other down quietly. A blipped
        network costs one of them a beat and nothing more; only a refusal the
        platform will keep repeating - a revoked key, a process it no longer
        recognises - ends the run, and it ends both loops and propagates.

        On the way out, the stop is owed to a ``finally``, so Ctrl-C sends the
        DELETE and hands back any claim this process had taken but not
        finished.
        """
        async with self:
            await self._serve_until_cancelled()

    async def _serve_until_cancelled(self) -> None:
        """Run the heartbeat and, when there is one, the claim loop together."""
        tasks: list[asyncio.Future[None]] = [
            asyncio.ensure_future(self._heartbeat_forever()),
        ]
        work = self._work
        if work is not None:
            tasks.append(asyncio.ensure_future(work.run()))
        try:
            done, _pending = await asyncio.wait(tasks, return_when=asyncio.FIRST_EXCEPTION)
            for task in done:
                # Re-raises whichever loop stopped, so a terminal refusal
                # surfaces to the developer instead of leaving one loop
                # running and the process looking healthy.
                task.result()
        finally:
            for task in tasks:
                _ = task.cancel()
            _ = await asyncio.gather(*tasks, return_exceptions=True)

    async def _heartbeat_forever(self) -> None:
        while True:
            await asyncio.sleep(self._heartbeat_interval_seconds)
            _ = await self.heartbeat()

    async def __aenter__(self) -> Self:
        """Announce this process on the way in."""
        _ = await self.announce()
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        """Stop cleanly on the way out, however the block ended."""
        _ = (exc_type, exc, traceback)
        await self.stop()

    async def _heartbeat_or_re_announce(self) -> bool:
        process_id = self._process_id
        if process_id is None:
            message = 'serving heartbeat requires an announced process: call announce() first'
            raise MaivnSDKError(message)
        try:
            _ = await self._with_retry(lambda: self._patch(process_id))
        except MaivnHTTPError as exc:
            if exc.status_code != HTTPStatus.NOT_FOUND:
                raise
            # The platform no longer knows this process - it restarted, or swept
            # a row it had aged out. Re-announcing is the entire recovery;
            # giving up would leave a running agent invisible and its triggers
            # unroutable until somebody noticed by hand.
            _ = await self._announce('re_announced')
            return True
        self._emit('heartbeat')
        return True

    async def _announce(self, kind: ServingEventKind) -> ServedVersion:
        body = await self._with_retry(self._post)
        self._process_id = _required_string(body, 'process_id')
        self._served = _served_version(body)
        self._bind_work_loop()
        self._emit(kind)
        return self._served

    def _bind_work_loop(self) -> None:
        """Point the claim loop at the process id the platform just issued.

        Re-announcing gives this process a NEW id, and a claim made with the
        old one is refused. Rebinding here - rather than once at construction -
        is what lets a process that the platform forgot recover by
        re-announcing and carry on taking work.
        """
        process_id = self._process_id
        runner = self._runner
        if process_id is None or runner is None:
            return
        claimer = WorkClaimer(self._client, process_id=process_id)
        if self._work is None:
            self._work = WorkLoop(claimer, runner, listener=self._claim_listener)
            return
        self._work.rebind(claimer)

    async def _post(self) -> JsonObject:
        return await self._client.control_http().post(
            self._serving_path,
            {
                'instance_id': str(self._instance_id),
                'manifest': self._manifest.model_dump(mode='json', exclude_none=True),
            },
        )

    async def _patch(self, process_id: str) -> JsonObject:
        # No body: the path already names the process, and the pinned contract
        # gives the heartbeat no fields. Inventing one would be refused by a
        # server that forbids extras.
        return await self._client.control_http().patch(self._process_path(process_id), {})

    async def _delete(self, process_id: str) -> JsonObject:
        return await self._client.control_http().delete(self._process_path(process_id))

    async def _with_retry(
        self,
        call: _ControlPlaneCall,
        *,
        attempts: int = MAX_RETRY_ATTEMPTS,
    ) -> JsonObject:
        delay = INITIAL_RETRY_BACKOFF_SECONDS
        for attempt in range(1, attempts + 1):
            try:
                return await call()
            # PERF203: catching inside the loop is what a retry IS; the cost of
            # a handler frame is irrelevant next to the network call it guards.
            except (MaivnHTTPError, httpx.HTTPError) as exc:  # noqa: PERF203
                if attempt == attempts or not _is_transient(exc):
                    raise
                self._emit('retrying', detail=f'{_detail(exc)}; retrying in {delay:.1f}s')
                await asyncio.sleep(delay)
                delay = min(delay * 2, MAX_RETRY_BACKOFF_SECONDS)
        message = 'serving retry budget must be at least one attempt'
        raise MaivnSDKError(message)

    @property
    def _serving_path(self) -> str:
        return f'/v1/projects/{quote(self._project_id, safe="")}/agents/serving'

    def _process_path(self, process_id: str) -> str:
        return f'{self._serving_path}/{quote(process_id, safe="")}'

    def _emit(self, kind: ServingEventKind, *, detail: str | None = None) -> None:
        get_logger().debug(
            'serving %s agent=%s version=%s process=%s %s',
            kind,
            self._manifest.agent_id,
            self._manifest.version,
            self._process_id,
            detail or '',
        )
        listener = self._listener
        if listener is None:
            return
        listener(
            ServingEvent(
                kind=kind,
                agent_id=self._manifest.agent_id,
                version=self._manifest.version,
                instance_id=str(self._instance_id),
                process_id=self._process_id,
                detail=detail,
            )
        )


def _require_transport_pairing(transport: ServingTransport, endpoint_url: str | None) -> None:
    """Refuse a transport and endpoint that cannot describe a reachable process."""
    if transport == 'webhook' and not (endpoint_url and endpoint_url.strip()):
        message = (
            'webhook transport requires endpoint_url: the platform reaches a webhook '
            'process by URL, so a manifest without one announces a version nothing can call'
        )
        raise ValueError(message)
    if transport == 'worker' and endpoint_url:
        message = (
            'worker transport takes no endpoint_url: a worker is reached through the '
            'platform rather than at a URL of its own'
        )
        raise ValueError(message)


def _is_transient(exc: BaseException) -> bool:
    if is_plan_limit_error(exc):
        # A plan limit is not congestion. The same 429 will come back on every
        # attempt, so retrying only delays the message the developer can act on
        # by the whole backoff budget.
        return False
    if isinstance(exc, MaivnHTTPError):
        return (
            exc.status_code >= HTTPStatus.INTERNAL_SERVER_ERROR
            or exc.status_code in _RETRYABLE_STATUSES
        )
    # Connection refused, DNS failure, read timeout: the transport never got an
    # answer, which is exactly the blipped-Wi-Fi case.
    return isinstance(exc, httpx.TransportError)


def _is_terminal(exc: MaivnHTTPError) -> bool:
    return exc.status_code in _TERMINAL_STATUSES


def _detail(exc: BaseException) -> str:
    text = str(exc).strip()
    return text or type(exc).__name__


def _required_string(body: JsonObject, field: str) -> str:
    value = body.get(field)
    if not isinstance(value, str) or not value:
        message = f'serving response omitted {field}'
        raise MaivnSDKError(message)
    return value


def _served_version(body: JsonObject) -> ServedVersion:
    served = body.get('served')
    if not isinstance(served, dict):
        message = 'serving response omitted served'
        raise MaivnSDKError(message)
    return ServedVersion.model_validate(served)


__all__ = [
    'HEARTBEAT_INTERVAL_SECONDS',
    'SERVING_TRANSPORTS',
    'ServableScope',
    'ServingEvent',
    'ServingEventKind',
    'ServingListener',
    'ServingSession',
    'build_serving_manifest',
    'instruction_text',
    'process_instance_id',
    'resolve_project_id',
]
