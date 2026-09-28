"""V1-compatible configuration objects backed by v2 ClientConfig."""

from __future__ import annotations

from contextvars import ContextVar
from typing import TYPE_CHECKING, ClassVar, cast

from pydantic import BaseModel, ConfigDict, Field

from maivn._internal.client import Client
from maivn._internal.config import DEFAULT_BASE_URL, ClientConfig

if TYPE_CHECKING:
    from pathlib import Path


class ServerConfiguration(BaseModel):
    """Server connection settings."""

    model_config: ClassVar[ConfigDict] = ConfigDict(extra='ignore')

    base_url: str = DEFAULT_BASE_URL
    mock_base_url: str = DEFAULT_BASE_URL
    timeout_seconds: float = 30.0
    max_retries: int = 3
    deployment_timezone: str = 'UTC'
    project_id: str | None = None


class ExecutionConfiguration(BaseModel):
    """SDK execution defaults."""

    model_config: ClassVar[ConfigDict] = ConfigDict(extra='ignore')

    default_timeout_seconds: float = 30.0
    pending_event_timeout_seconds: float = 30.0
    max_parallel_tools: int = 8
    enable_background_execution: bool = False
    tool_execution_timeout_seconds: float = 30.0
    dependency_wait_timeout_seconds: float = 30.0
    total_execution_timeout_seconds: float | None = None
    thread_id: str | None = None


class SecurityConfiguration(BaseModel):
    """SDK security settings."""

    model_config: ClassVar[ConfigDict] = ConfigDict(extra='ignore', hide_input_in_errors=True)

    api_key: str | None = Field(default=None, repr=False)


class LoggingConfiguration(BaseModel):
    """SDK logging settings."""

    model_config: ClassVar[ConfigDict] = ConfigDict(extra='ignore')

    level: str = 'INFO'
    format_string: str | None = None
    enable_timing_logs: bool = False


class MaivnConfiguration(BaseModel):
    """V1-compatible nested SDK configuration."""

    model_config: ClassVar[ConfigDict] = ConfigDict(extra='ignore', hide_input_in_errors=True)

    server: ServerConfiguration = Field(default_factory=ServerConfiguration)
    execution: ExecutionConfiguration = Field(default_factory=ExecutionConfiguration)
    security: SecurityConfiguration = Field(default_factory=SecurityConfiguration)
    logging: LoggingConfiguration = Field(default_factory=LoggingConfiguration)

    @classmethod
    def from_dict(cls, value: dict[str, object]) -> MaivnConfiguration:
        """Build configuration from a raw mapping."""
        return cls.model_validate(value)

    def to_dict(self) -> dict[str, object]:
        """Return a JSON-compatible mapping."""
        return cast('dict[str, object]', self.model_dump(mode='json', exclude_none=True))

    def to_client_config(self) -> ClientConfig:
        """Return the v2 client config represented by this configuration."""
        return ClientConfig.from_sources(
            api_key=self.security.api_key,
            project_id=self.server.project_id,
            base_url=self.server.base_url,
            timeout_seconds=self.server.timeout_seconds,
            thread_id=self.execution.thread_id,
            tool_execution_timeout=(
                self.execution.tool_execution_timeout_seconds
                if 'tool_execution_timeout_seconds' in self.execution.model_fields_set
                else None
            ),
            dependency_wait_timeout=(
                self.execution.dependency_wait_timeout_seconds
                if 'dependency_wait_timeout_seconds' in self.execution.model_fields_set
                else None
            ),
            total_execution_timeout=(
                self.execution.total_execution_timeout_seconds
                if 'total_execution_timeout_seconds' in self.execution.model_fields_set
                else None
            ),
        )


def _configuration_from_client_config(client_config: ClientConfig) -> MaivnConfiguration:
    """Represent every ClientConfig setting available through the nested compat model."""
    return MaivnConfiguration(
        server=ServerConfiguration(
            base_url=client_config.base_url_text,
            project_id=client_config.project_id,
            timeout_seconds=client_config.timeout_seconds,
        ),
        execution=ExecutionConfiguration(
            tool_execution_timeout_seconds=client_config.tool_execution_timeout or 30.0,
            dependency_wait_timeout_seconds=client_config.dependency_wait_timeout or 30.0,
            total_execution_timeout_seconds=client_config.total_execution_timeout,
            thread_id=client_config.thread_id,
        ),
        security=SecurityConfiguration(api_key=client_config.api_key),
    )


_configuration_var: ContextVar[MaivnConfiguration | None] = ContextVar(
    'maivn_configuration',
    default=None,
)


class ConfigurationBuilder:
    """Builder for creating MaivnConfiguration instances."""

    @staticmethod
    def from_dict(value: dict[str, object]) -> MaivnConfiguration:
        """Create configuration from a raw mapping."""
        return MaivnConfiguration.from_dict(value)

    @staticmethod
    def from_environment(  # noqa: PLR0913 - mirrors the supported ClientConfig input options.
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
    ) -> MaivnConfiguration:
        """Create SDK configuration from explicit values over selected source files."""
        client_config = ClientConfig.from_sources(
            api_key=api_key,
            env_file=env_file,
            api_key_file=api_key_file,
            project_id=project_id,
            base_url=base_url,
            timeout_seconds=timeout_seconds,
            thread_id=thread_id,
            tool_execution_timeout=tool_execution_timeout,
            dependency_wait_timeout=dependency_wait_timeout,
            total_execution_timeout=total_execution_timeout,
        )
        return _configuration_from_client_config(client_config)


class ClientBuilder:
    """Factory helpers for creating v2 Client instances through v1 names."""

    @staticmethod
    def from_configuration(configuration: MaivnConfiguration) -> Client:
        """Create a v2 client from an explicit MaivnConfiguration instance."""
        return Client(config=configuration.to_client_config())

    @staticmethod
    def from_environment(  # noqa: PLR0913 - mirrors the supported ClientConfig input options.
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
    ) -> Client:
        """Create a client from explicit values over selected source files."""
        return Client(
            config=ClientConfig.from_sources(
                api_key=api_key,
                env_file=env_file,
                api_key_file=api_key_file,
                project_id=project_id,
                base_url=base_url,
                timeout_seconds=timeout_seconds,
                thread_id=thread_id,
                tool_execution_timeout=tool_execution_timeout,
                dependency_wait_timeout=dependency_wait_timeout,
                total_execution_timeout=total_execution_timeout,
            )
        )


def get_configuration() -> MaivnConfiguration:
    """Return the active configuration for this context."""
    config = _configuration_var.get()
    if config is None:
        config = MaivnConfiguration()
        _ = _configuration_var.set(config)
    return config


def set_configuration(config: MaivnConfiguration) -> None:
    """Set the active configuration for this context."""
    _ = _configuration_var.set(config)


__all__ = [
    'ClientBuilder',
    'ConfigurationBuilder',
    'ExecutionConfiguration',
    'LoggingConfiguration',
    'MaivnConfiguration',
    'SecurityConfiguration',
    'ServerConfiguration',
    'get_configuration',
    'set_configuration',
]
