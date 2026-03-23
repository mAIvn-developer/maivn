from __future__ import annotations

from maivn._internal.api.agent import Agent
from maivn._internal.utils.configuration import MaivnConfiguration, temporary_configuration


def test_agent_client_cache_key_varies_across_configuration_contexts() -> None:
    config_a = MaivnConfiguration.from_dict(
        {
            "server": {
                "base_url": "http://localhost:8000",
                "mock_base_url": "http://localhost:8000",
                "timeout_seconds": 111,
            }
        }
    )
    config_b = MaivnConfiguration.from_dict(
        {
            "server": {
                "base_url": "http://localhost:9000",
                "mock_base_url": "http://localhost:9000",
                "timeout_seconds": 222,
            }
        }
    )

    with temporary_configuration(config_a):
        client_a1 = Agent._get_or_create_client("k")
        client_a2 = Agent._get_or_create_client("k")

    with temporary_configuration(config_b):
        client_b1 = Agent._get_or_create_client("k")

    assert client_a1 is client_a2
    assert client_a1 is not client_b1
