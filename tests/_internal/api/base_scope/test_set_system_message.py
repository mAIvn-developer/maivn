# pyright: strict
"""Tests for the public ``BaseScope.set_system_message`` API.

``set_system_message`` is the public re-point for the resolved system message
shared by :class:`Agent` and :class:`Swarm`. It delegates to the same
normalization used at construction time, so callers (e.g. maivn-studio) no
longer need to reach into the private ``_system_message`` PrivateAttr.
"""

from __future__ import annotations

from typing import cast

from maivn_shared import SystemMessage

from maivn._internal.api.agent import Agent
from maivn._internal.api.client import Client
from maivn._internal.api.swarm import Swarm
from maivn._internal.utils.configuration import MaivnConfiguration, ServerConfiguration

# MARK: - Helpers


def _make_client() -> Client:
    config = MaivnConfiguration(
        server=ServerConfiguration(
            base_url="http://example.com",
            mock_base_url="http://example.com",
        )
    )
    return Client.from_configuration(api_key="key", configuration=config)


def _resolved_system_message(scope: Agent | Swarm) -> SystemMessage | None:
    """Read the resolved ``_system_message`` PrivateAttr (Pattern 2 accessor)."""
    system_message_attr = "_system_message"
    return cast("SystemMessage | None", getattr(scope, system_message_attr))


# MARK: - Agent tests


def test_set_system_message_normalizes_str_for_agent() -> None:
    agent = Agent(name="t", client=_make_client(), system_prompt="original")
    assert _resolved_system_message(agent) == SystemMessage(content="original")

    agent.set_system_message("updated")

    assert _resolved_system_message(agent) == SystemMessage(content="updated")


def test_set_system_message_accepts_system_message_instance_for_agent() -> None:
    agent = Agent(name="t", client=_make_client(), system_prompt="original")
    replacement = SystemMessage(content="explicit")

    agent.set_system_message(replacement)

    assert _resolved_system_message(agent) is replacement


def test_set_system_message_none_clears_agent() -> None:
    agent = Agent(name="t", client=_make_client(), system_prompt="original")
    assert _resolved_system_message(agent) is not None

    agent.set_system_message(None)

    assert _resolved_system_message(agent) is None


# MARK: - Swarm tests


def test_set_system_message_normalizes_str_for_swarm() -> None:
    swarm = Swarm(name="s", system_prompt="original")
    assert _resolved_system_message(swarm) == SystemMessage(content="original")

    swarm.set_system_message("updated")

    assert _resolved_system_message(swarm) == SystemMessage(content="updated")


def test_set_system_message_none_clears_swarm() -> None:
    swarm = Swarm(name="s", system_prompt="original")
    assert _resolved_system_message(swarm) is not None

    swarm.set_system_message(None)

    assert _resolved_system_message(swarm) is None
