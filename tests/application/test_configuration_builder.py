from __future__ import annotations

from maivn import ConfigurationBuilder


def test_configuration_builder_reads_environment(monkeypatch) -> None:
    monkeypatch.setenv("MAIVN_TIMEOUT", "42")
    monkeypatch.setenv("MAIVN_TOOL_EXECUTION_TIMEOUT", "123")
    monkeypatch.setenv("MAIVN_ENABLE_BACKGROUND_EXECUTION", "false")
    monkeypatch.setenv("MAIVN_LOG_LEVEL", "debug")

    config = ConfigurationBuilder.from_environment()

    assert config.server.timeout_seconds == 42.0
    assert config.execution.tool_execution_timeout_seconds == 123.0
    assert config.execution.enable_background_execution is False
    assert config.logging.level == "DEBUG"
