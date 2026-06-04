# pyright: strict
from __future__ import annotations

import os
import queue
from collections.abc import Callable
from types import SimpleNamespace
from typing import cast, final
from unittest.mock import patch

import pytest

from maivn._internal.api.mcp import MCPServer
from maivn._internal.api.mcp.clients import MCPServerLike, McpStdioClient
from maivn._internal.api.mcp.tools import JsonObject


@final
class _DummyProcess:
    stdout: object = object()


def _new_stdio_client() -> McpStdioClient:
    """Return an ``McpStdioClient`` with ``__init__`` skipped (no subprocess)."""
    return McpStdioClient.__new__(McpStdioClient)


def _inject_server(client: McpStdioClient, server: object) -> None:
    """Assign a duck-typed ``_server`` to a freshly-allocated stdio client."""
    server_attr = "_server"
    setattr(client, server_attr, cast(MCPServerLike, server))


def _inject_process(client: McpStdioClient, process: object) -> None:
    """Assign a duck-typed ``_process`` without satisfying the ``Popen[str]`` type."""
    process_attr = "_process"
    setattr(client, process_attr, process)


def _inject_stdout_queue(client: McpStdioClient, q: queue.Queue[str]) -> None:
    stdout_queue_attr = "_stdout_queue"
    setattr(client, stdout_queue_attr, q)


def _read_response(client: McpStdioClient, request_id: int) -> JsonObject:
    read_response_attr = "_read_response"
    reader = cast(Callable[[int], JsonObject], getattr(client, read_response_attr))
    return reader(request_id)


def _build_process_env(client: McpStdioClient) -> dict[str, str]:
    build_process_env_attr = "_build_process_env"
    builder = cast(Callable[[], dict[str, str]], getattr(client, build_process_env_attr))
    return builder()


def test_stdio_response_timeout_raises_timeout() -> None:
    client = _new_stdio_client()
    # Inject duck-typed fakes; bypass the static types since we're testing internals.
    _inject_server(client, SimpleNamespace(stdio_response_timeout_seconds=0.0))
    _inject_process(client, _DummyProcess())
    _inject_stdout_queue(client, queue.Queue())

    with pytest.raises(TimeoutError):
        _ = _read_response(client, 1)


def test_stdio_server_without_timeout_emits_warning() -> None:
    with pytest.warns(RuntimeWarning, match="no stdio_response_timeout_seconds configured"):
        server = MCPServer(
            name="demo",
            transport="stdio",
            command="python",
        )
    assert server.inherit_env is False


def test_stdio_client_build_process_env_defaults_to_minimal_runtime_env() -> None:
    client = _new_stdio_client()
    _inject_server(
        client,
        SimpleNamespace(
            env=None,
            inherit_env=False,
            inherit_env_allowlist=None,
        ),
    )

    with patch.dict(
        os.environ,
        {
            "PATH": "C:\\Python",
            "SystemRoot": "C:\\Windows",
            "MAIVN_API_KEY": "secret",
        },
        clear=True,
    ):
        env = _build_process_env(client)

    assert env["PATH"] == "C:\\Python"
    assert env.get("SystemRoot") == "C:\\Windows" or env.get("SYSTEMROOT") == "C:\\Windows"
    assert "MAIVN_API_KEY" not in env


def test_stdio_client_build_process_env_can_explicitly_inherit_parent_env() -> None:
    client = _new_stdio_client()
    _inject_server(
        client,
        SimpleNamespace(
            env=None,
            inherit_env=True,
            inherit_env_allowlist=None,
        ),
    )

    with patch.dict(os.environ, {"PATH": "C:\\Python", "MAIVN_API_KEY": "secret"}, clear=True):
        env = _build_process_env(client)

    assert env == {"PATH": "C:\\Python", "MAIVN_API_KEY": "secret"}


def test_stdio_client_build_process_env_respects_hardening_controls() -> None:
    client = _new_stdio_client()
    _inject_server(
        client,
        SimpleNamespace(
            env={"EXPLICIT_TOKEN": "keep"},
            inherit_env=False,
            inherit_env_allowlist=["OPENAI_API_KEY"],
        ),
    )

    with patch.dict(
        os.environ,
        {
            "PATH": "C:\\Python",
            "SystemRoot": "C:\\Windows",
            "OPENAI_API_KEY": "allowed",
            "MAIVN_API_KEY": "blocked",
        },
        clear=True,
    ):
        env = _build_process_env(client)

    assert env["PATH"] == "C:\\Python"
    assert env.get("SystemRoot") == "C:\\Windows" or env.get("SYSTEMROOT") == "C:\\Windows"
    assert env["OPENAI_API_KEY"] == "allowed"
    assert env["EXPLICIT_TOKEN"] == "keep"
    assert "MAIVN_API_KEY" not in env
