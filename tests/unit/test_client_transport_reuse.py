"""`Client` shares one lease-managed transport instead of rebuilding it per call.

A bare `httpx.AsyncClient()` with no transport builds its own default transport
-- and therefore a fresh SSLContext plus a fresh TLS handshake -- on every
construction. `Client._http()`/`control_http()`/`event_http()` build a new
`HttpJsonClient` wrapper on every call (required so each request gets its own
`httpx.AsyncClient`, safe to use from any event loop -- see
`test_transport_leases.py`), so leaving the injected transport `None` by
default paid that setup cost on every single request. `Client.__init__` now
builds one default transport per plane (shared across planes unless the
caller injects its own), reused via `transport.leases.lease_transport` by
every request-scoped `httpx.AsyncClient` those wrappers create.
"""

from __future__ import annotations

import asyncio
import threading
from http import HTTPStatus

import httpx

from maivn import Client
from maivn._internal.transport.leases import LoopLocalTransport, lease_transport


def test_client_builds_one_default_transport_shared_by_every_plane() -> None:
    """No transport injected: all three planes share one lease-managed transport."""
    client = Client(api_key='test-key', base_url='http://testserver')

    assert isinstance(client._transport, LoopLocalTransport)  # noqa: SLF001 # pyright: ignore[reportPrivateUsage]
    assert client._control_transport is client._transport  # noqa: SLF001 # pyright: ignore[reportPrivateUsage]
    assert client._event_transport is client._transport  # noqa: SLF001 # pyright: ignore[reportPrivateUsage]


def test_client_gives_each_http_call_its_own_wrapper_sharing_the_transport() -> None:
    """`_http()` stays call-scoped (a run may hold it open via `async with`) ...

    ... but every wrapper it returns shares the one default transport, so the
    expensive part -- the transport, its SSLContext and its connection pool --
    is still built once per `Client`, not once per call.
    """
    client = Client(api_key='test-key', base_url='http://testserver')

    first = client._http()  # noqa: SLF001 # pyright: ignore[reportPrivateUsage]
    second = client._http()  # noqa: SLF001 # pyright: ignore[reportPrivateUsage]

    assert first is not second
    assert first._transport is client._transport  # noqa: SLF001 # pyright: ignore[reportPrivateUsage]
    assert second._transport is client._transport  # noqa: SLF001 # pyright: ignore[reportPrivateUsage]


def test_concurrent_runs_can_each_hold_their_own_http_call_open() -> None:
    """Two concurrent invokes each bounding their own `_http()` result must not collide.

    A regression guard: caching `_http()`'s result (an earlier version of this
    fix did) breaks the invoke stream's tool-result poster, which enters its
    `_http()` client as `async with tool_http:` for the run's own lifetime --
    `HttpJsonClient.__aenter__` refuses to be entered twice at once, so two
    concurrent runs sharing one cached wrapper would raise
    `RuntimeError: HTTP client context is already open`.
    """
    client = Client(
        api_key='test-key',
        base_url='http://testserver',
        transport=httpx.MockTransport(
            lambda _request: httpx.Response(HTTPStatus.OK, json={'ok': True})
        ),
    )
    entered = 0

    async def hold_open() -> None:
        nonlocal entered
        async with client._http():  # noqa: SLF001 # pyright: ignore[reportPrivateUsage]
            entered += 1
            await asyncio.sleep(0.01)

    async def run() -> None:
        await asyncio.gather(hold_open(), hold_open())

    asyncio.run(run())

    expected_concurrent_runs = 2
    assert entered == expected_concurrent_runs


def test_client_control_and_event_http_are_stable_and_share_the_one_public_origin() -> None:
    """`control_http`/`event_http` are cached too, and both point at the one public origin.

    There is no public per-plane base URL setting any more: every request,
    whatever internal grouping built it, targets the single ``base_url``.
    """
    client = Client(api_key='test-key', base_url='http://testserver')

    assert client.control_http() is client.control_http()
    assert client.event_http() is client.event_http()
    assert client.control_http().base_url == client.event_http().base_url == 'http://testserver'


def test_an_explicitly_injected_transport_overrides_the_shared_default() -> None:
    """A caller-supplied transport (tests, custom pooling) is never replaced."""
    injected = httpx.MockTransport(lambda _request: httpx.Response(200, json={}))

    client = Client(api_key='test-key', base_url='http://testserver', transport=injected)

    assert client._transport is injected  # noqa: SLF001 # pyright: ignore[reportPrivateUsage]
    assert client._control_transport is injected  # noqa: SLF001 # pyright: ignore[reportPrivateUsage]
    assert client._event_transport is injected  # noqa: SLF001 # pyright: ignore[reportPrivateUsage]


def test_a_per_group_transport_override_still_wins_over_the_shared_default() -> None:
    """An internal test seam can still isolate one request grouping's transport.

    Nothing public exposes this any more (no `control_transport=`/
    `event_transport=` kwarg): it is only the private, underscore-prefixed
    `_internal_transport_overrides` constructor seam, kept for internal
    tests that need to intercept one HTTP call grouping without touching
    another.
    """
    control_only = httpx.MockTransport(lambda _request: httpx.Response(200, json={}))

    client = Client(
        api_key='test-key',
        base_url='http://testserver',
        _internal_transport_overrides=(control_only, None),
    )

    assert client._control_transport is control_only  # noqa: SLF001 # pyright: ignore[reportPrivateUsage]
    assert client._transport is not control_only  # noqa: SLF001 # pyright: ignore[reportPrivateUsage]
    assert client._event_transport is client._transport  # noqa: SLF001 # pyright: ignore[reportPrivateUsage]


def test_a_second_event_loop_never_waits_for_a_stream_open_on_the_first() -> None:
    """A mid-run interrupt answer runs its own loop while the run's stream stays open.

    With one pool shared across loops, the answer waited for the stream to close,
    and the stream waited for the answer, until the server timed the run out.
    """
    transport = LoopLocalTransport(
        factory=lambda: httpx.MockTransport(
            lambda _request: httpx.Response(HTTPStatus.OK, json={'ok': True})
        ),
    )
    opened = threading.Event()
    release = threading.Event()

    async def hold_stream_open() -> None:
        async with httpx.AsyncClient(
            transport=lease_transport(transport), base_url='https://data.example.test'
        ) as client:
            _ = await client.get('/stream')
            opened.set()
            _ = await asyncio.to_thread(release.wait, 5)

    holder = threading.Thread(target=lambda: asyncio.run(hold_stream_open()))
    holder.start()
    try:
        assert opened.wait(5)

        async def answer() -> int:
            async with httpx.AsyncClient(
                transport=lease_transport(transport), base_url='https://data.example.test'
            ) as client:
                response = await client.post('/answer')
                return response.status_code

        # A shared pool blocks this call outright (the wait is shielded from
        # cancellation), so run it on a daemon thread: a regression fails fast
        # instead of hanging the suite.
        statuses: list[int] = []
        answerer = threading.Thread(
            target=lambda: statuses.append(asyncio.run(answer())), daemon=True
        )
        answerer.start()
        answerer.join(5)
        assert statuses == [HTTPStatus.OK]
    finally:
        release.set()
        holder.join(5)
