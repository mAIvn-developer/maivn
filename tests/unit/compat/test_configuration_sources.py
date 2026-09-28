"""Compatibility builders share the SDK's explicit credential-source behavior."""

from __future__ import annotations

from collections.abc import Callable
from typing import TYPE_CHECKING

import pytest
from pydantic import ValidationError

from maivn import (
    Client,
    ClientBuilder,
    ClientConfig,
    ConfigurationBuilder,
    ConfigurationError,
    MaivnConfiguration,
)
from maivn._internal.compat.configuration import SecurityConfiguration

if TYPE_CHECKING:
    from pathlib import Path


EnvironmentBuilder = Callable[..., Client | MaivnConfiguration]
_DEFAULT_TOOL_EXECUTION_TIMEOUT = 900.0
_DEFAULT_DEPENDENCY_WAIT_TIMEOUT = 300.0
_DEFAULT_TOTAL_EXECUTION_TIMEOUT = 7200.0


def _client_config(result: Client | MaivnConfiguration) -> ClientConfig:
    """Expose the client configuration produced by either compatibility entrypoint."""
    return result.config if isinstance(result, Client) else result.to_client_config()


@pytest.mark.parametrize(
    'build',
    [ConfigurationBuilder.from_environment, ClientBuilder.from_environment],
)
def test_environment_builders_use_only_selected_source_files(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    build: EnvironmentBuilder,
) -> None:
    """A builder reads an explicitly selected dotenv and its named key file."""
    key_file = tmp_path / 'selected-key'
    key_file.write_text('selected-file-key\n', encoding='utf-8')
    dotenv = tmp_path / 'selected.env'
    dotenv.write_text(
        f'MAIVN_API_KEY_FILE={key_file}\nMAIVN_BASE_URL=https://dotenv.example\n',
        encoding='utf-8',
    )
    monkeypatch.delenv('MAIVN_API_KEY', raising=False)
    monkeypatch.delenv('MAIVN_API_KEY_FILE', raising=False)

    config = _client_config(build(env_file=dotenv))

    assert config.api_key == 'selected-file-key'
    assert config.base_url_text == 'https://dotenv.example'


@pytest.mark.parametrize(
    'build',
    [ConfigurationBuilder.from_environment, ClientBuilder.from_environment],
)
def test_environment_builders_keep_explicit_and_process_values_over_selected_files(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    build: EnvironmentBuilder,
) -> None:
    """Explicit keys outrank process values, which outrank selected dotenv values."""
    key_file = tmp_path / 'selected-key'
    key_file.write_text('selected-file-key\n', encoding='utf-8')
    dotenv = tmp_path / 'selected.env'
    dotenv.write_text(
        f'MAIVN_API_KEY=dotenv-key\nMAIVN_API_KEY_FILE={key_file}\n',
        encoding='utf-8',
    )
    monkeypatch.setenv('MAIVN_API_KEY', 'process-key')

    assert _client_config(build(env_file=dotenv)).api_key == 'process-key'
    assert _client_config(build(api_key='explicit-key', env_file=dotenv)).api_key == 'explicit-key'


@pytest.mark.parametrize(
    'build',
    [ConfigurationBuilder.from_environment, ClientBuilder.from_environment],
)
def test_environment_builders_accept_an_explicit_api_key_file(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    build: EnvironmentBuilder,
) -> None:
    """A mounted key is read only when its path is explicitly selected."""
    key_file = tmp_path / 'mounted-key'
    key_file.write_text('mounted-key\n', encoding='utf-8')
    monkeypatch.delenv('MAIVN_API_KEY', raising=False)
    monkeypatch.delenv('MAIVN_API_KEY_FILE', raising=False)

    assert _client_config(build(api_key_file=key_file)).api_key == 'mounted-key'


@pytest.mark.parametrize(
    'build',
    [ConfigurationBuilder.from_environment, ClientBuilder.from_environment],
)
def test_environment_builders_do_not_discover_dotenv_files(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    build: EnvironmentBuilder,
) -> None:
    """A nearby dotenv file cannot silently select a credential for a builder."""
    (tmp_path / '.env').write_text('MAIVN_API_KEY=unexpected-key\n', encoding='utf-8')
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv('MAIVN_API_KEY', raising=False)
    monkeypatch.delenv('MAIVN_API_KEY_FILE', raising=False)

    with pytest.raises(ConfigurationError, match='MAIVN_API_KEY'):
        build()


def test_compat_configuration_preserves_client_config_options() -> None:
    """A configured compatibility object keeps every request-capable client setting."""
    timeout_seconds = 12
    tool_execution_timeout = 45
    dependency_wait_timeout = 46
    total_execution_timeout = 47
    compatibility = ConfigurationBuilder.from_environment(
        api_key='compat-key',
        project_id='project-1',
        base_url='https://data.example',
        timeout_seconds=timeout_seconds,
        thread_id='thread-1',
        tool_execution_timeout=tool_execution_timeout,
        dependency_wait_timeout=dependency_wait_timeout,
        total_execution_timeout=total_execution_timeout,
    )

    client = ClientBuilder.from_configuration(compatibility)

    assert client.config.api_key == 'compat-key'
    assert client.config.project_id == 'project-1'
    assert client.config.base_url_text == 'https://data.example'
    assert client.config.timeout_seconds == timeout_seconds
    assert client.config.thread_id == 'thread-1'
    assert client.config.tool_execution_timeout == tool_execution_timeout
    assert client.config.dependency_wait_timeout == dependency_wait_timeout
    assert client.config.total_execution_timeout == total_execution_timeout


def test_partial_compat_configuration_keeps_legacy_defaults(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Nested defaults remain explicit while untouched execution options use client defaults."""
    monkeypatch.setenv('MAIVN_BASE_URL', 'https://process.example')
    configuration = MaivnConfiguration(security=SecurityConfiguration(api_key='compat-key'))

    client_config = configuration.to_client_config()

    assert client_config.base_url_text == 'https://api.maivn.io'
    assert client_config.tool_execution_timeout == _DEFAULT_TOOL_EXECUTION_TIMEOUT
    assert client_config.dependency_wait_timeout == _DEFAULT_DEPENDENCY_WAIT_TIMEOUT
    assert client_config.total_execution_timeout == _DEFAULT_TOTAL_EXECUTION_TIMEOUT


def test_compat_configuration_without_plane_overrides_uses_one_entry_point(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The compatibility surface collapses to the single public entry point too.

    ``ConfigurationBuilder``/``MaivnConfiguration`` are the legacy-shaped
    on-ramp; they must not reintroduce per-plane origins that the plain
    `Client()` path already collapses when nothing overrides them.
    """
    monkeypatch.delenv('MAIVN_BASE_URL', raising=False)
    monkeypatch.delenv('MAIVN_CONTROL_PLANE_BASE_URL', raising=False)
    monkeypatch.delenv('MAIVN_EVENT_PLANE_BASE_URL', raising=False)
    configuration = MaivnConfiguration(security=SecurityConfiguration(api_key='compat-key'))

    client_config = configuration.to_client_config()

    assert client_config.base_url_text == 'https://api.maivn.io'


def test_nested_security_configuration_redacts_credentials() -> None:
    """Compatibility configuration diagnostics never render the API key value."""
    secret = 'do-not-render-this-credential'  # noqa: S105 - test-only secret fixture.
    configuration = MaivnConfiguration(security=SecurityConfiguration(api_key=secret))

    assert secret not in repr(configuration)
    with pytest.raises(ValidationError) as caught:
        SecurityConfiguration.model_validate({'api_key': [secret]})
    assert secret not in str(caught.value)
    with pytest.raises(ValidationError) as nested_caught:
        MaivnConfiguration.model_validate({'security': {'api_key': [secret]}})
    assert secret not in str(nested_caught.value)
