from __future__ import annotations

from maivn import Client
from maivn._internal.utils.configuration import (
    ExecutionConfiguration,
    MaivnConfiguration,
    ServerConfiguration,
)


def test_client_timeout_overrides_configuration() -> None:
    config = MaivnConfiguration(
        server=ServerConfiguration(),
        execution=ExecutionConfiguration(tool_execution_timeout_seconds=111.0),
    )

    client = Client.from_configuration(
        api_key=None,
        configuration=config,
        tool_execution_timeout=222.0,
    )

    assert client.get_tool_execution_timeout() == 222.0
