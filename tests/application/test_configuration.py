from __future__ import annotations

import pytest

from maivn._internal.utils.configuration.config_store import (
    get_configuration,
    reset_configuration,
    set_configuration,
    temporary_configuration,
)
from maivn._internal.utils.configuration.environment_config import (
    ExecutionConfiguration,
    LoggingConfiguration,
    MaivnConfiguration,
    SecurityConfiguration,
    ServerConfiguration,
    _parse_bool,
)


def test_temporary_configuration_restores_previous() -> None:
    original = get_configuration()
    override = MaivnConfiguration(
        security=SecurityConfiguration(api_key="key", require_api_key=False)
    )

    with temporary_configuration(override):
        assert get_configuration() is override

    assert get_configuration() is original


def test_reset_configuration_creates_new_default() -> None:
    override = MaivnConfiguration(
        security=SecurityConfiguration(api_key="key", require_api_key=False)
    )
    set_configuration(override)

    reset_configuration()
    current = get_configuration()

    assert current is not override
    assert isinstance(current, MaivnConfiguration)


def test_environment_config_validation() -> None:
    with pytest.raises(ValueError):
        ServerConfiguration(base_url="ftp://invalid")

    with pytest.raises(ValueError):
        ExecutionConfiguration(default_timeout_seconds=0)

    assert _parse_bool("true") is True
    assert _parse_bool("0") is False
    assert _parse_bool(1) is True
    assert _parse_bool(0) is False

    config = MaivnConfiguration(
        server=ServerConfiguration(),
        execution=ExecutionConfiguration(),
        security=SecurityConfiguration(api_key=None, require_api_key=True),
        logging=LoggingConfiguration(level="VERBOSE"),
    )

    errors = config.validate()

    assert any("API key is required" in error for error in errors)
    assert any("Log level must be one of" in error for error in errors)
