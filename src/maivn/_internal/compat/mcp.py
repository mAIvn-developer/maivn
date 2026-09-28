"""V1-compatible MCP authoring adapters."""

from __future__ import annotations

import json
import os
import queue
import re
import subprocess
import threading
from collections.abc import Mapping
from dataclasses import dataclass
from typing import ClassVar, Literal, Protocol, TypeAlias, cast

import httpx
from pydantic import BaseModel, ConfigDict, Field, PrivateAttr, field_validator

JsonObject: TypeAlias = dict[str, object]

_ESSENTIAL_ENV_KEYS = frozenset(
    {
        'COMSPEC',
        'HOME',
        'LANG',
        'PATH',
        'PATHEXT',
        'PYTHONIOENCODING',
        'SYSTEMROOT',
        'TEMP',
        'TMP',
        'USERPROFILE',
    }
)
_HTTP_ACCEPTED = 202

# Argument names an MCP server may advertise that are the SDK's business, never the
# model's. Matched on the normalized name so `apiKey`, `api-key` and `API_KEY` are one
# thing. Deliberately narrow: these are names that only ever hold a credential, so
# hiding them cannot cost a tool an argument it actually needed from the model.
_CREDENTIAL_ARG_NAMES = frozenset(
    {
        'accesskey',
        'accesstoken',
        'apikey',
        'apisecret',
        'apitoken',
        'authtoken',
        'authorization',
        'bearertoken',
        'clientsecret',
        'credential',
        'credentials',
        'password',
        'privatekey',
        'refreshtoken',
        'secret',
        'secretkey',
        'sessiontoken',
        'token',
    }
)
_ARG_NAME_SEPARATORS = re.compile(r'[-_\s]')


def _is_credential_arg_name(name: str) -> bool:
    """Return True when an argument name only ever holds a credential."""
    return _ARG_NAME_SEPARATORS.sub('', name).lower() in _CREDENTIAL_ARG_NAMES


def hide_tool_args_from_model(schema: JsonObject, hidden: frozenset[str]) -> JsonObject:
    """Return the schema with hidden arguments removed from properties and required.

    A hidden argument left in `required` advertises a contract the model cannot
    satisfy, which is worse than advertising it at all - so both have to go.
    """
    if not hidden:
        return schema
    properties = schema.get('properties')
    if not isinstance(properties, Mapping):
        return schema
    visible = {
        name: value
        for name, value in cast('Mapping[str, object]', properties).items()
        if name not in hidden
    }
    trimmed: JsonObject = {**schema, 'properties': visible}
    required = schema.get('required')
    if isinstance(required, list):
        trimmed['required'] = [
            name for name in cast('list[object]', required) if name not in hidden
        ]
    return trimmed


class MCPRuntimeError(RuntimeError):
    """Raised when an MCP peer returns an error response."""


class _MCPRuntime(Protocol):
    def list_tools(self) -> list[JsonObject]: ...

    def call_tool(self, name: str, arguments: JsonObject) -> JsonObject: ...

    def close(self) -> None: ...


class _MCPRuntimeBase:
    """Shared synchronous MCP initialization and request projection."""

    def __init__(self, server: MCPServer) -> None:
        self._server = server
        self._request_id = 0
        self._initialized = False

    def list_tools(self) -> list[JsonObject]:
        self._initialize()
        result = self._request('tools/list', {})
        tools = result.get('tools')
        if not isinstance(tools, list):
            message = 'MCP tools/list response did not contain a tools array'
            raise TypeError(message)
        return [
            cast('JsonObject', item)
            for item in cast('list[object]', tools)
            if isinstance(item, dict)
        ]

    def call_tool(self, name: str, arguments: JsonObject) -> JsonObject:
        self._initialize()
        return self._request('tools/call', {'name': name, 'arguments': arguments})

    def _initialize(self) -> None:
        if self._initialized:
            return
        _ = self._request(
            'initialize',
            {
                'protocolVersion': self._server.protocol_version,
                'capabilities': {},
                'clientInfo': {
                    'name': self._server.client_name,
                    'title': self._server.client_title,
                    'version': self._server.client_version,
                },
            },
        )
        self._notify('notifications/initialized', {})
        self._initialized = True

    def _next_request(self, method: str, params: JsonObject) -> JsonObject:
        self._request_id += 1
        return {
            'jsonrpc': '2.0',
            'id': self._request_id,
            'method': method,
            'params': params,
        }

    def _request(self, method: str, params: JsonObject) -> JsonObject:
        raise NotImplementedError

    def _notify(self, method: str, params: JsonObject) -> None:
        raise NotImplementedError

    def close(self) -> None:
        return


def _response_result(response: object) -> JsonObject:
    if not isinstance(response, dict):
        message = 'MCP server returned a non-object response'
        raise TypeError(message)
    payload = cast('JsonObject', response)
    error = payload.get('error')
    if isinstance(error, dict):
        raw_message = cast('JsonObject', error).get('message')
        message = raw_message if isinstance(raw_message, str) else 'MCP request failed'
        raise MCPRuntimeError(message)
    result = payload.get('result')
    if not isinstance(result, dict):
        message = 'MCP response did not contain an object result'
        raise TypeError(message)
    return cast('JsonObject', result)


class _StdioMCPRuntime(_MCPRuntimeBase):
    def __init__(self, server: MCPServer) -> None:
        super().__init__(server)
        command, args = _stdio_command(server)
        self._process = subprocess.Popen(  # noqa: S603 - developer-configured MCP command.
            [command, *args],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            text=True,
            encoding='utf-8',
            bufsize=1,
            cwd=server.working_dir,
            env=_stdio_environment(server),
        )
        self._responses: queue.Queue[str] = queue.Queue()
        self._reader = threading.Thread(target=self._drain_stdout, daemon=True)
        self._reader.start()
        self._lock = threading.Lock()

    def _drain_stdout(self) -> None:
        stdout = self._process.stdout
        if stdout is None:
            self._responses.put('')
            return
        for line in stdout:
            self._responses.put(line)
        self._responses.put('')

    def _request(self, method: str, params: JsonObject) -> JsonObject:
        with self._lock:
            payload = self._next_request(method, params)
            self._send(payload)
            request_id = payload['id']
            while True:
                try:
                    line = self._responses.get(timeout=self._timeout_seconds())
                except queue.Empty as exc:
                    message = f'MCP stdio request timed out: {method}'
                    raise TimeoutError(message) from exc
                if not line:
                    message = 'MCP stdio server closed unexpectedly'
                    raise RuntimeError(message)
                try:
                    decoded: object = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if not isinstance(decoded, dict):
                    continue
                response = cast('JsonObject', decoded)
                if response.get('id') == request_id:
                    return _response_result(response)

    def _notify(self, method: str, params: JsonObject) -> None:
        self._send({'jsonrpc': '2.0', 'method': method, 'params': params})

    def _send(self, payload: JsonObject) -> None:
        stdin = self._process.stdin
        if stdin is None:
            message = 'MCP stdio server has no stdin'
            raise RuntimeError(message)
        _ = stdin.write(json.dumps(payload, ensure_ascii=True) + '\n')
        stdin.flush()

    def _timeout_seconds(self) -> float | None:
        return self._server.stdio_response_timeout_seconds

    def close(self) -> None:
        if self._process.poll() is not None:
            return
        self._process.terminate()
        try:
            _ = self._process.wait(timeout=2)
        except subprocess.TimeoutExpired:
            self._process.kill()
            _ = self._process.wait(timeout=2)


class _HttpMCPRuntime(_MCPRuntimeBase):
    def __init__(self, server: MCPServer) -> None:
        super().__init__(server)
        self._client = httpx.Client(timeout=server.request_timeout_seconds or 30.0)
        self._session_id: str | None = None

    def _request(self, method: str, params: JsonObject) -> JsonObject:
        payload = self._next_request(method, params)
        response = self._post(payload)
        return _response_result(response)

    def _notify(self, method: str, params: JsonObject) -> None:
        _ = self._post({'jsonrpc': '2.0', 'method': method, 'params': params})

    def _post(self, payload: JsonObject) -> object:
        headers = {
            'Accept': 'application/json, text/event-stream',
            'Content-Type': 'application/json',
            **(self._server.headers or {}),
        }
        if self._session_id is not None:
            headers['Mcp-Session-Id'] = self._session_id
        response = self._client.post(self._server.url or '', headers=headers, json=payload)
        response.raise_for_status()
        self._session_id = response.headers.get('Mcp-Session-Id', self._session_id)
        if response.status_code == _HTTP_ACCEPTED or not response.content:
            return {}
        if 'text/event-stream' in response.headers.get('Content-Type', '').lower():
            return _last_sse_payload(response.text)
        return response.json()

    def close(self) -> None:
        self._client.close()


def _last_sse_payload(body: str) -> object:
    for block in reversed(body.split('\n\n')):
        data = '\n'.join(
            line.removeprefix('data:').lstrip()
            for line in block.splitlines()
            if line.startswith('data:')
        )
        if data:
            return json.loads(data)
    return {}


def _stdio_command(server: MCPServer) -> tuple[str, list[str]]:
    if server.command:
        return server.command, list(server.args)
    if server.auto_setup is not None:
        return server.auto_setup.resolve_command()
    message = 'stdio MCPServer requires command or auto_setup'
    raise ValueError(message)


def _stdio_environment(server: MCPServer) -> dict[str, str]:
    allowlist = set(server.inherit_env_allowlist or ())
    if server.inherit_env and not allowlist:
        inherited = os.environ.copy()
    else:
        allowed = {key.upper() for key in (*_ESSENTIAL_ENV_KEYS, *allowlist)}
        inherited = {key: value for key, value in os.environ.items() if key.upper() in allowed}
    if server.auto_setup is not None and server.auto_setup.env:
        inherited.update(server.auto_setup.env)
    if server.env:
        inherited.update(server.env)
    return inherited


class MCPAutoSetup(BaseModel):
    """Auto-setup instructions for local stdio MCP servers."""

    model_config: ClassVar[ConfigDict] = ConfigDict(populate_by_name=True)

    provider: Literal['uvx'] = 'uvx'
    package: str
    args: list[str] = Field(default_factory=list)
    # Extra requirement specs installed beside the package (uvx --with). The
    # sanctioned place to pin a server's floating dependency when an upstream
    # release breaks it -- e.g. mcp-server-fetch against the mcp 2.x rename.
    with_packages: list[str] = Field(default_factory=list)
    env: dict[str, str] | None = None
    working_dir: str | None = None
    uvx_command: str | None = None

    @field_validator('package')
    @classmethod
    def _validate_package(cls, value: str) -> str:
        if not value:
            message = 'auto_setup.package must be a non-empty string'
            raise ValueError(message)
        return value

    def resolve_command(self) -> tuple[str, list[str]]:
        """Resolve the command and arguments for running the MCP server."""
        command = self.uvx_command or 'uvx'
        with_args = [arg for spec in self.with_packages for arg in ('--with', spec)]
        return command, [*with_args, self.package, *self.args]


class MCPSoftErrorHandling(BaseModel):
    """Configuration for retrying MCP tools that signal soft failures."""

    model_config: ClassVar[ConfigDict] = ConfigDict(populate_by_name=True)

    enabled: bool = False
    keys: list[str] = Field(default_factory=lambda: ['Note', 'Information', 'Error Message'])
    max_retries: int = 1
    initial_backoff_seconds: float = 5.0
    max_backoff_seconds: float = 60.0

    @field_validator('max_retries')
    @classmethod
    def _validate_max_retries(cls, value: int) -> int:
        if value < 0:
            message = 'max_retries must be >= 0'
            raise ValueError(message)
        return value


@dataclass(frozen=True)
class ToolOverride:
    """Per-tool configuration applied at registration time."""

    name: str | None = None
    description: str | None = None
    tags: tuple[str, ...] | list[str] | None = None
    metadata: dict[str, object] | None = None
    always_execute: bool | None = None
    final_tool: bool | None = None
    default_args: dict[str, object] | None = None
    dependencies: tuple[object, ...] | list[object] | None = None
    output_schema: object | None = None
    before_execute: object | None = None
    after_execute: object | None = None

    def is_empty(self) -> bool:
        """Return True when no override field is set."""
        return (
            self.name is None
            and self.description is None
            and not self.tags
            and not self.metadata
            and self.always_execute is None
            and self.final_tool is None
            and not self.default_args
            and not self.dependencies
            and self.output_schema is None
            and self.before_execute is None
            and self.after_execute is None
        )


class MCPServer(BaseModel):
    """MCP server registration metadata."""

    model_config: ClassVar[ConfigDict] = ConfigDict(
        arbitrary_types_allowed=True,
        populate_by_name=True,
    )

    name: str
    transport: Literal['http', 'stdio'] = 'stdio'
    url: str | None = None
    command: str | None = None
    args: list[str] = Field(default_factory=list)
    env: dict[str, str] | None = None
    inherit_env: bool = False
    inherit_env_allowlist: list[str] | None = None
    working_dir: str | None = None
    headers: dict[str, str] | None = None
    protocol_version: str = '2024-11-05'
    client_name: str = 'maivn'
    client_title: str = 'mAIvn'
    client_version: str = '0.1.0'
    tool_name_prefix: str | None = None
    tool_name_separator: str = '__'
    default_tool_args: JsonObject | None = None
    tool_defaults: dict[str, JsonObject] | None = None
    injected_tool_args: JsonObject | None = None
    model_visible_tool_args: list[str] | None = None
    tool_overrides: dict[str, ToolOverride] | None = None
    max_calls_per_minute: int | None = None
    max_calls_per_day: int | None = None
    request_timeout_seconds: float | None = None
    stdio_response_timeout_seconds: float | None = None
    raise_on_tool_error: bool = False
    auto_setup: MCPAutoSetup | None = None
    soft_error_handling: MCPSoftErrorHandling | None = None
    _runtime: _MCPRuntime | None = PrivateAttr(default=None)

    @field_validator('name', 'tool_name_separator')
    @classmethod
    def _validate_non_empty(cls, value: str) -> str:
        if not value:
            message = 'MCPServer name and tool_name_separator must be non-empty'
            raise ValueError(message)
        return value

    def build_tool_name(self, mcp_tool_name: str) -> str:
        """Build a prefixed public tool name."""
        prefix = self.tool_name_prefix if self.tool_name_prefix is not None else self.name
        raw = f'{prefix}{self.tool_name_separator}{mcp_tool_name}' if prefix else mcp_tool_name
        return raw.replace('-', '_').replace(' ', '_')

    def resolve_tool_defaults(self, mcp_tool_name: str) -> JsonObject:
        """Resolve default arguments for a tool."""
        defaults: JsonObject = {}
        if self.default_tool_args is not None:
            defaults.update(self.default_tool_args)
        if self.tool_defaults is not None:
            defaults.update(self.tool_defaults.get(mcp_tool_name, {}))
        return defaults

    def resolve_injected_tool_args(self, mcp_tool_name: str) -> JsonObject:
        """Resolve arguments the SDK supplies itself, which the model never authors."""
        injected: JsonObject = {}
        if self.injected_tool_args is not None:
            injected.update(self.injected_tool_args)
        for name, value in self.resolve_tool_defaults(mcp_tool_name).items():
            injected.setdefault(name, value)
        return injected

    def model_hidden_arg_names(self, mcp_tool_name: str, schema: JsonObject) -> frozenset[str]:
        """Return the schema's argument names the model must never be shown.

        A credential is not a decision. When a server advertises one as an ordinary
        argument the model has to guess, and a model that guesses "I have no key"
        does not call the tool at all - it reports the credential as missing and
        returns nothing, which is how a correctly configured mcp-auto-setup failed
        three runs in four. Hiding the argument removes the guess.

        `model_visible_tool_args` is the escape hatch for the rare server that
        genuinely wants the model to choose a key; `injected_tool_args` extends the
        set to arguments no name-based rule could infer, such as a tenant id.
        """
        properties = schema.get('properties')
        if not isinstance(properties, Mapping):
            return frozenset()
        forced_visible = set(self.model_visible_tool_args or ())
        explicitly_injected = set(self.resolve_injected_tool_args(mcp_tool_name))
        hidden = {
            name
            for name in cast('Mapping[str, object]', properties)
            if name not in forced_visible
            and (name in explicitly_injected or _is_credential_arg_name(name))
        }
        return frozenset(hidden)

    def resolve_tool_override(self, mcp_tool_name: str) -> ToolOverride | None:
        """Resolve a registration override for a raw MCP tool name."""
        if self.tool_overrides is None:
            return None
        return self.tool_overrides.get(mcp_tool_name)

    def list_tools(self) -> list[JsonObject]:
        """Discover tools from the configured MCP transport."""
        return self._get_runtime().list_tools()

    def call_tool(self, tool_name: str, arguments: JsonObject) -> JsonObject:
        """Execute one tool through the configured MCP transport."""
        return self._get_runtime().call_tool(tool_name, arguments)

    def close(self) -> None:
        """Close compatibility server resources."""
        runtime = self._runtime
        self._runtime = None
        if runtime is not None:
            runtime.close()

    def _get_runtime(self) -> _MCPRuntime:
        runtime = self._runtime
        if runtime is None:
            if self.transport == 'http':
                if not self.url:
                    message = 'http MCPServer requires url'
                    raise ValueError(message)
                runtime = _HttpMCPRuntime(self)
            else:
                runtime = _StdioMCPRuntime(self)
            self._runtime = runtime
        return runtime


__all__ = [
    'JsonObject',
    'MCPAutoSetup',
    'MCPServer',
    'MCPSoftErrorHandling',
    'ToolOverride',
]
