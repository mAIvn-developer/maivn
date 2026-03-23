from __future__ import annotations

import pytest  # pyright: ignore[reportMissingImports]

from maivn import Agent, Swarm


def _make_agent(name: str, *, final: bool = False) -> Agent:
    return Agent(
        name=name,
        description=f"{name} agent",
        api_key="mock_api_key",
        use_as_final_output=final,
    )


def test_swarm_rejects_multiple_final_output_agents() -> None:
    swarm = Swarm(
        name="swarm",
        agents=[_make_agent("a1", final=True), _make_agent("a2", final=True)],
    )

    with pytest.raises(ValueError) as exc:
        swarm.validate_on_invoke()

    assert "use_as_final_output" in str(exc.value)


def test_swarm_allows_single_final_output_agent() -> None:
    swarm = Swarm(
        name="swarm",
        agents=[_make_agent("a1", final=True), _make_agent("a2", final=False)],
    )

    swarm.validate_on_invoke()
