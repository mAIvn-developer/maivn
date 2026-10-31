"""Mounting the events route keeps the host app's OpenAPI schema and /docs working."""

from __future__ import annotations

import pytest

from maivn.events import BridgeRegistry

_ = pytest.importorskip('fastapi')
_ = pytest.importorskip('sse_starlette')

from fastapi import FastAPI  # noqa: E402

from maivn.events.fastapi import mount_events  # noqa: E402


def test_mounted_events_route_does_not_break_the_openapi_schema() -> None:
    """Building the schema must not try to resolve the typing-only return annotation."""
    app = FastAPI()
    _ = mount_events(app, registry=BridgeRegistry())

    paths = app.openapi()['paths']

    assert '/maivn/events/{session_id}' in paths
    assert 'get' in paths['/maivn/events/{session_id}']
