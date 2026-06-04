# pyright: strict
"""SDK roster builder surfaces ``@depends_on_agent`` targets per agent.

Regression: when an agent's tool was decorated with ``@depends_on_agent``
targeting another roster member, the orchestrator had no way to know that
running the parent agent would already invoke the dependency target. It
would then schedule the dependency target as a redundant separate stage.

These tests pin the contract: the SDK roster entry for the parent agent
exposes ``invokes_via_dependency: ["TargetName"]`` so the orchestrator can
reason about the implicit invocation when planning.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import cast

from pydantic import BaseModel, Field

from maivn import Agent, Swarm, depends_on_agent
from maivn._internal.api.client import Client
from maivn._internal.utils.configuration import MaivnConfiguration, ServerConfiguration


def _make_client() -> Client:
    config = MaivnConfiguration(
        server=ServerConfiguration(
            base_url="http://example.com",
            mock_base_url="http://example.com",
        )
    )
    return Client.from_configuration(api_key="key", configuration=config)


def _build_invocation_tool_map(swarm: Swarm) -> dict[str, str]:
    """Test access to a Swarm protected helper via cast-through-getattr.

    The Swarm class keeps these helpers underscore-prefixed; tests pin their
    contract via this typed accessor so we do not need a ``# pyright: ignore``
    or src-side rename to expose them.
    """
    tool_map_attr = "_build_invocation_tool_map"
    fn = cast("Callable[[], dict[str, str]]", getattr(swarm, tool_map_attr))
    return fn()


def _build_agent_roster_entry(
    swarm: Swarm, agent: Agent, tool_map: dict[str, str]
) -> dict[str, object]:
    """Test access to ``Swarm._build_agent_roster_entry`` (see helper above)."""
    roster_entry_attr = "_build_agent_roster_entry"
    fn = cast(
        "Callable[[Agent, dict[str, str]], dict[str, object]]",
        getattr(swarm, roster_entry_attr),
    )
    return fn(agent, tool_map)


def _agent_with_dep_tool() -> tuple[Agent, Agent]:
    """Build a coordinator whose model tool depends on an analyzer agent."""
    analyzer = Agent(name="Data Analyzer", client=_make_client())

    @analyzer.toolify(name="analyze_dataset", description="Analyze a dataset")
    def analyze_dataset(dataset_name: str) -> dict[str, str]:
        return {"dataset": dataset_name}

    _ = analyze_dataset  # registered via decorator side effect

    coordinator = Agent(name="Research Coordinator", client=_make_client())

    @depends_on_agent(analyzer, arg_name="analysis_result")
    @coordinator.toolify(name="generate_research_report", description="Build a report")
    class ResearchReport(BaseModel):
        """Research report combining analysis and conclusions."""

        title: str = Field(..., description="Report title")
        analysis_result: dict[str, object] = Field(..., description="Analysis result")

    _ = ResearchReport  # registered via decorator side effect

    return coordinator, analyzer


def test_roster_entry_lists_dependency_target_for_coordinator() -> None:
    coordinator, analyzer = _agent_with_dep_tool()
    swarm = Swarm(name="swarm", agents=[coordinator, analyzer])

    tool_map = _build_invocation_tool_map(swarm)
    coordinator_entry = _build_agent_roster_entry(swarm, coordinator, tool_map)

    assert coordinator_entry.get("invokes_via_dependency") == ["Data Analyzer"]


def test_roster_entry_omits_field_for_agents_without_dependencies() -> None:
    _, analyzer = _agent_with_dep_tool()
    swarm = Swarm(name="swarm", agents=[analyzer])

    tool_map = _build_invocation_tool_map(swarm)
    analyzer_entry = _build_agent_roster_entry(swarm, analyzer, tool_map)

    # `model_dump(exclude_none=True)` keeps default lists; confirm the field
    # is absent or empty so the orchestrator prompt doesn't render it.
    assert not analyzer_entry.get("invokes_via_dependency")


def test_roster_entry_drops_targets_outside_swarm_roster() -> None:
    """Out-of-roster dependency targets aren't actionable for the orchestrator,
    so they are filtered out before the hint is surfaced."""
    out_of_roster_agent = Agent(name="Outside", client=_make_client())
    coordinator = Agent(name="Coordinator", client=_make_client())

    @depends_on_agent(out_of_roster_agent, arg_name="external_input")
    @coordinator.toolify(name="orchestrate", description="Combine external input")
    class OrchestrateOutput(BaseModel):
        """Output combining external input."""

        external_input: dict[str, object] = Field(..., description="External input")

    _ = OrchestrateOutput  # registered via decorator side effect

    swarm = Swarm(name="swarm", agents=[coordinator])
    tool_map = _build_invocation_tool_map(swarm)
    entry = _build_agent_roster_entry(swarm, coordinator, tool_map)

    assert not entry.get("invokes_via_dependency")
