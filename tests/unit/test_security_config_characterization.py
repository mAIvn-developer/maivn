"""Characterize credential, env-file, vault-secret and base-URL configuration handling."""

from __future__ import annotations

import base64
import binascii
from typing import TYPE_CHECKING

import pytest
from pydantic import ValidationError

from maivn import ClientConfig, ConfigurationError, LocalVaultConfig, VaultSetupError
from maivn._internal.config import (
    LOCAL_BASE_URL,
    local_tool_base_url,
    local_vault_directory_from_environment,
    local_vault_from_environment,
)

if TYPE_CHECKING:
    from pathlib import Path

_SENTINEL = 'sk-characterization-secret-value'
_VAULT_HELP = 'Set MAIVN_VAULT_DIRECTORY to durable storage when supplying MAIVN_VAULT_KEY.'
_MISSING_KEY = (
    'MAIVN_API_KEY is required unless api_key, env_file, or api_key_file '
    '(MAIVN_API_KEY_FILE) supplies a credential'
)
_SDK_VARIABLES = (
    'MAIVN_API_KEY',
    'MAIVN_API_KEY_FILE',
    'MAIVN_PROJECT_ID',
    'MAIVN_BASE_URL',
    'MAIVN_VAULT_DIRECTORY',
    'MAIVN_VAULT_KEY',
)


@pytest.fixture(autouse=True)
def clean_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    """Start every case without SDK variables from the machine or the suite."""
    for name in _SDK_VARIABLES:
        monkeypatch.delenv(name, raising=False)


def test_vault_settings_default_to_nothing() -> None:
    """Without hosting variables the vault has no location and no key provider."""
    config = local_vault_from_environment()
    assert config == LocalVaultConfig()
    assert config.key_provider is None
    assert local_vault_directory_from_environment() is None


def test_vault_directory_alone_is_used_without_a_key(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """A durable directory alone selects storage and leaves the key to the OS store."""
    monkeypatch.setenv('MAIVN_VAULT_DIRECTORY', str(tmp_path))
    config = local_vault_from_environment()
    assert config.directory == str(tmp_path)
    assert config.key_provider is None


def test_vault_key_is_decoded_only_when_the_provider_runs(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """The hosting key stays encoded until storage asks for it, then decodes exactly."""
    material = bytes(range(32))
    monkeypatch.setenv('MAIVN_VAULT_DIRECTORY', str(tmp_path))
    monkeypatch.setenv('MAIVN_VAULT_KEY', base64.b64encode(material).decode())
    config = local_vault_from_environment()
    assert config.key_provider is not None
    assert config.key_provider() == material
    assert base64.b64encode(material).decode() not in repr(config)


def test_malformed_vault_key_fails_only_when_used(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """A non-base64 hosting key does not break configuration, only key retrieval."""
    monkeypatch.setenv('MAIVN_VAULT_DIRECTORY', str(tmp_path))
    monkeypatch.setenv('MAIVN_VAULT_KEY', 'not base64 at all!')
    config = local_vault_from_environment()
    assert config.key_provider is not None
    with pytest.raises(binascii.Error):
        config.key_provider()


def test_vault_key_without_directory_is_refused_without_the_key(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A key without durable storage is refused with guidance that never echoes the key."""
    monkeypatch.setenv('MAIVN_VAULT_KEY', _SENTINEL)
    with pytest.raises(VaultSetupError) as caught:
        local_vault_from_environment()
    assert str(caught.value) == _VAULT_HELP
    assert caught.value.__cause__ is None
    assert caught.value.__suppress_context__


def test_blank_vault_directory_without_a_key_names_only_the_directory(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Without a key, the guidance is about the blank directory, not the key (U05-D12)."""
    monkeypatch.setenv('MAIVN_VAULT_DIRECTORY', '   ')
    with pytest.raises(VaultSetupError) as caught:
        local_vault_from_environment()
    assert str(caught.value) == (
        'MAIVN_VAULT_DIRECTORY is blank; set it to a durable storage directory or unset it.'
    )
    assert caught.value.__cause__ is None
    assert local_vault_directory_from_environment() == '   '


def test_vault_directory_inspection_ignores_an_unusable_key(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Inspecting the location neither decodes nor validates the key."""
    monkeypatch.setenv('MAIVN_VAULT_DIRECTORY', str(tmp_path))
    monkeypatch.setenv('MAIVN_VAULT_KEY', 'not base64 at all!')
    assert local_vault_directory_from_environment() == str(tmp_path)


def test_missing_credential_message_is_exact() -> None:
    """No credential source produces the exact actionable message."""
    with pytest.raises(ConfigurationError) as caught:
        ClientConfig.from_sources()
    assert str(caught.value) == _MISSING_KEY


def test_blank_explicit_key_falls_back_to_the_environment(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A whitespace-only explicit key does not shadow the environment key."""
    monkeypatch.setenv('MAIVN_API_KEY', 'env-key')
    assert ClientConfig.from_sources(api_key='   ').api_key == 'env-key'


def test_blank_keys_everywhere_are_missing(monkeypatch: pytest.MonkeyPatch) -> None:
    """Whitespace in every source counts as no credential at all."""
    monkeypatch.setenv('MAIVN_API_KEY', '  ')
    with pytest.raises(ConfigurationError, match=r'^MAIVN_API_KEY is required'):
        ClientConfig.from_sources(api_key='')


def test_explicit_key_file_beats_the_environment_key_file(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """An explicitly selected key file wins over MAIVN_API_KEY_FILE."""
    chosen = tmp_path / 'chosen'
    chosen.write_text('  chosen-key  \n', encoding='utf-8')
    other = tmp_path / 'other'
    other.write_text('other-key\n', encoding='utf-8')
    monkeypatch.setenv('MAIVN_API_KEY_FILE', str(other))
    assert ClientConfig.from_sources(api_key_file=chosen).api_key == 'chosen-key'
    assert ClientConfig.from_sources().api_key == 'other-key'


def test_direct_key_means_the_key_file_is_never_read(tmp_path: Path) -> None:
    """A direct key leaves even an unreadable key file untouched."""
    config = ClientConfig.from_sources(api_key='direct', api_key_file=tmp_path / 'missing')
    assert config.api_key == 'direct'


@pytest.mark.parametrize('content', ['', '   \n', 'first\nsecond\n'])
def test_key_file_must_hold_exactly_one_line(tmp_path: Path, content: str) -> None:
    """An empty or multi-line key file is refused without echoing its contents."""
    path = tmp_path / 'key'
    path.write_text(content, encoding='utf-8')
    with pytest.raises(ConfigurationError) as caught:
        ClientConfig.from_sources(api_key_file=path)
    assert str(caught.value) == 'API key file must contain one non-empty line'


@pytest.mark.parametrize('kind', ['missing', 'directory', 'binary'])
def test_unreadable_key_file_is_refused_without_its_path_or_bytes(
    tmp_path: Path, kind: str
) -> None:
    """Read and decode failures share one value-free message with no chained cause."""
    path = tmp_path / 'private-key-location'
    if kind == 'directory':
        path.mkdir()
    elif kind == 'binary':
        path.write_bytes(b'\xff\xfe' + _SENTINEL.encode())
    with pytest.raises(ConfigurationError) as caught:
        ClientConfig.from_sources(api_key_file=path)
    assert str(caught.value) == 'Unable to read API key file'
    assert caught.value.__cause__ is None
    assert caught.value.__suppress_context__


def test_env_file_must_be_an_existing_regular_file(tmp_path: Path) -> None:
    """Missing paths and directories are refused before anything is read."""
    for selected in (tmp_path / 'missing.env', tmp_path):
        with pytest.raises(ConfigurationError) as caught:
            ClientConfig.from_sources(api_key='direct', env_file=selected)
        assert str(caught.value) == 'Selected env file does not exist or is not a regular file'


def test_undecodable_env_file_is_refused_without_its_contents(tmp_path: Path) -> None:
    """An env file that is not UTF-8 fails with a fixed message and no chained cause."""
    path = tmp_path / 'settings.env'
    path.write_bytes(b'MAIVN_API_KEY=' + _SENTINEL.encode() + b'\xff\xfe\n')
    with pytest.raises(ConfigurationError) as caught:
        ClientConfig.from_sources(env_file=path)
    assert str(caught.value) == 'Unable to read selected env file'
    assert caught.value.__cause__ is None
    assert caught.value.__suppress_context__


def test_env_file_supplies_key_project_and_origin(tmp_path: Path) -> None:
    """A selected env file is read as UTF-8 for every SDK setting."""
    path = tmp_path / 'settings.env'
    path.write_text(
        'MAIVN_API_KEY=clé-from-file\nMAIVN_PROJECT_ID=project-1\n'
        'MAIVN_BASE_URL=https://file.example/base/\n',
        encoding='utf-8',
    )
    config = ClientConfig.from_sources(env_file=path)
    assert config.api_key == 'clé-from-file'
    assert config.project_id == 'project-1'
    assert config.base_url_text == 'https://file.example/base'


def test_base_url_precedence_and_default(monkeypatch: pytest.MonkeyPatch) -> None:
    """Explicit origin beats MAIVN_BASE_URL, which beats the production default."""
    assert ClientConfig.from_sources(api_key='k').base_url_text == 'https://api.maivn.io'
    monkeypatch.setenv('MAIVN_BASE_URL', 'https://env.example')
    assert ClientConfig.from_sources(api_key='k').base_url_text == 'https://env.example'
    explicit = ClientConfig.from_sources(api_key='k', base_url='https://explicit.example/')
    assert explicit.base_url_text == 'https://explicit.example'


def test_blank_project_id_is_treated_as_absent(monkeypatch: pytest.MonkeyPatch) -> None:
    """A whitespace project id falls back to the environment, then to none."""
    assert ClientConfig.from_sources(api_key='k', project_id='  ').project_id is None
    monkeypatch.setenv('MAIVN_PROJECT_ID', 'env-project')
    assert ClientConfig.from_sources(api_key='k', project_id=' ').project_id == 'env-project'


def test_optional_settings_keep_defaults_unless_supplied() -> None:
    """Unsupplied timeouts keep their defaults; supplied ones are applied verbatim."""
    default = ClientConfig.from_sources(api_key='k')
    assert (
        default.timeout_seconds,
        default.thread_id,
        default.tool_execution_timeout,
        default.dependency_wait_timeout,
        default.total_execution_timeout,
    ) == (30.0, None, 900.0, 300.0, 7200.0)
    custom = ClientConfig.from_sources(
        api_key='k',
        timeout_seconds=1.5,
        thread_id='thread-1',
        tool_execution_timeout=2.0,
        dependency_wait_timeout=3.0,
        total_execution_timeout=4.0,
    )
    assert (
        custom.timeout_seconds,
        custom.thread_id,
        custom.tool_execution_timeout,
        custom.dependency_wait_timeout,
        custom.total_execution_timeout,
    ) == (1.5, 'thread-1', 2.0, 3.0, 4.0)


def test_config_repr_and_validation_errors_hide_the_key() -> None:
    """Neither the model repr nor its validation error echoes the API key."""
    config = ClientConfig(api_key=_SENTINEL)
    assert _SENTINEL not in repr(config)
    assert _SENTINEL not in str(config)
    with pytest.raises(ValidationError) as caught:
        ClientConfig.model_validate(
            {'api_key': _SENTINEL, 'timeout_seconds': 0, 'extra': _SENTINEL}
        )
    assert _SENTINEL not in str(caught.value)


def test_malformed_environment_origin_error_hides_the_key(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A malformed MAIVN_BASE_URL fails validation without echoing the key or the URL."""
    monkeypatch.setenv('MAIVN_API_KEY', _SENTINEL)
    monkeypatch.setenv('MAIVN_BASE_URL', 'not a url with secret-host-part')
    with pytest.raises(ValidationError) as caught:
        ClientConfig.from_sources()
    assert _SENTINEL not in str(caught.value)
    assert 'secret-host-part' not in str(caught.value)


def test_local_tool_origin_defaults_and_strips(monkeypatch: pytest.MonkeyPatch) -> None:
    """Local tools use the loopback API unless MAIVN_BASE_URL names another origin."""
    assert local_tool_base_url() == LOCAL_BASE_URL == 'http://127.0.0.1:8000'
    monkeypatch.setenv('MAIVN_BASE_URL', 'https://tools.example/')
    assert local_tool_base_url() == 'https://tools.example'
    monkeypatch.setenv('MAIVN_BASE_URL', 'not a url')
    assert local_tool_base_url() == LOCAL_BASE_URL
