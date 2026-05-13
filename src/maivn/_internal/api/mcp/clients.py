from __future__ import annotations

import json
import os
import queue
import subprocess
import threading
import time
from typing import TYPE_CHECKING, Any

import httpx

from .tools import (
    DEFAULT_PROTOCOL_VERSION,
    MCPToolDefinition,
)

if TYPE_CHECKING:
    from .server import MCPServer

_ESSENTIAL_PARENT_ENV_KEYS = frozenset(
    {
        "COMSPEC",
        "ComSpec",
        "HOME",
        "LANG",
        "LC_ALL",
        "PATH",
        "PATHEXT",
        "PYTHONIOENCODING",
        "SYSTEMROOT",
        "SystemRoot",
        "TEMP",
        "TMP",
        "TMPDIR",
        "USERPROFILE",
    }
)


class McpClientBase:
    """Base class for MCP client implementations.

    Provides shared ``list_tools``, ``call_tool``, and ``_ensure_initialized``
    logic.  Subclasses must implement ``_send_request`` and
    ``_send_notification`` for their transport.
    """

    _server: Any
    _protocol_version: str
    _request_id: int
    _initialized: bool

    def _send_request(
        self,
        method: str,
        *,
        params: dict[str, Any] | None = None,
        allow_uninitialized: bool = False,
    ) -> dict[str, Any]:
        raise NotImplementedError

    def _send_notification(self, method: str) -> None:
        raise NotImplementedError

    def close(self) -> None:
        return

    def list_tools(self) -> list[MCPToolDefinition]:
        self._ensure_initialized()
        tools: list[MCPToolDefinition] = []
        cursor: str | None = None

        while True:
            params = {"cursor": cursor} if cursor else {}
            response = self._send_request("tools/list", params=params)
            result = response.get("result") or {}
            raw_tools = result.get("tools") or []
            for tool in raw_tools:
                tools.append(MCPToolDefinition.model_validate(tool))
            cursor = result.get("nextCursor")
            if not cursor:
                break
        return tools

    def call_tool(self, tool_name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        self._ensure_initialized()
        payload = {"name": tool_name, "arguments": arguments}
        response = self._send_request("tools/call", params=payload)
        return response.get("result") or {}

    def _ensure_initialized(self) -> None:
        if self._initialized:
            return
        init_payload = {
            "protocolVersion": self._protocol_version,
            "capabilities": {},
            "clientInfo": {
                "name": self._server.client_name,
                "title": self._server.client_title,
                "version": self._server.client_version,
            },
        }
        response = self._send_request("initialize", params=init_payload, allow_uninitialized=True)
        result = response.get("result") or {}
        negotiated_version = result.get("protocolVersion")
        if negotiated_version:
            self._protocol_version = negotiated_version
        self._on_initialized(response)
        self._send_notification("notifications/initialized")
        self._initialized = True

    def _on_initialized(self, response: dict[str, Any]) -> None:
        """Hook called after the initialize handshake succeeds.

        Subclasses may override to extract transport-specific data from
        the initialize response (e.g. session ID for HTTP).
        """


class McpHttpClient(McpClientBase):
    """HTTP-based MCP client implementation."""

    def __init__(self, server: MCPServer) -> None:
        self._server = server
        self._client = httpx.Client(
            timeout=httpx.Timeout(server.request_timeout_seconds)
            if server.request_timeout_seconds
            else httpx.Timeout(30.0)
        )
        self._session_id: str | None = None
        self._protocol_version: str = server.protocol_version or DEFAULT_PROTOCOL_VERSION
        self._request_id = 0
        self._initialized = False

    def close(self) -> None:
        self._client.close()

    def _on_initialized(self, response: dict[str, Any]) -> None:
        self._session_id = response.get("_mcp_session_id") or self._session_id

    def _send_notification(self, method: str) -> None:
        payload = {"jsonrpc": "2.0", "method": method}
        self._post_json(payload, expect_response=False, allow_uninitialized=True)

    def _send_request(
        self,
        method: str,
        *,
        params: dict[str, Any] | None = None,
        allow_uninitialized: bool = False,
    ) -> dict[str, Any]:
        if not allow_uninitialized and not self._initialized:
            self._ensure_initialized()
        self._request_id += 1
        payload = {"jsonrpc": "2.0", "id": self._request_id, "method": method}
        if params is not None:
            payload["params"] = params
        return self._post_json(
            payload,
            expect_response=True,
            allow_uninitialized=allow_uninitialized,
        )

    def _post_json(
        self,
        payload: dict[str, Any],
        *,
        expect_response: bool,
        allow_uninitialized: bool,
    ) -> dict[str, Any]:
        _ = allow_uninitialized
        headers = {
            "Accept": "application/json, text/event-stream",
            "Content-Type": "application/json",
        }
        headers.update(self._server.headers or {})
        if self._session_id:
            headers["Mcp-Session-Id"] = self._session_id
        if self._protocol_version:
            headers["MCP-Protocol-Version"] = self._protocol_version

        response = self._client.post(self._server.url or "", headers=headers, json=payload)
        response.raise_for_status()
        if response.status_code == 202 and not response.content:
            return {}

        self._session_id = response.headers.get("Mcp-Session-Id", self._session_id)

        content_type = response.headers.get("Content-Type", "").lower()
        if "text/event-stream" in content_type:
            return self._parse_sse_response(response, payload.get("id"), expect_response)

        if not response.content:
            return {}

        data = response.json()
        if isinstance(data, dict):
            data["_mcp_session_id"] = self._session_id
            return data
        raise ValueError("Unexpected MCP response payload")

    def _parse_sse_response(
        self,
        response: httpx.Response,
        request_id: int | None,
        expect_response: bool,
    ) -> dict[str, Any]:
        data_lines: list[str] = []
        for raw_line in response.iter_lines():
            if raw_line is None:
                continue
            line = raw_line.decode() if isinstance(raw_line, bytes) else raw_line
            if not line:
                if not data_lines:
                    continue
                payload_str = "\n".join(data_lines)
                data_lines = []
                try:
                    payload = json.loads(payload_str)
                except json.JSONDecodeError:
                    continue
                if not isinstance(payload, dict):
                    continue
                if not expect_response:
                    return payload
                if payload.get("id") == request_id:
                    payload["_mcp_session_id"] = self._session_id
                    return payload
                continue

            if line.startswith("data:"):
                data_lines.append(line[len("data:") :].lstrip())

        if expect_response:
            raise ValueError("MCP SSE response ended without a matching result")
        return {}


class McpStdioClient(McpClientBase):
    """Stdio-based MCP client implementation."""

    def __init__(self, server: MCPServer) -> None:
        self._server = server
        self._process = self._spawn_process()
        self._lock = threading.Lock()
        self._request_id = 0
        self._initialized = False
        self._protocol_version: str = server.protocol_version or DEFAULT_PROTOCOL_VERSION
        self._stdout_queue: queue.Queue[str] = queue.Queue()
        self._stdout_thread = threading.Thread(target=self._drain_stdout, daemon=True)
        self._stdout_thread.start()
        self._stderr_thread = threading.Thread(target=self._drain_stderr, daemon=True)
        self._stderr_thread.start()

    def _build_process_env(self) -> dict[str, str]:
        allowlist = tuple(self._server.inherit_env_allowlist or ())
        inherited: dict[str, str]

        if self._server.inherit_env and not allowlist:
            inherited = os.environ.copy()
        else:
            allowed_names = set(_ESSENTIAL_PARENT_ENV_KEYS)
            allowed_names.update(allowlist)
            if os.name == "nt":
                allowed_names_upper = {name.upper() for name in allowed_names}
                inherited = {
                    key: value
                    for key, value in os.environ.items()
                    if key.upper() in allowed_names_upper
                }
            else:
                inherited = {
                    key: value for key, value in os.environ.items() if key in allowed_names
                }

        if self._server.env:
            inherited.update(self._server.env)
        return inherited

    def _spawn_process(self) -> subprocess.Popen[str]:
        return subprocess.Popen(
            [self._server.command or "", *self._server.args],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            bufsize=1,
            env=self._build_process_env(),
            cwd=self._server.working_dir or None,
        )

    def _drain_stderr(self) -> None:
        if not self._process.stderr:
            return
        for _ in self._process.stderr:
            continue

    def _drain_stdout(self) -> None:
        if not self._process.stdout:
            return
        try:
            for line in self._process.stdout:
                self._stdout_queue.put(line)
        finally:
            self._stdout_queue.put("")

    def close(self) -> None:
        process_exited = self._process.poll() is not None
        try:
            if self._process.stdin:
                self._process.stdin.close()
        except Exception:  # noqa: BLE001 - cleanup must never raise
            pass
        if not process_exited:
            try:
                self._process.terminate()
                self._process.wait(timeout=2)
            except Exception:  # noqa: BLE001 - escalate to SIGKILL on any failure
                try:
                    self._process.kill()
                except Exception:  # noqa: BLE001 - process may already be gone
                    pass
        try:
            if self._process.stdout:
                self._process.stdout.close()
        except Exception:  # noqa: BLE001 - cleanup must never raise
            pass
        try:
            if self._process.stderr:
                self._process.stderr.close()
        except Exception:  # noqa: BLE001 - cleanup must never raise
            pass

    def _send_notification(self, method: str) -> None:
        payload = {"jsonrpc": "2.0", "method": method}
        self._send_payload(payload)

    def _send_request(
        self,
        method: str,
        *,
        params: dict[str, Any] | None = None,
        allow_uninitialized: bool = False,
    ) -> dict[str, Any]:
        if not allow_uninitialized and not self._initialized:
            self._ensure_initialized()
        with self._lock:
            self._request_id += 1
            payload = {"jsonrpc": "2.0", "id": self._request_id, "method": method}
            if params is not None:
                payload["params"] = params
            self._send_payload(payload)
            return self._read_response(self._request_id)

    def _send_payload(self, payload: dict[str, Any]) -> None:
        if not self._process.stdin:
            raise RuntimeError("MCP stdio server has no stdin")
        serialized = json.dumps(payload, ensure_ascii=True)
        self._process.stdin.write(serialized + "\n")
        self._process.stdin.flush()

    def _read_response(self, request_id: int) -> dict[str, Any]:
        if not self._process.stdout:
            raise RuntimeError("MCP stdio server has no stdout")
        deadline = None
        timeout_seconds = self._server.stdio_response_timeout_seconds
        if timeout_seconds is not None:
            deadline = time.monotonic() + timeout_seconds
        while True:
            timeout = None
            if deadline is not None:
                timeout = max(0.0, deadline - time.monotonic())
                if timeout == 0.0:
                    raise TimeoutError("MCP stdio response timed out")
            try:
                line = self._stdout_queue.get(timeout=timeout)
            except queue.Empty as exc:
                raise TimeoutError("MCP stdio response timed out") from exc
            if not line:
                raise RuntimeError("MCP stdio server closed unexpectedly")
            try:
                payload = json.loads(line.strip())
            except json.JSONDecodeError:
                continue
            if not isinstance(payload, dict):
                continue
            if payload.get("id") == request_id:
                return payload
