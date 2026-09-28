"""Credential convenience is explicit, predictable, and does not print secrets."""

from __future__ import annotations

import os
from typing import TYPE_CHECKING

import httpx
import pytest

from maivn import Agent, Client, ClientConfig, ConfigurationError, Swarm

if TYPE_CHECKING:
    from pathlib import Path


def test_explicit_env_file_is_a_fallback_without_mutating_environment(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A chosen dotenv file supplies defaults; terminal exports keep precedence."""
    dotenv = tmp_path / 'settings.env'
    dotenv.write_text(
        'MAIVN_API_KEY=file-key\nMAIVN_BASE_URL=https://file.example\n', encoding='utf-8'
    )
    monkeypatch.delenv('MAIVN_API_KEY', raising=False)
    monkeypatch.delenv('MAIVN_BASE_URL', raising=False)
    config = ClientConfig.from_sources(env_file=dotenv)
    assert config.api_key == 'file-key'
    assert config.base_url_text == 'https://file.example'
    assert 'MAIVN_API_KEY' not in os.environ
    monkeypatch.setenv('MAIVN_API_KEY', 'terminal-key')
    assert Client(env_file=dotenv).config.api_key == 'terminal-key'
    assert Client(api_key='explicit-key', env_file=dotenv).config.api_key == 'explicit-key'


def test_no_implicit_dotenv_search(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """An unrelated project file must not silently decide which account a client uses."""
    (tmp_path / '.env').write_text('MAIVN_API_KEY=unexpected-key\n', encoding='utf-8')
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv('MAIVN_API_KEY', raising=False)
    monkeypatch.delenv('MAIVN_API_KEY_FILE', raising=False)
    with pytest.raises(ConfigurationError, match='MAIVN_API_KEY'):
        Client()


def test_api_key_file_is_read_only_when_no_direct_or_environment_key_exists(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Explicit file selection supports mounted secrets without changing key precedence."""
    secret = tmp_path / 'api-key'
    secret.write_text('mounted-key\n', encoding='utf-8')
    monkeypatch.delenv('MAIVN_API_KEY', raising=False)
    assert Client(api_key_file=secret).config.api_key == 'mounted-key'
    monkeypatch.setenv('MAIVN_API_KEY_FILE', str(secret))
    assert Client().config.api_key == 'mounted-key'
    monkeypatch.setenv('MAIVN_API_KEY', 'terminal-key')
    missing = tmp_path / 'missing-key'
    assert Client(api_key_file=missing).config.api_key == 'terminal-key'
    assert Client(api_key='explicit-key', api_key_file=missing).config.api_key == 'explicit-key'


@pytest.mark.parametrize('content', ['', 'first-key\nsecond-key'])
def test_invalid_key_files_fail_without_echoing_contents(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    content: str,
) -> None:
    """Bad file contents yield an actionable error before any request is sent."""
    secret = tmp_path / 'api-key'
    secret.write_text(content, encoding='utf-8')
    monkeypatch.delenv('MAIVN_API_KEY', raising=False)
    with pytest.raises(ConfigurationError, match='API key file') as error:
        Client(api_key_file=secret)
    assert 'first-key' not in str(error.value)


def test_explicit_missing_files_are_reported(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A typo in a selected credential source is not silently ignored."""
    monkeypatch.delenv('MAIVN_API_KEY', raising=False)
    with pytest.raises(ConfigurationError, match='env file'):
        Client(env_file=tmp_path / 'missing.env')
    with pytest.raises(ConfigurationError, match='API key file'):
        Client(api_key_file=tmp_path / 'missing-key')


@pytest.mark.parametrize('scope_type', [Agent, Swarm])
def test_scopes_accept_selected_credential_files(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    scope_type: type[Agent | Swarm],
) -> None:
    """Scope construction shares the same configuration path as Client."""
    secret = tmp_path / 'api-key'
    secret.write_text('scope-file-key', encoding='utf-8')
    monkeypatch.delenv('MAIVN_API_KEY', raising=False)
    scope = scope_type(name='file-scope', api_key_file=secret)
    assert scope.client is not None
    assert scope.client.config.api_key == 'scope-file-key'


def test_plain_representations_do_not_show_credentials() -> None:
    """Debug printing common SDK objects must not disclose an API key."""
    agent = Agent(name='private-key', api_key='private-test-credential')
    assert agent.client is not None
    assert 'private-test-credential' not in repr(agent)
    assert 'private-test-credential' not in repr(agent.client.config)


@pytest.mark.parametrize('scope_type', [Agent, Swarm])
def test_injected_client_keeps_precedence_over_selected_files(
    tmp_path: Path,
    scope_type: type[Agent | Swarm],
) -> None:
    """Adding file options must not replace explicitly supplied configuration."""
    config = ClientConfig(api_key='injected-key')
    missing = tmp_path / 'not-selected'
    client = Client(config=config, env_file=missing, api_key_file=missing)
    scope = scope_type(name='injected', client=client, env_file=missing, api_key_file=missing)
    assert scope.client is client
    assert client.config.api_key == 'injected-key'


def test_selected_dotenv_can_name_a_key_file(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """File-source priority is consistent when deployment settings live in dotenv."""
    monkeypatch.delenv('MAIVN_API_KEY', raising=False)
    monkeypatch.delenv('MAIVN_API_KEY_FILE', raising=False)
    monkeypatch.chdir(tmp_path)
    (tmp_path / 'default-key').write_text('dotenv-file-key', encoding='utf-8')
    (tmp_path / 'override-key').write_text('explicit-file-key', encoding='utf-8')
    dotenv = tmp_path / 'settings.env'
    dotenv.write_text('MAIVN_API_KEY_FILE=default-key\n', encoding='utf-8')
    assert Client(env_file=dotenv).config.api_key == 'dotenv-file-key'
    monkeypatch.setenv('MAIVN_API_KEY_FILE', 'missing-key')
    assert (
        Client(env_file=dotenv, api_key_file='override-key').config.api_key == 'explicit-file-key'
    )
    dotenv.write_text('MAIVN_API_KEY=dotenv-value\n', encoding='utf-8')
    assert Client(env_file=dotenv, api_key_file='override-key').config.api_key == 'dotenv-value'


def test_empty_swarm_resolves_environment_on_first_execution(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Keyless discovery remains possible, and execution uses the current environment."""
    monkeypatch.delenv('MAIVN_API_KEY', raising=False)
    monkeypatch.delenv('MAIVN_API_KEY_FILE', raising=False)
    swarm = Swarm(name='late-environment')
    assert swarm.client is None
    monkeypatch.setenv('MAIVN_API_KEY', 'execution-key')

    async def send(
        _self: httpx.AsyncClient, request: httpx.Request, **_kwargs: object
    ) -> httpx.Response:
        assert request.headers['authorization'] == 'Bearer execution-key'
        return httpx.Response(
            200,
            request=request,
            json={
                'thread_id': 'thr-1',
                'status': 'completed',
                'history': [],
                'created_at': '2026-09-15T12:00:00Z',
                'updated_at': '2026-09-15T12:00:00Z',
            },
        )

    monkeypatch.setattr(httpx.AsyncClient, 'send', send)
    assert swarm.get_thread('thr-1').status == 'completed'
    assert swarm.client is not None
    assert swarm.client.config.api_key == 'execution-key'


def test_agent_reports_a_broken_environment_selected_key_file(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A broken selected file is not misreported as an absent credential."""
    monkeypatch.delenv('MAIVN_API_KEY', raising=False)
    monkeypatch.setenv('MAIVN_API_KEY_FILE', str(tmp_path / 'missing-key'))
    with pytest.raises(ConfigurationError, match='API key file'):
        Agent(name='broken-config')
