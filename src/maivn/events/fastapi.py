"""FastAPI / Starlette adapter for streaming :class:`EventBridge` events.

This module is the **one-liner** developers reach for when they want their
maivn-powered backend to push live execution events to their frontend
over Server-Sent Events. It hides the EventBridge / BridgeRegistry / SSE
plumbing behind a small, ergonomic surface.

Quickstart::

    from fastapi import FastAPI
    from maivn.events.fastapi import mount_events, get_event_bridge

    app = FastAPI()

    # Wires GET /maivn/events/{session_id} into the app. That's it.
    mount_events(app)

    @app.post("/start")
    async def start():
        bridge = get_event_bridge("session-123")
        await bridge.emit_status_message("orchestrator", "Working...")
        await bridge.emit_final("Done")
        return {"ok": True}

The frontend then connects with any HTML5 ``EventSource`` (or any HTTP
client that speaks SSE). See ``docs/guides/frontend-events.md`` for
client examples in JavaScript, TypeScript, Swift, Kotlin, Go, Python,
and cURL.

This adapter is intentionally framework-specific so the base SDK does
**not** force ``fastapi`` / ``starlette`` / ``sse-starlette`` on every
consumer. Install the optional extra::

    pip install maivn[fastapi]

If you use a different framework (Flask, aiohttp, Django, raw ASGI,
etc.), use :class:`maivn.events.EventBridge` directly — see the
"Custom backend integration" section in the frontend events guide.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import TYPE_CHECKING, Any

from . import BridgeAudience, BridgeRegistry, EventBridge

if TYPE_CHECKING:
    from fastapi import APIRouter, FastAPI

# MARK: Optional dependencies


def _require_fastapi() -> Any:
    try:
        import fastapi as _fastapi  # noqa: PLC0415
    except ModuleNotFoundError as exc:
        raise ModuleNotFoundError(
            "maivn.events.fastapi requires fastapi + sse-starlette. "
            "Install with `pip install maivn[fastapi]`."
        ) from exc
    return _fastapi


def _require_sse_starlette() -> Any:
    try:
        from sse_starlette.sse import EventSourceResponse  # noqa: PLC0415
    except ModuleNotFoundError as exc:
        raise ModuleNotFoundError(
            "maivn.events.fastapi requires sse-starlette. "
            "Install with `pip install maivn[fastapi]`."
        ) from exc
    return EventSourceResponse


# MARK: Module-level registry

_default_registry = BridgeRegistry()

AuthHook = Callable[..., Awaitable[None]]
BridgeFactory = Callable[[str], EventBridge]


# MARK: Public helpers


def get_event_bridge(
    session_id: str,
    *,
    create: bool = True,
    factory: BridgeFactory | None = None,
    registry: BridgeRegistry | None = None,
    audience: BridgeAudience = "frontend_safe",
) -> EventBridge:
    """Return the bridge for ``session_id``, creating one if needed.

    Parameters
    ----------
    session_id:
        Logical session identifier. The frontend uses the same id when
        opening its SSE connection.
    create:
        When ``True`` (default), missing bridges are created on demand.
        When ``False``, raises ``KeyError`` if no bridge exists.
    factory:
        Optional callable that constructs a custom :class:`EventBridge`
        subclass. Receives the session id and must return an
        ``EventBridge`` instance.
    registry:
        Use a custom registry instead of the module-level default. Useful
        for tests or for partitioning bridges across mounted sub-apps.
    audience:
        Audience used when creating a bridge without a custom factory. The
        FastAPI adapter defaults to ``"frontend_safe"`` because events are
        usually consumed by end-user browser clients.
    """
    target_registry = registry if registry is not None else _default_registry
    existing = target_registry.get(session_id)
    if existing is not None:
        return existing
    if not create:
        raise KeyError(f"No event bridge registered for session {session_id!r}")
    bridge_factory = factory or (lambda sid: EventBridge(sid, audience=audience))
    return target_registry.create(session_id, factory=bridge_factory)


def remove_event_bridge(
    session_id: str,
    *,
    registry: BridgeRegistry | None = None,
) -> None:
    """Close and forget the bridge for ``session_id``.

    Idempotent — safe to call when no bridge exists.
    """
    (registry if registry is not None else _default_registry).remove(session_id)


# MARK: Router factory


def create_event_router(
    *,
    prefix: str = "/maivn",
    path: str = "/events/{session_id}",
    auth: AuthHook | None = None,
    factory: BridgeFactory | None = None,
    registry: BridgeRegistry | None = None,
    audience: BridgeAudience = "frontend_safe",
    heartbeat_interval: float | None = None,
    tags: list[str] | None = None,
) -> APIRouter:
    """Return a FastAPI ``APIRouter`` that exposes the SSE event endpoint.

    Mount it with ``app.include_router(create_event_router())`` to wire
    ``GET {prefix}{path}`` into your application.

    Parameters
    ----------
    prefix:
        URL prefix. Default ``"/maivn"``. Pass ``""`` to mount at the
        application root.
    path:
        URL path under the prefix. Must contain ``{session_id}``. Default
        ``"/events/{session_id}"``.
    auth:
        Optional async dependency callable run before each connection.
        Use it to enforce authentication / authorization. The callable
        receives the request via FastAPI's dependency injection — declare
        whatever parameters you need (``Request``, ``BackgroundTasks``,
        ``Depends(...)``, etc.). Raise ``HTTPException`` to reject.
    factory:
        Optional :class:`EventBridge` factory; see :func:`get_event_bridge`.
    registry:
        Optional :class:`BridgeRegistry`; see :func:`get_event_bridge`.
    audience:
        Audience used for automatically created bridges when ``factory`` is
        not supplied. Defaults to ``"frontend_safe"`` for browser-facing SSE.
    heartbeat_interval:
        Override the bridge default for this endpoint only. Useful when
        the client lives behind a proxy with an aggressive idle timeout.
    tags:
        OpenAPI tags. Default ``["maivn-events"]``.
    """
    fastapi = _require_fastapi()
    EventSourceResponse = _require_sse_starlette()  # noqa: N806

    if "{session_id}" not in path:
        raise ValueError("path must contain a {session_id} placeholder")

    target_registry = registry if registry is not None else _default_registry
    router = fastapi.APIRouter(prefix=prefix, tags=tags or ["maivn-events"])

    if auth is not None:
        dependencies = [fastapi.Depends(auth)]
    else:
        dependencies = []

    @router.get(path, dependencies=dependencies)
    async def stream_events(  # type: ignore[misc]
        session_id: str,
        last_event_id: str | None = None,
    ) -> Any:
        """Stream session events via Server-Sent Events.

        Supports the standard SSE ``Last-Event-ID`` reconnection protocol.
        Pass ``?last_event_id=...`` (or send the ``Last-Event-ID`` header
        — most browser EventSource clients do this automatically) to skip
        events the client has already seen.
        """
        bridge = get_event_bridge(
            session_id,
            create=True,
            factory=factory,
            registry=target_registry,
            audience=audience,
        )
        return EventSourceResponse(
            bridge.generate_sse(
                last_event_id=last_event_id,
                heartbeat_interval=heartbeat_interval,
            )
        )

    return router


def mount_events(
    app: FastAPI,
    *,
    prefix: str = "/maivn",
    path: str = "/events/{session_id}",
    auth: AuthHook | None = None,
    factory: BridgeFactory | None = None,
    registry: BridgeRegistry | None = None,
    audience: BridgeAudience = "frontend_safe",
    heartbeat_interval: float | None = None,
    tags: list[str] | None = None,
) -> APIRouter:
    """Mount the SSE event router onto ``app`` in a single call.

    Equivalent to ``app.include_router(create_event_router(...))`` but
    saves a line and returns the router so callers can attach extra
    routes if they want to.
    """
    router = create_event_router(
        prefix=prefix,
        path=path,
        auth=auth,
        factory=factory,
        registry=registry,
        audience=audience,
        heartbeat_interval=heartbeat_interval,
        tags=tags,
    )
    app.include_router(router)
    return router


__all__ = [
    "AuthHook",
    "BridgeFactory",
    "create_event_router",
    "get_event_bridge",
    "mount_events",
    "remove_event_bridge",
]
