"""Credential-shaped MCP arguments are the SDK's job, never the model's.

An MCP server advertises whatever input schema it likes, and many advertise the
credential they need as an ordinary argument - every one of the twenty-one Alpha
Vantage tools declares `api_key`. A model cannot know a real credential, so it
either invents one or, far worse, decides it has none and silently refuses to
call the tool at all. That is exactly what made mcp-auto-setup flaky: three runs
in four returned "no API key was available" and an empty result, while the key
had been configured correctly the entire time.

Credentials must not be a thing the model reasons about. The SDK strips
credential-shaped arguments from the schema the model sees and supplies them
itself, so the outcome no longer depends on the model's judgement.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, cast

from maivn import MCPServer
from maivn._internal import scope as scope_module

if TYPE_CHECKING:
    from collections.abc import Mapping

    import pytest

_QUOTE_TOOL: dict[str, Any] = {
    'name': 'global_quote',
    'description': 'Get the latest price for a stock symbol.',
    'inputSchema': {
        'type': 'object',
        'properties': {
            'api_key': {'type': 'string', 'description': 'Alpha Vantage API key'},
            'symbol': {'type': 'string', 'description': 'Ticker symbol (e.g. AAPL)'},
        },
        'required': ['symbol'],
    },
}


class _Scope:
    def __init__(self, server: MCPServer, private_data: dict[str, object] | None = None) -> None:
        self.mcp_servers = (server,)
        self.private_data = private_data or {}


def _server(**kwargs: Any) -> MCPServer:
    return MCPServer(name='alpha_vantage', transport='stdio', command='python', **kwargs)


def _compile(
    monkeypatch: pytest.MonkeyPatch,
    server: MCPServer,
    tool: dict[str, Any] | None = None,
    private_data: dict[str, object] | None = None,
) -> Any:
    """Compile the server's tools without starting a real stdio process."""
    listed = [tool or _QUOTE_TOOL]

    def _list_tools(_self: MCPServer) -> list[dict[str, Any]]:
        return listed

    monkeypatch.setattr(MCPServer, 'list_tools', _list_tools)
    # Fetched dynamically: the module-private compiler is the unit under test, and
    # `getattr` keeps the private access at one named spot.
    compile_tools = getattr(scope_module, '_compile_mcp_tools')  # noqa: B009
    compiled = cast('list[Any]', compile_tools(_Scope(server, private_data)))
    assert len(compiled) == 1
    return compiled[0]


def _record_calls(monkeypatch: pytest.MonkeyPatch) -> dict[str, object]:
    """Capture the arguments the SDK actually sends to the MCP server."""
    seen: dict[str, object] = {}

    def _call_tool(
        _self: MCPServer, _name: str, arguments: Mapping[str, object]
    ) -> dict[str, object]:
        seen.update(arguments)
        return {'ok': True}

    monkeypatch.setattr(MCPServer, 'call_tool', _call_tool)
    return seen


def _properties(compiled: Any) -> Mapping[str, object]:
    schema = cast('Mapping[str, object]', compiled.input_schema)
    return cast('Mapping[str, object]', schema['properties'])


def test_a_credential_shaped_argument_is_not_advertised_to_the_model(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The model never sees `api_key`, so it can never decide it is missing."""
    compiled = _compile(monkeypatch, _server())

    assert 'api_key' not in _properties(compiled)


def test_ordinary_arguments_are_left_alone(monkeypatch: pytest.MonkeyPatch) -> None:
    """Only credentials are hidden - the tool must stay usable."""
    compiled = _compile(monkeypatch, _server())

    assert 'symbol' in _properties(compiled)


def test_a_hidden_argument_is_dropped_from_required(monkeypatch: pytest.MonkeyPatch) -> None:
    """A required-but-hidden argument would advertise a schema the model cannot satisfy."""
    tool = {
        'name': 'global_quote',
        'inputSchema': {
            'type': 'object',
            'properties': {
                'api_key': {'type': 'string'},
                'symbol': {'type': 'string'},
            },
            'required': ['api_key', 'symbol'],
        },
    }
    compiled = _compile(monkeypatch, _server(), tool)
    schema = cast('Mapping[str, object]', compiled.input_schema)

    assert schema['required'] == ['symbol']


def test_a_developer_can_name_an_out_of_the_ordinary_argument(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Not every injected argument is credential-shaped - a tenant id is not."""
    tool = {
        'name': 'global_quote',
        'inputSchema': {
            'type': 'object',
            'properties': {'tenant_id': {'type': 'string'}, 'symbol': {'type': 'string'}},
            'required': ['symbol'],
        },
    }
    compiled = _compile(monkeypatch, _server(injected_tool_args={'tenant_id': 'acme'}), tool)

    assert 'tenant_id' not in _properties(compiled)
    assert 'symbol' in _properties(compiled)


def test_an_injected_value_reaches_the_server(monkeypatch: pytest.MonkeyPatch) -> None:
    """Hiding an argument is only half the job - the SDK still has to supply it."""
    seen = _record_calls(monkeypatch)
    compiled = _compile(monkeypatch, _server(injected_tool_args={'api_key': 'configured-key'}))

    compiled.target(symbol='AAPL')

    assert seen['api_key'] == 'configured-key'
    assert seen['symbol'] == 'AAPL'


def test_a_model_supplied_value_cannot_override_an_injected_one(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Defence in depth: the argument is hidden, but a forged one must still lose.

    Model-authored arguments otherwise overwrite defaults, so a model that emitted
    `api_key` anyway would replace the configured credential with its invention.
    """
    seen = _record_calls(monkeypatch)
    compiled = _compile(monkeypatch, _server(injected_tool_args={'api_key': 'configured-key'}))

    compiled.target(symbol='AAPL', api_key='model-invented')

    assert seen['api_key'] == 'configured-key'


def test_a_server_without_configured_values_simply_omits_the_credential(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """With nothing configured the argument is absent, not empty or invented.

    MCP servers read their own credentials from their own environment - the Alpha
    Vantage server falls back to `ALPHA_VANTAGE_API_KEY`. Sending nothing lets that
    happen; sending an invented value defeats it.
    """
    seen = _record_calls(monkeypatch)
    compiled = _compile(monkeypatch, _server())

    compiled.target(symbol='AAPL')

    assert 'api_key' not in seen


def test_a_private_data_placeholder_in_a_tool_default_is_resolved(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """`tool_defaults` are merged AFTER the runtime resolves the model's arguments.

    A developer parks a private URL in `tool_defaults` and refers to it by
    placeholder - that is the documented way to keep a credential-bearing URL out
    of the model entirely. The placeholder was merged in at call time, downstream of
    every resolution step, so the MCP server received the literal `{_{alpha_url}_}`
    and rejected it. That silently emptied all three feeds in
    financial-planner-mcp while the run still reported success.
    """
    seen = _record_calls(monkeypatch)
    server = _server(tool_defaults={'global_quote': {'url': '{_{alpha_url}_}'}})
    compiled = _compile(
        monkeypatch,
        server,
        private_data={'alpha_url': 'https://example.test/query?symbol=AAPL'},
    )

    compiled.target(symbol='AAPL')

    assert seen['url'] == 'https://example.test/query?symbol=AAPL'
