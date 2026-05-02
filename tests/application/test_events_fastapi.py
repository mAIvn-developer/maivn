"""Tests for the ``maivn.events.fastapi`` adapter.

Coverage focuses on the wiring contract (router shape, registry lifecycle,
auth dependency injection, factory hook, optional-dep error path) — i.e.
the surface an SDK consumer interacts with. End-to-end SSE streaming over
``httpx.AsyncClient`` + ``ASGITransport`` is intentionally NOT exercised
here; sse-starlette's exit-signal listener interacts poorly with
pytest-asyncio's per-test event loop, leading to flaky CI without buying
real coverage. The streaming itself is covered by
``test_event_bridge_concurrency.py`` (bridge-level) and by Studio/Booth's
own end-to-end suites.
"""

from __future__ import annotations

from collections.abc import Iterator

import pytest
from fastapi import FastAPI, HTTPException, Request
from fastapi.testclient import TestClient

from maivn.events import BridgeRegistry, EventBridge
from maivn.events.fastapi import (
    create_event_router,
    get_event_bridge,
    mount_events,
    remove_event_bridge,
)

# MARK: Helpers


@pytest.fixture(autouse=True)
def _reset_sse_starlette_app_status() -> Iterator[None]:
    """Reset sse-starlette's module-level shutdown event between tests.

    sse-starlette uses an ``asyncio.Event`` bound to the loop that first
    served a request. ``pytest-asyncio`` creates a fresh loop per test,
    which would otherwise crash the second test with ``RuntimeError:
    Event is bound to a different event loop``.
    """
    from sse_starlette.sse import AppStatus

    AppStatus.should_exit = False
    AppStatus.should_exit_event = None  # type: ignore[assignment]
    yield
    AppStatus.should_exit = False
    AppStatus.should_exit_event = None  # type: ignore[assignment]


@pytest.fixture
def fresh_registry() -> Iterator[BridgeRegistry]:
    """Hand each test its own registry so they don't clobber each other."""
    registry = BridgeRegistry()
    yield registry
    # Drain anything still alive.
    for session_id in list(registry._bridges):
        registry.remove(session_id)


def _make_app(**router_kwargs: object) -> FastAPI:
    app = FastAPI()
    mount_events(app, **router_kwargs)  # type: ignore[arg-type]
    return app


# MARK: Mount + basic shape


def test_mount_events_registers_route_at_default_path(
    fresh_registry: BridgeRegistry,
) -> None:
    app = _make_app(registry=fresh_registry)
    paths = {route.path for route in app.routes}
    assert "/maivn/events/{session_id}" in paths


def test_mount_events_supports_custom_prefix_and_path(
    fresh_registry: BridgeRegistry,
) -> None:
    app = _make_app(
        prefix="/api",
        path="/v1/events/{session_id}",
        registry=fresh_registry,
    )
    paths = {route.path for route in app.routes}
    assert "/api/v1/events/{session_id}" in paths


def test_create_event_router_rejects_path_without_session_id_placeholder() -> None:
    with pytest.raises(ValueError, match=r"\{session_id\}"):
        create_event_router(path="/events")


# MARK: get_event_bridge / remove_event_bridge


def test_get_event_bridge_creates_on_demand_then_returns_same_instance(
    fresh_registry: BridgeRegistry,
) -> None:
    first = get_event_bridge("sess-1", registry=fresh_registry)
    second = get_event_bridge("sess-1", registry=fresh_registry)
    assert first is second


def test_get_event_bridge_defaults_to_frontend_safe_audience(
    fresh_registry: BridgeRegistry,
) -> None:
    bridge = get_event_bridge("safe-default", registry=fresh_registry)
    assert bridge.audience == "frontend_safe"


def test_get_event_bridge_with_create_false_raises_for_missing(
    fresh_registry: BridgeRegistry,
) -> None:
    with pytest.raises(KeyError):
        get_event_bridge("missing", create=False, registry=fresh_registry)


def test_remove_event_bridge_is_idempotent(fresh_registry: BridgeRegistry) -> None:
    remove_event_bridge("never-existed", registry=fresh_registry)  # no raise
    bridge = get_event_bridge("sess-x", registry=fresh_registry)
    remove_event_bridge("sess-x", registry=fresh_registry)
    assert bridge._closed is True


# MARK: Auth hook (sync via TestClient)


def test_auth_dependency_blocks_unauthorized_requests(
    fresh_registry: BridgeRegistry,
) -> None:
    """The ``auth`` hook can reject requests before the SSE handler runs."""

    async def reject_all(_: Request) -> None:
        raise HTTPException(status_code=401, detail="nope")

    app = FastAPI()
    mount_events(app, auth=reject_all, registry=fresh_registry)

    with TestClient(app) as client:
        response = client.get("/maivn/events/blocked")
        assert response.status_code == 401
        assert response.json() == {"detail": "nope"}


def test_auth_dependency_can_inspect_request(
    fresh_registry: BridgeRegistry,
) -> None:
    """The ``auth`` hook receives the FastAPI ``Request`` like any dependency."""

    seen_headers: list[str] = []

    async def require_token(request: Request) -> None:
        token = request.headers.get("x-maivn-token")
        seen_headers.append(token or "")
        if token != "let-me-in":
            raise HTTPException(status_code=403)

    app = FastAPI()
    mount_events(app, auth=require_token, registry=fresh_registry)

    with TestClient(app) as client:
        no_header = client.get("/maivn/events/sess-auth")
        assert no_header.status_code == 403

    assert seen_headers == [""]


# MARK: Custom factory


def test_factory_lets_callers_supply_a_subclass(
    fresh_registry: BridgeRegistry,
) -> None:
    constructed: list[str] = []

    class TaggedBridge(EventBridge):
        def __init__(self, session_id: str) -> None:
            super().__init__(session_id)
            constructed.append(session_id)

    bridge = get_event_bridge(
        "factory-sess",
        factory=TaggedBridge,
        registry=fresh_registry,
    )
    assert isinstance(bridge, TaggedBridge)
    assert constructed == ["factory-sess"]


# MARK: Endpoint shape


def test_route_signature_matches_sse_contract(
    fresh_registry: BridgeRegistry,
) -> None:
    """Confirm the registered route accepts ``last_event_id`` and is a GET.

    End-to-end SSE streaming is exercised at the bridge level (see
    ``test_event_bridge_concurrency.py`` and Studio/Booth integration
    suites). We avoid consuming the SSE body here because FastAPI's
    sync ``TestClient`` does not cleanly tear down streaming responses
    from sse-starlette during pytest's per-test event loop teardown,
    which leads to flaky CI without buying real coverage.
    """
    app = _make_app(registry=fresh_registry)
    [route] = [r for r in app.routes if getattr(r, "path", None) == "/maivn/events/{session_id}"]
    # FastAPI's APIRoute has .methods + .dependant
    assert "GET" in route.methods  # type: ignore[attr-defined]
    # The endpoint signature carries session_id (path) + last_event_id (query).
    param_names = {p.name for p in route.dependant.path_params}  # type: ignore[attr-defined]
    param_names |= {p.name for p in route.dependant.query_params}  # type: ignore[attr-defined]
    assert "session_id" in param_names
    assert "last_event_id" in param_names


# MARK: Optional-dep error path


def test_helpful_error_when_fastapi_missing(monkeypatch: pytest.MonkeyPatch) -> None:
    """If a consumer skips the [fastapi] extra, the error names the fix."""
    import maivn.events.fastapi as fastapi_module

    real_require = fastapi_module._require_fastapi

    def fake_require() -> object:
        raise ModuleNotFoundError(
            "maivn.events.fastapi requires fastapi + sse-starlette. "
            "Install with `pip install maivn[fastapi]`."
        )

    monkeypatch.setattr(fastapi_module, "_require_fastapi", fake_require)
    try:
        with pytest.raises(ModuleNotFoundError, match=r"maivn\[fastapi\]"):
            fastapi_module.create_event_router()
    finally:
        monkeypatch.setattr(fastapi_module, "_require_fastapi", real_require)
