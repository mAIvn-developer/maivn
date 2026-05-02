from __future__ import annotations

import os
import queue
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from maivn._internal.api.mcp import MCPServer
from maivn._internal.api.mcp.clients import _McpStdioClient


class _DummyProcess:
    stdout = object()


def test_stdio_response_timeout_raises_timeout() -> None:
    client = _McpStdioClient.__new__(_McpStdioClient)
    client._server = SimpleNamespace(stdio_response_timeout_seconds=0.0)
    client._process = _DummyProcess()
    client._stdout_queue = queue.Queue()

    with pytest.raises(TimeoutError):
        client._read_response(1)


def test_stdio_server_without_timeout_emits_warning() -> None:
    with pytest.warns(RuntimeWarning, match="no stdio_response_timeout_seconds configured"):
        server = MCPServer(
            name="demo",
            transport="stdio",
            command="python",
        )
    assert server.inherit_env is False


def test_stdio_client_build_process_env_defaults_to_minimal_runtime_env() -> None:
    client = _McpStdioClient.__new__(_McpStdioClient)
    client._server = SimpleNamespace(
        env=None,
        inherit_env=False,
        inherit_env_allowlist=None,
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
        env = client._build_process_env()

    assert env["PATH"] == "C:\\Python"
    assert env.get("SystemRoot") == "C:\\Windows" or env.get("SYSTEMROOT") == "C:\\Windows"
    assert "MAIVN_API_KEY" not in env


def test_stdio_client_build_process_env_can_explicitly_inherit_parent_env() -> None:
    client = _McpStdioClient.__new__(_McpStdioClient)
    client._server = SimpleNamespace(
        env=None,
        inherit_env=True,
        inherit_env_allowlist=None,
    )

    with patch.dict(os.environ, {"PATH": "C:\\Python", "MAIVN_API_KEY": "secret"}, clear=True):
        env = client._build_process_env()

    assert env == {"PATH": "C:\\Python", "MAIVN_API_KEY": "secret"}


def test_stdio_client_build_process_env_respects_hardening_controls() -> None:
    client = _McpStdioClient.__new__(_McpStdioClient)
    client._server = SimpleNamespace(
        env={"EXPLICIT_TOKEN": "keep"},
        inherit_env=False,
        inherit_env_allowlist=["OPENAI_API_KEY"],
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
        env = client._build_process_env()

    assert env["PATH"] == "C:\\Python"
    assert env.get("SystemRoot") == "C:\\Windows" or env.get("SYSTEMROOT") == "C:\\Windows"
    assert env["OPENAI_API_KEY"] == "allowed"
    assert env["EXPLICIT_TOKEN"] == "keep"
    assert "MAIVN_API_KEY" not in env
