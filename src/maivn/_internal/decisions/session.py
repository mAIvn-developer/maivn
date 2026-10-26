"""Decision runs: link SDK decisions to the run they belong to.

A decision-only run is opened with ``POST /v1/decisions/runs`` and closed with
``POST /v1/decisions/runs/{session_id}/complete``. While it is open, and while the SDK
executes a local tool for an agent run, decisions carry that run's session id so the
platform records each one as a Decision step in the run's trace.
"""

from __future__ import annotations

import inspect
import logging
from contextvars import ContextVar, Token
from dataclasses import dataclass
from typing import TYPE_CHECKING, Literal

from maivn._internal.api.async_stream import run_blocking

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable
    from types import TracebackType

    from typing_extensions import Self

    from maivn._internal.client import Client

logger = logging.getLogger(__name__)

DECISION_RUNS_PATH = '/v1/decisions/runs'
DECISION_RUN_COMPLETE_PATH = '/v1/decisions/runs/{session_id}/complete'

current_decision_session: ContextVar[str | None] = ContextVar(
    'current_decision_session',
    default=None,
)
"""Session id of the open :func:`decision_run`, if any."""

current_tool_session: ContextVar[str | None] = ContextVar(
    'current_tool_session',
    default=None,
)
"""Session id of the agent invoke whose local tool the SDK is executing, if any."""


def linked_session_id(explicit: str | None = None) -> str | None:
    """Resolve the session a decision links to: explicit, decision run, then tool time."""
    return explicit or current_decision_session.get() or current_tool_session.get()


class decision_run:  # noqa: N801 - public context-manager factory, named like a function.
    """Open a decision-only run; usable with ``with`` and ``async with``.

    Decisions made inside carry the run's session id, so the platform shows each as a
    Decision step in the run's trace. On exit the run completes as ``failed`` when an
    exception escaped and ``completed`` otherwise. A completion failure never masks the
    caller's exception. ``client`` is an ordinary :class:`maivn.Client`; without one a
    default client is built from ``MAIVN_BASE_URL`` and ``MAIVN_API_KEY``.
    """

    def __init__(self, name: str, *, client: Client | None = None) -> None:
        self._name = name
        self._client = client
        self._session_id: str | None = None
        self._token: Token[str | None] | None = None

    @property
    def session_id(self) -> str:
        """Return the opened run's session id (available after entering)."""
        if self._session_id is None:
            message = 'decision_run is not open'
            raise RuntimeError(message)
        return self._session_id

    def _platform(self) -> Client:
        if self._client is None:
            from maivn._internal.client import Client  # noqa: PLC0415 - avoid an import cycle.

            self._client = Client()
        return self._client

    async def _open(self) -> None:
        http = self._platform()._http()  # noqa: SLF001  # pyright: ignore[reportPrivateUsage]
        payload = await http.post(DECISION_RUNS_PATH, {'name': self._name})
        session_id = payload.get('session_id')
        if not isinstance(session_id, str) or not session_id:
            message = 'The platform did not return a decision run session id'
            raise RuntimeError(message)
        self._session_id = session_id

    def _bind(self) -> None:
        # Bound in the caller's own context: a sync facade runs the coroutine in a copy.
        self._token = current_decision_session.set(self.session_id)

    def _unbind(self) -> None:
        token, self._token = self._token, None
        if token is not None:
            try:
                current_decision_session.reset(token)
            except ValueError:  # Exited in a different context than it was entered.
                current_decision_session.set(None)

    async def _close(self, *, failed: bool) -> None:
        status: Literal['completed', 'failed'] = 'failed' if failed else 'completed'
        try:
            http = self._platform()._http()  # noqa: SLF001  # pyright: ignore[reportPrivateUsage]
            await http.post(
                DECISION_RUN_COMPLETE_PATH.format(session_id=self.session_id),
                {'status': status},
            )
        except Exception:  # noqa: BLE001 - completion must never mask the caller's outcome.
            logger.warning('Decision run completion failed: session_id=%s', self._session_id)

    def __enter__(self) -> Self:
        run_blocking(self._open)
        self._bind()
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        _ = exc, traceback
        self._unbind()
        try:
            run_blocking(lambda: self._close(failed=exc_type is not None))
        except Exception:  # noqa: BLE001 - completion must never mask the caller's outcome.
            logger.warning('Decision run completion failed: session_id=%s', self._session_id)

    async def __aenter__(self) -> Self:
        await self._open()
        self._bind()
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        _ = exc, traceback
        self._unbind()
        await self._close(failed=exc_type is not None)


@dataclass(frozen=True, slots=True)
class DecisionAppResult:
    """A decision-only run's report text and the session that holds its trace."""

    text: str
    session_id: str


class DecisionApp:
    """A decision-only executor: runs ``run()`` inside a :func:`decision_run`.

    ``run`` takes no arguments and returns the report text, synchronously or as an
    awaitable. ``name`` labels the run in the trace; it defaults to the callable's name.
    """

    def __init__(
        self,
        run: Callable[[], str | Awaitable[str]],
        *,
        name: str | None = None,
    ) -> None:
        self._run = run
        self.name = name or _derived_name(run)

    def execute(self, *, client: Client | None = None) -> DecisionAppResult:
        """Run synchronously (outside an event loop) and return the report and session."""
        with decision_run(self.name, client=client) as opened:
            value = self._run()
            if inspect.isawaitable(value):
                awaitable = value

                async def _await() -> str:
                    return await awaitable

                value = run_blocking(_await)
            return DecisionAppResult(text=str(value), session_id=opened.session_id)

    async def aexecute(self, *, client: Client | None = None) -> DecisionAppResult:
        """Run asynchronously and return the report and session."""
        async with decision_run(self.name, client=client) as opened:
            value = self._run()
            if inspect.isawaitable(value):
                value = await value
            return DecisionAppResult(text=str(value), session_id=opened.session_id)


def _derived_name(run: Callable[[], object]) -> str:
    raw = getattr(run, '__name__', None)
    if not isinstance(raw, str) or raw == '<lambda>':
        return 'Decision app'
    return (raw.replace('_', ' ').strip().capitalize() or 'Decision app')[:120]


__all__ = [
    'DecisionApp',
    'DecisionAppResult',
    'current_decision_session',
    'current_tool_session',
    'decision_run',
    'linked_session_id',
]
