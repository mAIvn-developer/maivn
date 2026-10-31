"""``max_calls_per_minute`` paces MCP tool calls and ``max_calls_per_day`` caps them."""

from __future__ import annotations

import pytest

from maivn import MCPServer
from maivn._internal.compat import mcp
from maivn._internal.compat.mcp import MCPRuntimeError

JsonObject = dict[str, object]


class _FakeRuntime:
    def __init__(self) -> None:
        self.calls: list[str] = []

    def list_tools(self) -> list[JsonObject]:
        return []

    def call_tool(self, name: str, arguments: JsonObject) -> JsonObject:
        _ = arguments
        self.calls.append(name)
        return {'content': []}

    def close(self) -> None:
        return


class _Clock:
    def __init__(self) -> None:
        self.now = 1_000.0
        self.slept: list[float] = []

    def monotonic(self) -> float:
        return self.now

    def sleep(self, seconds: float) -> None:
        self.slept.append(seconds)
        self.now += seconds


@pytest.fixture
def clock(monkeypatch: pytest.MonkeyPatch) -> _Clock:
    """Replace the module clock so waits are recorded instead of slept."""
    fake = _Clock()
    monkeypatch.setattr(mcp, '_monotonic', fake.monotonic)
    monkeypatch.setattr(mcp, '_sleep', fake.sleep)
    return fake


def _server(**limits: int) -> tuple[MCPServer, _FakeRuntime]:
    server = MCPServer(name='quota', command='unused', **limits)  # pyright: ignore[reportArgumentType]
    runtime = _FakeRuntime()
    server._runtime = runtime  # noqa: SLF001  # pyright: ignore[reportPrivateUsage] - test transport.
    return server, runtime


def test_calls_over_the_minute_limit_wait_for_a_free_slot(clock: _Clock) -> None:
    """The third call inside one minute waits until the first call is a minute old."""
    server, runtime = _server(max_calls_per_minute=2)

    _ = server.call_tool('a', {})
    clock.now += 10
    _ = server.call_tool('b', {})
    _ = server.call_tool('c', {})

    assert runtime.calls == ['a', 'b', 'c']
    assert clock.slept == [pytest.approx(50.0)]


def test_calls_over_the_day_limit_are_refused_without_reaching_the_server(clock: _Clock) -> None:
    """Waiting a day is never useful, so the capped call fails at once."""
    server, runtime = _server(max_calls_per_day=1)

    _ = server.call_tool('a', {})
    with pytest.raises(MCPRuntimeError, match='max_calls_per_day'):
        _ = server.call_tool('b', {})

    assert runtime.calls == ['a']
    clock.now += 86_400
    _ = server.call_tool('c', {})
    assert runtime.calls == ['a', 'c']


def test_unlimited_server_never_waits(clock: _Clock) -> None:
    """Without limits every call goes straight through."""
    server, runtime = _server()

    for name in 'abcdef':
        _ = server.call_tool(name, {})

    assert runtime.calls == list('abcdef')
    assert clock.slept == []


def test_limits_must_allow_at_least_one_call() -> None:
    """A zero limit would block every call, so it is rejected up front."""
    with pytest.raises(ValueError, match='at least 1'):
        _ = MCPServer(name='quota', command='unused', max_calls_per_minute=0)
