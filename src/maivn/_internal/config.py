"""SDK configuration surface.

Only this module reads SDK environment variables.
"""

from __future__ import annotations

import base64
from pathlib import Path

from pydantic import AnyUrl, BaseModel, ConfigDict, Field, SecretStr, StrictStr
from pydantic_settings import BaseSettings, SettingsConfigDict

from maivn._internal.automatic_vault import LocalVaultConfig, VaultSetupError
from maivn._internal.errors import ConfigurationError, MissingCredentialError

DEFAULT_BASE_URL = 'https://api.maivn.io'
# The API origin of a platform running on this machine; the local terminal
# tools (connection setup, the webhook tunnel, the file watcher) default to it.
LOCAL_BASE_URL = 'http://127.0.0.1:8000'
# Named here, and quoted verbatim by the serving error, so a developer whose
# process cannot say which project it belongs to is told the variable rather
# than left to guess it.
PROJECT_ID_ENV_VAR = 'MAIVN_PROJECT_ID'
DEFAULT_TIMEOUT_SECONDS = 30.0
DEFAULT_TOOL_EXECUTION_TIMEOUT = 900.0
DEFAULT_DEPENDENCY_WAIT_TIMEOUT = 300.0
DEFAULT_TOTAL_EXECUTION_TIMEOUT = 7200.0


class _VaultEnvSettings(BaseSettings):
    """Read only explicitly named vault settings; never discover dotenv files."""

    model_config = SettingsConfigDict(extra='ignore', hide_input_in_errors=True)
    directory: str | None = Field(default=None, validation_alias='MAIVN_VAULT_DIRECTORY')
    key: SecretStr | None = Field(default=None, validation_alias='MAIVN_VAULT_KEY', repr=False)


def local_vault_from_environment() -> LocalVaultConfig:
    """Use hosting secret injection without changing ordinary Client construction."""
    try:
        settings = _VaultEnvSettings()
        encoded_key = settings.key
        if encoded_key is None:
            return LocalVaultConfig(directory=settings.directory)

        def key_provider() -> bytes:
            return base64.b64decode(encoded_key.get_secret_value(), validate=True)

        return LocalVaultConfig(
            directory=settings.directory,
            key_provider=key_provider,
        )
    except Exception:  # noqa: BLE001 - environment validation must not echo secret values.
        message = 'Set MAIVN_VAULT_DIRECTORY to durable storage when supplying MAIVN_VAULT_KEY.'
        raise VaultSetupError(message) from None


def local_vault_directory_from_environment() -> str | None:
    """Inspect the chosen location without decoding or validating unused key settings."""
    return _VaultEnvSettings().directory


class _SdkEnvSettings(BaseSettings):
    """Environment-backed SDK settings."""

    model_config = SettingsConfigDict(extra='ignore', hide_input_in_errors=True)

    api_key: str | None = Field(default=None, validation_alias='MAIVN_API_KEY', repr=False)
    api_key_file: str | None = Field(default=None, validation_alias='MAIVN_API_KEY_FILE')
    project_id: str | None = Field(default=None, validation_alias=PROJECT_ID_ENV_VAR)
    base_url: AnyUrl | None = Field(default=None, validation_alias='MAIVN_BASE_URL')


class ClientConfig(BaseModel):
    """HTTP client configuration for the v2 SDK."""

    model_config = ConfigDict(extra='forbid', frozen=True, hide_input_in_errors=True)

    api_key: str = Field(..., min_length=1, repr=False)
    project_id: StrictStr | None = Field(
        default=None,
        min_length=1,
        description='Project a serving process announces into; never inferred from the key.',
    )
    # AnyUrl(str) parses the default at class-definition time so the declared
    # attribute type (AnyUrl) matches the runtime value; a bare str literal
    # default is only str until pydantic validates it.  boundary
    base_url: AnyUrl = Field(default=AnyUrl(DEFAULT_BASE_URL))
    timeout_seconds: float = Field(default=DEFAULT_TIMEOUT_SECONDS, gt=0)
    thread_id: StrictStr | None = Field(
        default=None,
        min_length=1,
        description='Default thread id for invokes that omit a per-call thread id.',
    )
    tool_execution_timeout: float | None = Field(
        default=DEFAULT_TOOL_EXECUTION_TIMEOUT,
        gt=0,
        description=(
            'V1 per-tool timeout retained for compatibility; no v2 backend knob exists yet.'
        ),
    )
    dependency_wait_timeout: float | None = Field(
        default=DEFAULT_DEPENDENCY_WAIT_TIMEOUT,
        gt=0,
        description=(
            'V1 dependency wait timeout retained for compatibility; no v2 backend knob exists yet.'
        ),
    )
    total_execution_timeout: float | None = Field(
        default=DEFAULT_TOTAL_EXECUTION_TIMEOUT,
        gt=0,
        description=(
            'V1 total execution timeout retained for compatibility; no v2 backend knob exists yet.'
        ),
    )

    @classmethod
    def from_sources(  # noqa: PLR0913 - v1-compatible config accepts these public options.
        cls,
        *,
        api_key: str | None = None,
        env_file: str | Path | None = None,
        api_key_file: str | Path | None = None,
        project_id: str | None = None,
        base_url: str | None = None,
        timeout_seconds: float | None = None,
        thread_id: str | None = None,
        tool_execution_timeout: float | None = None,
        dependency_wait_timeout: float | None = None,
        total_execution_timeout: float | None = None,
    ) -> ClientConfig:
        """Resolve explicit values, process environment, selected dotenv, then key file.

        Files are opt-in; no parent-directory or PATH search is performed and
        loading a dotenv file does not mutate the process environment.
        """
        if env_file is not None and not Path(env_file).is_file():
            message = 'Selected env file does not exist or is not a regular file'
            raise ConfigurationError(message)
        try:
            env = _SdkEnvSettings(_env_file=env_file, _env_file_encoding='utf-8')  # pyright: ignore[reportCallIssue] - BaseSettings runtime options are omitted by the generated model signature.
        except (OSError, UnicodeError):
            message = 'Unable to read selected env file'
            raise ConfigurationError(message) from None
        resolved_api_key = _first_nonblank(api_key, env.api_key)
        selected_key_file = api_key_file if api_key_file is not None else env.api_key_file
        if resolved_api_key is None and selected_key_file is not None:
            resolved_api_key = _read_api_key_file(selected_key_file)
        if resolved_api_key is None:
            message = (
                'MAIVN_API_KEY is required unless api_key, env_file, or api_key_file '
                '(MAIVN_API_KEY_FILE) supplies a credential'
            )
            raise MissingCredentialError(message)
        resolved_base_url = base_url or (str(env.base_url) if env.base_url is not None else None)
        resolved_base_url = resolved_base_url or DEFAULT_BASE_URL
        payload: dict[str, object] = {
            'api_key': resolved_api_key,
            'project_id': _first_nonblank(project_id, env.project_id),
            'base_url': resolved_base_url,
        }
        payload.update(
            {
                name: value
                for name, value in (
                    ('timeout_seconds', timeout_seconds),
                    ('thread_id', thread_id),
                    ('tool_execution_timeout', tool_execution_timeout),
                    ('dependency_wait_timeout', dependency_wait_timeout),
                    ('total_execution_timeout', total_execution_timeout),
                )
                if value is not None
            }
        )
        return cls.model_validate(payload)

    @property
    def base_url_text(self) -> str:
        """Return the configured base URL as a plain string."""
        return str(self.base_url).rstrip('/')


def local_tool_base_url() -> str:
    """Return MAIVN_BASE_URL when set, otherwise the local platform's API origin."""
    try:
        configured = _SdkEnvSettings().base_url
    except Exception:  # noqa: BLE001 - a malformed value falls back like an unset one.
        configured = None
    return LOCAL_BASE_URL if configured is None else str(configured).rstrip('/')


def _read_api_key_file(path: str | Path) -> str:
    """Read one explicitly selected credential without echoing its contents on failure."""
    try:
        value = Path(path).read_text(encoding='utf-8').strip()
    except (OSError, UnicodeError):
        message = 'Unable to read API key file'
        raise ConfigurationError(message) from None
    if not value or len(value.splitlines()) != 1:
        message = 'API key file must contain one non-empty line'
        raise ConfigurationError(message)
    return value


def _first_nonblank(first: str | None, second: str | None) -> str | None:
    for value in (first, second):
        if value is not None and value.strip():
            return value
    return None


__all__ = ['LOCAL_BASE_URL', 'PROJECT_ID_ENV_VAR', 'ClientConfig', 'local_tool_base_url']
