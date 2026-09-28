"""Pulling the work a locally running agent's triggers produced, and reporting it.

Owner ruling 2026-09-04: we do not host agents, and the default transport is
*pull*. Announcing makes a process reachable; this is what actually reaches it.
Until now the announce half was the whole story, and a fire aimed at a
self-served agent sat on the platform's queue with nothing able to claim it.

Three things about the shape here are deliberate:

* **The claim carries no definition.** The platform holds none - it hands over
  an identity, the event payload, the message binding the trigger declared, and
  correlation. The instructions, the model and the tools are already here, in
  the process doing the claiming.
* **A claim is a lease, not a delivery.** It expires. A process that takes work
  and dies does not strand it; the platform hands it out again when the lease
  runs out. So a run that takes a while keeps saying ``working``, and a process
  that stops before finishing says ``abandoned`` rather than going quiet.
* **Reporting is not optional.** The result is what rejoins the ordinary path -
  invocation history, output binding delivery, the trace views. A run nobody
  reports is a run that, from every other surface, never happened.
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass, field
from http import HTTPStatus
from typing import TYPE_CHECKING, Final, Literal, TypeAlias, cast

import httpx
from maivn_contracts.scenarios.templates import render_event_template

from maivn._internal.compat.logging import get_logger
from maivn._internal.errors import MaivnHTTPError, MaivnSDKError
from maivn._internal.plan_limits import is_plan_limit_error
from maivn._internal.tracing import sdk_operation

if TYPE_CHECKING:
    from maivn._internal.client import Client
    from maivn._internal.transport.http import JsonObject

CLAIMS_PATH: Final = '/v1/agent-work/claims'

# How long an idle process waits before asking again. The platform returns its
# own advice with every empty answer; this is only the floor used when it does
# not, and when a claim call fails outright.
IDLE_POLL_SECONDS: Final = 2.0
# How often a process holding a claim tells the platform it is still working.
# The platform's lease is two minutes, so a third of it leaves room for two
# missed extensions before anything is at risk.
KEEPALIVE_INTERVAL_SECONDS: Final = 40.0

ReportStatus: TypeAlias = Literal['working', 'completed', 'failed', 'abandoned']

# Statuses below 500 that still mean "try again shortly" rather than "you are
# wrong". Mirrors serving.py, because a claim loop and a heartbeat loop face
# exactly the same network.
_RETRYABLE_STATUSES: Final[frozenset[int]] = frozenset(
    {HTTPStatus.REQUEST_TIMEOUT, HTTPStatus.TOO_MANY_REQUESTS},
)
_TERMINAL_STATUSES: Final[frozenset[int]] = frozenset(
    {HTTPStatus.UNAUTHORIZED, HTTPStatus.FORBIDDEN},
)
# The claim went to somebody else, or ran out. Not an error to retry: there is
# nothing left to report on.
_CLAIM_LOST = HTTPStatus.CONFLICT


@dataclass(frozen=True, slots=True)
class ClaimedWork:
    """One fire handed to this process, with the lease that proves it holds it.

    ``binding`` is the trigger's declared mapping from the event payload into
    session messages. It is carried verbatim because only this process can
    apply it - a callable binding names a module that lives here, not there.
    """

    fire_id: str
    claim_token: int
    invocation_id: str
    trigger_id: str
    agent_id: str
    agent_version: int
    session_id: str
    root_event_id: str
    payload: JsonObject = field(default_factory=dict)
    binding: JsonObject | None = None
    claim_until: str | None = None

    def messages(self) -> list[str]:
        """Render what the trigger asked, from the binding it declared.

        A template binding interpolates ``{{event.path}}`` against the payload.
        Anything else - a callable binding, a binding this SDK version does not
        know, no binding at all - falls back to handing the agent the event
        itself, which is honest: the process was given an event and no usable
        instruction for turning it into a question.
        """
        binding: JsonObject = self.binding or {}
        raw = binding.get('messages') if binding.get('kind') == 'template' else None
        if not isinstance(raw, list):
            return [json.dumps(self.payload, sort_keys=True, default=str)]
        templates = cast('list[object]', raw)
        if not templates:
            return [json.dumps(self.payload, sort_keys=True, default=str)]
        return [render_event_template(str(template), self.payload) for template in templates]


@dataclass(frozen=True, slots=True)
class RunReport:
    """What a runner can say about one claimed fire beyond the answer itself.

    The answer alone was enough while a served run left no trace but a terminal
    event. It is not enough now that a served run persists in the ledger like
    any other: the run meters and bills like any other too, and only this
    process ever sees the tokens - they were spent here, against a client the
    platform cannot see.

    A runner may still return a plain string. That is the honest shape for a
    runner that genuinely cannot count, and it reports no usage rather than
    reporting a zero it made up.
    """

    output: str = ''
    usage: Mapping[str, object] | None = None


RunnerResult: TypeAlias = 'str | RunReport | None'
WorkRunner: TypeAlias = Callable[[ClaimedWork], Awaitable[RunnerResult]]

# The buckets the platform meters, named as it names them. Anything else in a
# usage mapping is dropped here rather than sent: the platform ignores what it
# does not know, and a smaller report is easier to reason about than one whose
# extra keys silently go nowhere.
_USAGE_TOKEN_FIELDS: Final[tuple[str, ...]] = (
    'input_tokens',
    'output_tokens',
    'cache_read_input_tokens',
    'cache_creation_input_tokens',
)
# InvokeResponse.usage has published the short names since v1 compatibility.
_USAGE_ALIASES: Final[dict[str, str]] = {
    'cache_read_tokens': 'cache_read_input_tokens',
    'cache_creation_tokens': 'cache_creation_input_tokens',
}


def usage_report(usage: Mapping[str, object] | None) -> dict[str, object] | None:
    """Reduce a runner's usage mapping to the reading the platform can meter.

    Everything that is not a whole, non-negative token count is dropped, and a
    mapping with nothing left in it reports nothing at all. The platform
    validates this again on arrival and is right to - a third-party client can
    post whatever it likes - but a figure the SDK itself knows is nonsense
    should never be put on the wire as though this process stood behind it.
    """
    if not usage:
        return None
    counts: dict[str, object] = {}
    for key, value in usage.items():
        bucket = _USAGE_ALIASES.get(key, key)
        if bucket not in _USAGE_TOKEN_FIELDS:
            continue
        count = _token_count(value)
        if count is not None:
            counts[bucket] = count
    model = usage.get('model') or usage.get('model_id')
    if isinstance(model, str) and model.strip():
        counts['model'] = model.strip()
    if not any(bucket in counts for bucket in _USAGE_TOKEN_FIELDS):
        return None
    return counts


def _token_count(value: object) -> int | None:
    """Return one usable token count, or None for anything that is not one."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    if isinstance(value, float) and not value.is_integer():
        return None
    count = int(value)
    return count if count >= 0 else None


ClaimEvent: TypeAlias = Literal['claimed', 'completed', 'failed', 'released', 'idle', 'unreachable']
ClaimListener: TypeAlias = Callable[[str, ClaimEvent, str | None], None]


class ClaimLostError(MaivnSDKError):
    """The platform gave this claim to somebody else, or its lease ran out."""


class WorkClaimer:
    """The two API calls a serving process makes for its work."""

    def __init__(self, client: Client, *, process_id: str) -> None:
        """Bind one announced process id to the client that claims for it."""
        self._client = client
        self._process_id = process_id

    @property
    def process_id(self) -> str:
        """Return the announced process this claimer speaks for."""
        return self._process_id

    async def claim(self, *, max_fires: int = 1) -> tuple[list[ClaimedWork], float]:
        """Ask for work, returning what was handed over and how long to wait."""
        with sdk_operation('claim', attributes={'maivn.serve.cycle': 'claim'}):
            body = await self._client.event_http().post(
                CLAIMS_PATH,
                {'process_id': self._process_id, 'max_fires': max_fires},
            )
        raw = body.get('claims')
        entries = cast('list[object]', raw) if isinstance(raw, list) else []
        claims = [_claimed_work(entry) for entry in entries]
        poll_after = body.get('poll_after_seconds')
        wait = float(poll_after) if isinstance(poll_after, (int, float)) else IDLE_POLL_SECONDS
        return claims, wait

    async def report(
        self,
        work: ClaimedWork,
        status: ReportStatus,
        *,
        output: str = '',
        reason: str = '',
        usage: Mapping[str, object] | None = None,
    ) -> None:
        """Tell the platform what happened to one claim this process holds."""
        body: JsonObject = {
            'process_id': self._process_id,
            'claim_token': work.claim_token,
            'status': status,
            'output': output,
            'reason': reason,
        }
        # Omitted rather than sent as null when there is nothing to report, so
        # a process running against a platform that predates usage reporting
        # keeps working: the report body is strict about fields it does not
        # know, and a lost run would be a high price for a missing meter.
        if usage:
            body['usage'] = dict(usage)
        try:
            with sdk_operation(
                'claim.report',
                attributes={'maivn.claim.id': work.fire_id},
            ):
                _ = await self._client.event_http().post(
                    f'{CLAIMS_PATH}/{work.fire_id}',
                    body,
                )
        except MaivnHTTPError as exc:
            if exc.status_code == _CLAIM_LOST:
                message = f'claim on {work.fire_id} expired or was reclaimed'
                raise ClaimLostError(message) from exc
            raise


class WorkLoop:
    """Claims work, runs it here, reports the result, forever.

    Written to be one of two coroutines a serving process runs. It owns no
    timers the heartbeat needs and shares no lock with it, so neither can
    starve the other; a transient failure costs this loop a pause and nothing
    else, and only a refusal the platform will keep repeating - a revoked key,
    a process it no longer recognises - stops it.
    """

    def __init__(
        self,
        claimer: WorkClaimer,
        runner: WorkRunner,
        *,
        idle_poll_seconds: float = IDLE_POLL_SECONDS,
        keepalive_interval_seconds: float = KEEPALIVE_INTERVAL_SECONDS,
        listener: ClaimListener | None = None,
    ) -> None:
        """Bind one claimer to the callable that actually runs the work."""
        self._claimer = claimer
        self._runner = runner
        self._idle = idle_poll_seconds
        self._keepalive = keepalive_interval_seconds
        self._listener = listener
        self._held: ClaimedWork | None = None

    @property
    def held(self) -> ClaimedWork | None:
        """Return the claim this loop is running right now, if any."""
        return self._held

    def rebind(self, claimer: WorkClaimer) -> None:
        """Point this loop at a new process identity after a re-announcement."""
        self._claimer = claimer

    async def run(self) -> None:
        """Claim and run until cancelled, handing back anything unfinished."""
        try:
            while True:
                wait = await self._one_pass()
                await asyncio.sleep(wait)
        finally:
            await self._release_held()

    async def _one_pass(self) -> float:
        """Claim once and run whatever came back. Returns how long to wait."""
        try:
            claims, wait = await self._claimer.claim()
        except MaivnHTTPError as exc:
            if _is_terminal(exc):
                raise
            self._emit('', 'unreachable', _detail(exc))
            return self._idle
        except httpx.HTTPError as exc:
            self._emit('', 'unreachable', _detail(exc))
            return self._idle
        if not claims:
            self._emit('', 'idle', None)
            return wait
        for work in claims:
            await self._perform(work)
        # Work arrived, so ask again immediately: a busy trigger should not be
        # throttled by the idle pause.
        return 0.0

    async def _perform(self, work: ClaimedWork) -> None:
        """Run one claim here and report what happened, keeping the lease alive."""
        self._held = work
        self._emit(work.fire_id, 'claimed', None)
        keepalive = asyncio.ensure_future(self._keep_alive(work))
        answer = ''
        reason = ''
        usage: dict[str, object] | None = None
        settled: ClaimEvent = 'completed'
        try:
            result = await self._runner(work)
            if isinstance(result, RunReport):
                answer = result.output
                usage = usage_report(result.usage)
            else:
                answer = result or ''
        except asyncio.CancelledError:
            # The process is stopping part-way through a run. Reporting an
            # outcome here would be inventing one; run()'s own exit hands the
            # claim back so the platform can give the work to somebody else.
            raise
        except Exception as exc:  # noqa: BLE001 - a developer's agent may raise anything, and a
            # raised exception is a failed run, not a failed loop.
            settled, reason = 'failed', _detail(exc)
        finally:
            await _stop(keepalive)
        self._held = None
        status: ReportStatus = 'failed' if settled == 'failed' else 'completed'
        await self._report(work, status, output=answer, reason=reason, usage=usage)
        self._emit(work.fire_id, settled, reason or None)

    async def _keep_alive(self, work: ClaimedWork) -> None:
        """Say 'still working' until the run finishes or the claim is gone."""
        while True:
            await asyncio.sleep(self._keepalive)
            try:
                await self._claimer.report(work, 'working')
            except ClaimLostError:
                # Somebody else has it now. Stop extending; the report at the
                # end will say the same thing and the run's own result is
                # discarded there rather than silently overwriting theirs.
                return
            except (MaivnHTTPError, httpx.HTTPError) as exc:
                # One missed extension is not the end of the lease. Keep trying
                # until the run finishes or the lease genuinely runs out.
                self._emit(work.fire_id, 'unreachable', _detail(exc))

    async def _release_held(self) -> None:
        """Hand back a claim this process took but never finished.

        This runs on the way out of a cancelled loop - which is what Ctrl-C
        produces - so the fire becomes claimable again immediately instead of
        waiting out its lease. Failing to say so is survivable; the lease
        expiring is exactly the backstop for it.
        """
        work = self._held
        if work is None:
            return
        self._held = None
        await self._report(work, 'abandoned', reason='serving process stopped')
        self._emit(work.fire_id, 'released', None)

    async def _report(
        self,
        work: ClaimedWork,
        status: ReportStatus,
        *,
        output: str = '',
        reason: str = '',
        usage: Mapping[str, object] | None = None,
    ) -> None:
        """Report one outcome, retrying transient failures with backoff."""
        delay = _INITIAL_RETRY_BACKOFF_SECONDS
        for attempt in range(1, _MAX_REPORT_ATTEMPTS + 1):
            try:
                await self._claimer.report(
                    work,
                    status,
                    output=output,
                    reason=reason,
                    usage=usage,
                )
            # PERF203: catching inside the loop is what a retry IS; the cost of
            # a handler frame is irrelevant next to the network call it guards.
            except ClaimLostError as exc:  # noqa: PERF203
                self._emit(work.fire_id, 'released', _detail(exc))
                return
            except (MaivnHTTPError, httpx.HTTPError) as exc:
                if attempt == _MAX_REPORT_ATTEMPTS or not _is_transient(exc):
                    self._emit(work.fire_id, 'unreachable', _detail(exc))
                    return
                await asyncio.sleep(delay)
                delay = min(delay * 2, _MAX_RETRY_BACKOFF_SECONDS)
            else:
                return

    def _emit(self, fire_id: str, event: ClaimEvent, detail: str | None) -> None:
        get_logger().debug('agent work %s fire=%s %s', event, fire_id, detail or '')
        listener = self._listener
        if listener is not None:
            listener(fire_id, event, detail)


_INITIAL_RETRY_BACKOFF_SECONDS: Final = 0.5
_MAX_RETRY_BACKOFF_SECONDS: Final = 8.0
_MAX_REPORT_ATTEMPTS: Final = 5


async def _stop(task: asyncio.Future[None]) -> None:
    """Cancel one helper task without swallowing our own cancellation.

    ``await task`` after cancelling it would raise the helper's CancelledError,
    and catching that also catches the caller's OWN cancellation - which is how
    a serving process that was told to stop kept claiming work forever. gather
    with return_exceptions hands the helper's outcome back as a value and lets
    a real cancellation of this coroutine through.
    """
    _ = task.cancel()
    _ = await asyncio.gather(task, return_exceptions=True)


def _claimed_work(entry: object) -> ClaimedWork:
    if not isinstance(entry, dict):
        message = 'agent work claim was not an object'
        raise MaivnSDKError(message)
    claim = cast('JsonObject', entry)
    raw_work = claim.get('work')
    if not isinstance(raw_work, dict):
        message = 'agent work claim carried no hand-over'
        raise MaivnSDKError(message)
    work = cast('JsonObject', raw_work)
    claim_until = claim.get('claim_until')
    return ClaimedWork(
        fire_id=_required_str(claim, 'fire_id'),
        claim_token=_required_int(claim, 'claim_token'),
        invocation_id=_required_str(work, 'invocation_id'),
        trigger_id=_required_str(work, 'trigger_id'),
        agent_id=_required_str(work, 'agent_id'),
        agent_version=_required_int(work, 'agent_version'),
        session_id=_required_str(work, 'session_id'),
        root_event_id=_required_str(work, 'root_event_id'),
        payload=_object_field(work, 'payload') or {},
        binding=_object_field(work, 'message_binding'),
        claim_until=claim_until if isinstance(claim_until, str) else None,
    )


def _object_field(body: JsonObject, field_name: str) -> JsonObject | None:
    value = body.get(field_name)
    if not isinstance(value, dict):
        return None
    return dict(cast('JsonObject', value))


def _required_str(body: JsonObject, field_name: str) -> str:
    value = body.get(field_name)
    if not isinstance(value, str) or not value:
        message = f'agent work claim omitted {field_name}'
        raise MaivnSDKError(message)
    return value


def _required_int(body: JsonObject, field_name: str) -> int:
    value = body.get(field_name)
    if isinstance(value, bool) or not isinstance(value, int):
        message = f'agent work claim omitted {field_name}'
        raise MaivnSDKError(message)
    return value


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
    return isinstance(exc, httpx.TransportError)


def _is_terminal(exc: MaivnHTTPError) -> bool:
    return exc.status_code in _TERMINAL_STATUSES


def _detail(exc: BaseException) -> str:
    text = str(exc).strip()
    return text or type(exc).__name__


__all__ = [
    'CLAIMS_PATH',
    'IDLE_POLL_SECONDS',
    'KEEPALIVE_INTERVAL_SECONDS',
    'ClaimEvent',
    'ClaimListener',
    'ClaimLostError',
    'ClaimedWork',
    'ReportStatus',
    'RunReport',
    'WorkClaimer',
    'WorkLoop',
    'WorkRunner',
    'usage_report',
]
