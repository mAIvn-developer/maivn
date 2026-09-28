"""Unit tests for SDK configuration loading."""

from __future__ import annotations

import pytest

from maivn import Agent, Client, ClientConfig, ConfigurationError
from maivn._internal.config import LOCAL_BASE_URL, local_tool_base_url


def test_agent_uses_client_environment_defaults(monkeypatch: pytest.MonkeyPatch) -> None:
    """Creating an agent needs no repeated credential or endpoint plumbing."""
    monkeypatch.setenv('MAIVN_API_KEY', 'env-key')
    monkeypatch.setenv('MAIVN_BASE_URL', 'https://env.example')
    agent = Agent(name='environment-agent')
    assert agent.client is not None
    assert agent.client.config == ClientConfig.from_sources()


def test_agent_explicit_config_overrides_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    """Opting into environment defaults does not take precedence over caller choices."""
    monkeypatch.setenv('MAIVN_API_KEY', 'env-key')
    monkeypatch.setenv('MAIVN_BASE_URL', 'https://env.example')
    agent = Agent(
        name='explicit-agent', api_key='explicit-key', base_url='https://explicit.example'
    )
    assert agent.client is not None
    assert agent.client.config.api_key == 'explicit-key'
    assert agent.client.config.base_url_text == 'https://explicit.example'


def test_config_prefers_constructor_values(monkeypatch: pytest.MonkeyPatch) -> None:
    """Constructor values override environment-backed defaults."""
    monkeypatch.setenv('MAIVN_API_KEY', 'env-key')
    monkeypatch.setenv('MAIVN_BASE_URL', 'https://env.example')

    config = ClientConfig.from_sources(
        api_key='ctor-key',
        base_url='https://ctor.example',
    )

    assert config.api_key == 'ctor-key'
    assert str(config.base_url) == 'https://ctor.example/'


def test_config_reads_sdk_env_vars(monkeypatch: pytest.MonkeyPatch) -> None:
    """The SDK settings surface owns exactly one public origin: MAIVN_BASE_URL."""
    monkeypatch.setenv('MAIVN_API_KEY', 'env-key')
    monkeypatch.setenv('MAIVN_BASE_URL', 'https://env.example')

    config = ClientConfig.from_sources()

    assert config.api_key == 'env-key'
    assert str(config.base_url) == 'https://env.example/'


def test_config_defaults_to_the_production_endpoint(monkeypatch: pytest.MonkeyPatch) -> None:
    """An SDK client without URL configuration reaches the production API.

    The platform has one public API entry point
    (``https://api.maivn.io``), and a bare `Client()` must never quietly
    split its calls across separate addresses.
    """
    monkeypatch.setenv('MAIVN_API_KEY', 'env-key')
    monkeypatch.delenv('MAIVN_BASE_URL', raising=False)

    config = ClientConfig.from_sources()

    assert config.base_url_text == 'https://api.maivn.io'


def test_legacy_per_plane_env_vars_are_no_longer_read(monkeypatch: pytest.MonkeyPatch) -> None:
    """The retired MAIVN_CONTROL_PLANE_BASE_URL/MAIVN_EVENT_PLANE_BASE_URL are ignored.

    There is exactly one public address knob (``MAIVN_BASE_URL``); setting the
    old per-plane variables must not change where the SDK sends requests.
    """
    monkeypatch.setenv('MAIVN_API_KEY', 'env-key')
    monkeypatch.delenv('MAIVN_BASE_URL', raising=False)
    monkeypatch.setenv('MAIVN_CONTROL_PLANE_BASE_URL', 'https://control.example')
    monkeypatch.setenv('MAIVN_EVENT_PLANE_BASE_URL', 'https://events.example')

    config = ClientConfig.from_sources()

    assert config.base_url_text == 'https://api.maivn.io'


def test_client_config_has_no_public_per_plane_fields() -> None:
    """The public config surface exposes exactly one address: base_url."""
    assert 'control_plane_base_url' not in ClientConfig.model_fields
    assert 'event_plane_base_url' not in ClientConfig.model_fields


def test_client_rejects_unknown_per_plane_constructor_arguments() -> None:
    """`Client()` no longer accepts per-plane base URL overrides."""
    with pytest.raises(TypeError):
        Client(
            api_key='test-key',
            base_url='https://data.example',
            control_plane_base_url='https://control.example',  # pyright: ignore[reportCallIssue]
        )


def test_config_requires_api_key(monkeypatch: pytest.MonkeyPatch) -> None:
    """A request-capable client config must have an API key."""
    monkeypatch.delenv('MAIVN_API_KEY', raising=False)
    monkeypatch.delenv('MAIVN_BASE_URL', raising=False)

    with pytest.raises(ConfigurationError, match='MAIVN_API_KEY'):
        _ = ClientConfig.from_sources()


def test_no_second_origin_is_configurable(monkeypatch: pytest.MonkeyPatch) -> None:
    """Private file bytes and every other call use MAIVN_BASE_URL; nothing names another origin."""
    monkeypatch.setenv('MAIVN_API_KEY', 'env-key')
    monkeypatch.setenv('MAIVN_BASE_URL', 'https://api.example')
    monkeypatch.setenv('MAIVN_VAULT_ORIGIN', 'https://elsewhere.example')

    config = ClientConfig.from_sources()

    assert config.base_url_text == 'https://api.example'
    assert set(ClientConfig.model_fields) & {'vault_origin'} == set()
    for name in ('control_plane_base_url_text', 'event_plane_base_url_text', 'vault_origin_text'):
        assert not hasattr(config, name)


def test_local_tools_default_to_the_one_origin(monkeypatch: pytest.MonkeyPatch) -> None:
    """Terminal tools use MAIVN_BASE_URL, else the local platform's API origin."""
    monkeypatch.delenv('MAIVN_BASE_URL', raising=False)
    assert local_tool_base_url() == LOCAL_BASE_URL == 'http://127.0.0.1:8000'
    monkeypatch.setenv('MAIVN_BASE_URL', 'https://api.example/')
    assert local_tool_base_url() == 'https://api.example'
