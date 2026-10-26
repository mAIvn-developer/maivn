"""Characterization tests for the v1-compatible MCP adapter.

Stdio servers are tiny local Python scripts launched with ``sys.executable``;
HTTP servers are a loopback ``http.server`` on 127.0.0.1. Nothing reaches the
network and no ``npx``/``uvx`` package is ever launched.
"""

from __future__ import annotations

import json
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from textwrap import dedent
from typing import TYPE_CHECKING, ClassVar, cast

import httpx
import pytest
from pydantic import ValidationError

from maivn import MCPAutoSetup, MCPServer, MCPSoftErrorHandling, ToolOverride
from maivn._internal.compat.mcp import hide_tool_args_from_model

if TYPE_CHECKING:
    from collections.abc import Callable, Iterator
    from pathlib import Path

JsonObject = dict[str, object]

_STDIO_SERVER = """
import json
import os
import sys

mode = sys.argv[1]
for line in sys.stdin:
    request = json.loads(line)
    if 'id' not in request:
        continue
    method = request['method']
    if mode == 'exit':
        sys.exit(0)
    if mode == 'silent':
        continue
    if mode == 'noisy':
        print('not json', flush=True)
        print('[1, 2]', flush=True)
        print(json.dumps({'jsonrpc': '2.0', 'id': 999, 'result': {}}), flush=True)
    if method == 'initialize':
        result = {'protocolVersion': request['params']['protocolVersion']}
    elif method == 'tools/list':
        result = {'tools': [{'name': 'env'}, 'skip-me', {'name': 'error'}]}
    elif request['params']['name'] == 'error':
        error = {'jsonrpc': '2.0', 'id': request['id'], 'error': {'code': -1}}
        print(json.dumps(error), flush=True)
        continue
    else:
        names = request['params']['arguments']['names']
        result = {'env': {name: os.environ.get(name) for name in names}, 'cwd': os.getcwd()}
    print(json.dumps({'jsonrpc': '2.0', 'id': request['id'], 'result': result}), flush=True)
"""


@pytest.fixture
def stdio_script(tmp_path: Path) -> str:
    """Write the scripted stdio MCP server and return its path."""
    script = tmp_path / 'mcp_server.py'
    _ = script.write_text(dedent(_STDIO_SERVER), encoding='utf-8')
    return str(script)


def _stdio(script: str, mode: str = 'ok', **kwargs: object) -> MCPServer:
    return MCPServer.model_validate(
        {
            'name': 'local',
            'transport': 'stdio',
            'command': sys.executable,
            'args': [script, mode],
            'stdio_response_timeout_seconds': 10,
            **kwargs,
        },
    )


class _Recorder:
    """Scripted loopback MCP HTTP peer that records every request it receives."""

    def __init__(self) -> None:
        self.requests: list[tuple[dict[str, str], JsonObject]] = []
        self.reply: Callable[[JsonObject], tuple[int, dict[str, str], bytes]] = _json_reply


def _json_reply(payload: JsonObject) -> tuple[int, dict[str, str], bytes]:
    if 'id' not in payload:
        return 202, {}, b''
    method = payload['method']
    result: JsonObject = {'tools': [{'name': 'echo'}]} if method == 'tools/list' else {'ok': method}
    body = json.dumps({'jsonrpc': '2.0', 'id': payload['id'], 'result': result}).encode()
    return 200, {'Content-Type': 'application/json', 'Mcp-Session-Id': 'sess-1'}, body


@pytest.fixture
def http_peer() -> Iterator[tuple[str, _Recorder]]:
    """Serve a scripted MCP endpoint on loopback and yield its URL and recorder."""
    recorder = _Recorder()

    class Handler(BaseHTTPRequestHandler):
        peer: ClassVar[_Recorder] = recorder

        def do_POST(self) -> None:
            length = int(self.headers.get('Content-Length', '0'))
            payload = cast('JsonObject', json.loads(self.rfile.read(length)))
            self.peer.requests.append(({k.lower(): v for k, v in self.headers.items()}, payload))
            status, headers, body = self.peer.reply(payload)
            self.send_response(status)
            for key, value in headers.items():
                self.send_header(key, value)
            self.send_header('Content-Length', str(len(body)))
            self.end_headers()
            _ = self.wfile.write(body)

        def log_message(self, format: str, *args: object) -> None:  # noqa: A002, ARG002
            return

    server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
    thread = threading.Thread(
        target=server.serve_forever,
        kwargs={'poll_interval': 0.01},
        daemon=True,
    )
    thread.start()
    try:
        yield f'http://127.0.0.1:{server.server_address[1]}/mcp', recorder
    finally:
        server.shutdown()
        server.server_close()


def _http(url: str, **kwargs: object) -> MCPServer:
    return MCPServer.model_validate({'name': 'remote', 'transport': 'http', 'url': url, **kwargs})


def test_http_runtime_initializes_once_and_carries_the_session_id(
    http_peer: tuple[str, _Recorder],
) -> None:
    """Initialize, notify, then reuse the session id and custom headers on later calls."""
    url, recorder = http_peer
    server = _http(url, headers={'X-Team': 'blue'}, client_version='9.9')
    try:
        tools = server.list_tools()
        result = server.call_tool('echo', {'text': 'hi'})
    finally:
        server.close()
    assert tools == [{'name': 'echo'}]
    assert result == {'ok': 'tools/call'}
    payloads = [payload for _headers, payload in recorder.requests]
    assert payloads == [
        {
            'jsonrpc': '2.0',
            'id': 1,
            'method': 'initialize',
            'params': {
                'protocolVersion': '2024-11-05',
                'capabilities': {},
                'clientInfo': {'name': 'maivn', 'title': 'mAIvn', 'version': '9.9'},
            },
        },
        {'jsonrpc': '2.0', 'method': 'notifications/initialized', 'params': {}},
        {'jsonrpc': '2.0', 'id': 2, 'method': 'tools/list', 'params': {}},
        {
            'jsonrpc': '2.0',
            'id': 3,
            'method': 'tools/call',
            'params': {'name': 'echo', 'arguments': {'text': 'hi'}},
        },
    ]
    first_headers = recorder.requests[0][0]
    assert first_headers['accept'] == 'application/json, text/event-stream'
    assert first_headers['content-type'] == 'application/json'
    assert first_headers['x-team'] == 'blue'
    assert 'mcp-session-id' not in first_headers
    assert [headers.get('mcp-session-id') for headers, _ in recorder.requests[1:]] == [
        'sess-1',
        'sess-1',
        'sess-1',
    ]


def test_http_runtime_reads_the_last_sse_data_block(http_peer: tuple[str, _Recorder]) -> None:
    """Event-stream replies resolve to the last block that carries data lines."""
    url, recorder = http_peer

    def reply(payload: JsonObject) -> tuple[int, dict[str, str], bytes]:
        if 'id' not in payload:
            return 202, {}, b''
        if payload['method'] == 'initialize':
            return 200, {'Content-Type': 'application/json'}, b'{"id": 1, "result": {}}'
        first = json.dumps({'id': payload['id'], 'result': {'n': 1}})
        last = json.dumps({'id': payload['id'], 'result': {'tools': [{'name': 'b'}]}})
        body = f'data: {first}\n\nevent: message\ndata:{last}\n\n: comment only\n\n'
        return 200, {'Content-Type': 'Text/Event-Stream; charset=utf-8'}, body.encode()

    recorder.reply = reply
    server = _http(url)
    try:
        assert server.list_tools() == [{'name': 'b'}]
    finally:
        server.close()


def test_http_runtime_treats_an_empty_body_as_an_empty_result(
    http_peer: tuple[str, _Recorder],
) -> None:
    """An empty 200 reply is an empty object, which carries no result."""
    url, recorder = http_peer
    recorder.reply = lambda _payload: (200, {}, b'')
    server = _http(url)
    with pytest.raises(TypeError, match=r'^MCP response did not contain an object result$'):
        _ = server.call_tool('x', {})
    server.close()


def test_http_runtime_with_an_sse_body_without_data_has_no_result(
    http_peer: tuple[str, _Recorder],
) -> None:
    """An event stream with no data lines yields no result."""
    url, recorder = http_peer
    recorder.reply = lambda _payload: (200, {'Content-Type': 'text/event-stream'}, b': ping\n\n')
    server = _http(url)
    with pytest.raises(TypeError, match=r'^MCP response did not contain an object result$'):
        _ = server.list_tools()
    server.close()


@pytest.mark.parametrize(
    ('body', 'error', 'message'),
    [
        (b'{"error": {"message": "tool exploded"}}', 'runtime', 'tool exploded'),
        (b'{"error": {"code": 5}}', 'runtime', 'MCP request failed'),
        (b'[1, 2]', 'type', 'MCP server returned a non-object response'),
        (b'{"result": [1]}', 'type', 'MCP response did not contain an object result'),
    ],
)
def test_http_runtime_projects_peer_errors(
    http_peer: tuple[str, _Recorder],
    body: bytes,
    error: str,
    message: str,
) -> None:
    """Error objects raise MCPRuntimeError; malformed replies raise TypeError."""
    url, recorder = http_peer
    recorder.reply = lambda _payload: (200, {'Content-Type': 'application/json'}, body)
    server = _http(url)
    expected: type[Exception] = RuntimeError if error == 'runtime' else TypeError
    with pytest.raises(expected) as excinfo:
        _ = server.list_tools()
    server.close()
    assert str(excinfo.value) == message
    assert (type(excinfo.value).__name__ == 'MCPRuntimeError') is (error == 'runtime')


def test_http_runtime_refuses_a_tools_list_without_an_array(
    http_peer: tuple[str, _Recorder],
) -> None:
    """A tools/list result without a tools array is a type error."""
    url, recorder = http_peer
    recorder.reply = lambda payload: (
        (202, {}, b'')
        if 'id' not in payload
        else (200, {'Content-Type': 'application/json'}, b'{"result": {"tools": "none"}}')
    )
    server = _http(url)
    with pytest.raises(TypeError, match=r'^MCP tools/list response did not contain a tools array$'):
        _ = server.list_tools()
    server.close()


def test_http_runtime_raises_on_http_errors(http_peer: tuple[str, _Recorder]) -> None:
    """Non-2xx replies raise httpx status errors."""
    url, recorder = http_peer
    recorder.reply = lambda _payload: (500, {}, b'boom')
    server = _http(url)
    with pytest.raises(httpx.HTTPStatusError):
        _ = server.list_tools()
    server.close()


def test_http_server_requires_a_url() -> None:
    """An http transport without a url is refused at first use."""
    server = MCPServer(name='remote', transport='http')
    with pytest.raises(ValueError, match=r'^http MCPServer requires url$'):
        _ = server.list_tools()
    server.close()


def test_stdio_runtime_skips_noise_and_filters_non_object_tools(stdio_script: str) -> None:
    """Unparseable lines, non-objects and other ids are skipped; non-dict tools dropped."""
    server = _stdio(stdio_script, 'noisy')
    try:
        tools = server.list_tools()
    finally:
        server.close()
    assert tools == [{'name': 'env'}, {'name': 'error'}]


def test_stdio_runtime_projects_error_without_message(stdio_script: str) -> None:
    """A stdio error object without a message uses the generic failure text."""
    server = _stdio(stdio_script)
    try:
        with pytest.raises(RuntimeError, match=r'^MCP request failed$'):
            _ = server.call_tool('error', {})
    finally:
        server.close()


def test_stdio_runtime_reports_a_closed_server(stdio_script: str) -> None:
    """A server that exits before answering is reported as closed."""
    server = _stdio(stdio_script, 'exit')
    try:
        with pytest.raises(RuntimeError, match=r'^MCP stdio server closed unexpectedly$'):
            _ = server.list_tools()
    finally:
        server.close()


def test_stdio_runtime_times_out_on_a_silent_server(stdio_script: str) -> None:
    """A server that never answers raises TimeoutError naming the method."""
    server = _stdio(stdio_script, 'silent', stdio_response_timeout_seconds=0.1)
    try:
        with pytest.raises(TimeoutError, match=r'^MCP stdio request timed out: initialize$'):
            _ = server.list_tools()
    finally:
        server.close()
    server.close()


def test_stdio_environment_is_an_allowlist_by_default(
    stdio_script: str,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Only essential, allowlisted, auto-setup and explicit variables reach the server."""
    monkeypatch.setenv('UNITA_PROBE_SECRET', 'leak')
    monkeypatch.setenv('UNITA_PROBE_ALLOWED', 'shared')
    server = MCPServer(
        name='local',
        auto_setup=MCPAutoSetup(
            package=stdio_script,
            args=['ok'],
            uvx_command=sys.executable,
            env={'UNITA_FROM_SETUP': 'setup', 'UNITA_OVERRIDDEN': 'setup'},
        ),
        env={'UNITA_OVERRIDDEN': 'server'},
        inherit_env_allowlist=['unita_probe_allowed'],
        working_dir=str(tmp_path),
        stdio_response_timeout_seconds=10,
    )
    names = [
        'UNITA_PROBE_SECRET',
        'UNITA_PROBE_ALLOWED',
        'UNITA_FROM_SETUP',
        'UNITA_OVERRIDDEN',
    ]
    try:
        result = server.call_tool('env', {'names': names})
    finally:
        server.close()
    assert result['env'] == {
        'UNITA_PROBE_SECRET': None,
        'UNITA_PROBE_ALLOWED': 'shared',
        'UNITA_FROM_SETUP': 'setup',
        'UNITA_OVERRIDDEN': 'server',
    }
    assert str(result['cwd']).lower() == str(tmp_path).lower()


def test_stdio_inherit_env_passes_the_whole_environment(
    stdio_script: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``inherit_env`` without an allowlist copies every variable."""
    monkeypatch.setenv('UNITA_PROBE_SECRET', 'visible')
    server = _stdio(stdio_script, inherit_env=True)
    try:
        result = server.call_tool('env', {'names': ['UNITA_PROBE_SECRET']})
    finally:
        server.close()
    assert result['env'] == {'UNITA_PROBE_SECRET': 'visible'}


def test_stdio_inherit_env_with_allowlist_still_filters(
    stdio_script: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An allowlist narrows ``inherit_env`` back to allowlisted variables."""
    monkeypatch.setenv('UNITA_PROBE_SECRET', 'leak')
    monkeypatch.setenv('UNITA_PROBE_ALLOWED', 'shared')
    server = _stdio(stdio_script, inherit_env=True, inherit_env_allowlist=['UNITA_PROBE_ALLOWED'])
    try:
        result = server.call_tool('env', {'names': ['UNITA_PROBE_SECRET', 'UNITA_PROBE_ALLOWED']})
    finally:
        server.close()
    assert result['env'] == {'UNITA_PROBE_SECRET': None, 'UNITA_PROBE_ALLOWED': 'shared'}


def test_stdio_server_requires_a_command_or_auto_setup() -> None:
    """A stdio server with nothing to launch is refused."""
    server = MCPServer(name='local')
    with pytest.raises(ValueError, match=r'^stdio MCPServer requires command or auto_setup$'):
        _ = server.list_tools()


def test_close_without_a_runtime_is_a_no_op() -> None:
    """Closing a server that never started does nothing."""
    server = MCPServer(name='local')
    server.close()
    server.close()


@pytest.mark.parametrize(
    ('kwargs', 'raw', 'expected'),
    [
        ({}, 'get-quote', 'srv__get_quote'),
        ({'tool_name_prefix': ''}, 'get quote', 'get_quote'),
        ({'tool_name_prefix': 'av', 'tool_name_separator': '-'}, 'q', 'av_q'),
        ({'tool_name_prefix': 'my team'}, 'q', 'my_team__q'),
    ],
)
def test_build_tool_name_prefixes_and_sanitizes(
    kwargs: dict[str, str],
    raw: str,
    expected: str,
) -> None:
    """Names default to the server name prefix; dashes and spaces become underscores."""
    assert MCPServer(name='srv', **kwargs).build_tool_name(raw) == expected  # pyright: ignore[reportArgumentType]


@pytest.mark.parametrize('field', ['name', 'tool_name_separator'])
def test_server_name_and_separator_must_be_non_empty(field: str) -> None:
    """Empty names or separators are refused."""
    payload: dict[str, str] = {'name': 'srv', field: ''}
    with pytest.raises(ValidationError) as excinfo:
        _ = MCPServer.model_validate(payload)
    errors = excinfo.value.errors()
    assert [(error['loc'], error['msg']) for error in errors] == [
        ((field,), 'Value error, MCPServer name and tool_name_separator must be non-empty'),
    ]


def test_tool_defaults_and_injected_args_layer_in_order() -> None:
    """Per-tool defaults override server defaults; injected args win over both."""
    server = MCPServer(
        name='srv',
        default_tool_args={'region': 'us', 'limit': 5},
        tool_defaults={'quote': {'limit': 10}},
        injected_tool_args={'region': 'eu', 'tenant': 't-1'},
    )
    assert server.resolve_tool_defaults('quote') == {'region': 'us', 'limit': 10}
    assert server.resolve_tool_defaults('other') == {'region': 'us', 'limit': 5}
    assert server.resolve_injected_tool_args('quote') == {
        'region': 'eu',
        'tenant': 't-1',
        'limit': 10,
    }
    assert MCPServer(name='srv').resolve_tool_defaults('x') == {}
    assert MCPServer(name='srv').resolve_injected_tool_args('x') == {}


def test_model_hidden_arg_names_combines_credentials_and_injected_args() -> None:
    """Credential-shaped and injected names are hidden unless forced visible."""
    server = MCPServer(
        name='srv',
        injected_tool_args={'tenant': 't-1'},
        model_visible_tool_args=['token'],
    )
    schema: JsonObject = {
        'properties': {
            'API-Key': {},
            'client secret': {},
            'token': {},
            'tenant': {},
            'symbol': {},
            'api_key_hint': {},
        },
    }
    assert server.model_hidden_arg_names('q', schema) == frozenset(
        {'API-Key', 'client secret', 'tenant'},
    )
    assert server.model_hidden_arg_names('q', {'properties': ['api_key']}) == frozenset()


def test_hide_tool_args_from_model_trims_properties_and_required() -> None:
    """Hidden names leave both properties and required; other keys survive."""
    schema: JsonObject = {
        'type': 'object',
        'properties': {'api_key': {}, 'symbol': {}},
        'required': ['api_key', 'symbol'],
    }
    assert hide_tool_args_from_model(schema, frozenset({'api_key'})) == {
        'type': 'object',
        'properties': {'symbol': {}},
        'required': ['symbol'],
    }
    assert schema['properties'] == {'api_key': {}, 'symbol': {}}
    no_required: JsonObject = {'properties': {'api_key': {}}, 'required': 'api_key'}
    assert hide_tool_args_from_model(no_required, frozenset({'api_key'})) == {
        'properties': {},
        'required': 'api_key',
    }


def test_hide_tool_args_from_model_leaves_unusable_schemas_alone() -> None:
    """Nothing hidden or no properties mapping returns the same schema object."""
    schema: JsonObject = {'properties': {'a': {}}}
    assert hide_tool_args_from_model(schema, frozenset()) is schema
    odd: JsonObject = {'properties': ['a']}
    assert hide_tool_args_from_model(odd, frozenset({'a'})) is odd


def test_tool_override_lookup_and_emptiness() -> None:
    """Overrides are looked up by raw tool name; empty overrides report empty."""
    override = ToolOverride(description='Quote a ticker')
    server = MCPServer(name='srv', tool_overrides={'quote': override})
    assert server.resolve_tool_override('quote') is override
    assert server.resolve_tool_override('other') is None
    assert MCPServer(name='srv').resolve_tool_override('quote') is None
    assert ToolOverride().is_empty() is True
    assert ToolOverride(tags=[], metadata={}, default_args={}, dependencies=()).is_empty() is True
    assert override.is_empty() is False


@pytest.mark.parametrize(
    'override',
    [
        ToolOverride(name='n'),
        ToolOverride(tags=('t',)),
        ToolOverride(metadata={'k': 1}),
        ToolOverride(always_execute=False),
        ToolOverride(final_tool=False),
        ToolOverride(default_args={'a': 1}),
        ToolOverride(dependencies=('d',)),
        ToolOverride(output_schema={}),
        ToolOverride(before_execute=print),
        ToolOverride(after_execute=print),
    ],
)
def test_any_set_override_field_makes_it_non_empty(override: ToolOverride) -> None:
    """Each override field on its own counts as configuration."""
    assert override.is_empty() is False


def test_auto_setup_refuses_an_empty_package_and_honours_a_custom_command() -> None:
    """The package must be non-empty; ``uvx_command`` replaces ``uvx``."""
    with pytest.raises(ValidationError) as excinfo:
        _ = MCPAutoSetup(package='')
    assert [(e['loc'], e['msg']) for e in excinfo.value.errors()] == [
        (('package',), 'Value error, auto_setup.package must be a non-empty string'),
    ]
    setup = MCPAutoSetup(package='pkg', uvx_command='/opt/uvx', args=['--x'])
    assert setup.resolve_command() == ('/opt/uvx', ['pkg', '--x'])


def test_soft_error_handling_defaults_and_retry_floor() -> None:
    """Soft-error handling is off by default and refuses negative retries."""
    defaults = MCPSoftErrorHandling()
    assert defaults.model_dump() == {
        'enabled': False,
        'keys': ['Note', 'Information', 'Error Message'],
        'max_retries': 1,
        'initial_backoff_seconds': 5.0,
        'max_backoff_seconds': 60.0,
    }
    assert MCPSoftErrorHandling(max_retries=0).max_retries == 0
    with pytest.raises(ValidationError) as excinfo:
        _ = MCPSoftErrorHandling(max_retries=-1)
    assert [(e['loc'], e['msg']) for e in excinfo.value.errors()] == [
        (('max_retries',), 'Value error, max_retries must be >= 0'),
    ]
