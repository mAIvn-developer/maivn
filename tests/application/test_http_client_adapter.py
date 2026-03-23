from __future__ import annotations

from maivn._internal.adapters.networking.http_client import HttpClient
from maivn._internal.utils.configuration import (
    MaivnConfiguration,
    ServerConfiguration,
    temporary_configuration,
)


def test_http_client_uses_config_timeout() -> None:
    config = MaivnConfiguration(
        server=ServerConfiguration(
            base_url="http://example.com",
            mock_base_url="http://example.com",
            timeout_seconds=123.0,
        )
    )

    with temporary_configuration(config):
        client = HttpClient()

    assert client._timeout == 123.0


def test_http_client_explicit_timeout_overrides_config() -> None:
    config = MaivnConfiguration(
        server=ServerConfiguration(
            base_url="http://example.com",
            mock_base_url="http://example.com",
            timeout_seconds=111.0,
        )
    )

    with temporary_configuration(config):
        client = HttpClient(timeout=5.5)

    assert client._timeout == 5.5
