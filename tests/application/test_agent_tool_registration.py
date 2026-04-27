from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field

from maivn._internal.api.agent import Agent
from maivn._internal.api.client import Client
from maivn._internal.core.entities.tools import BaseTool, FunctionTool
from maivn._internal.utils.configuration import MaivnConfiguration, ServerConfiguration
from maivn._internal.utils.decorators import depends_on_tool


def _make_client() -> Client:
    config = MaivnConfiguration(
        server=ServerConfiguration(
            base_url="http://example.com",
            mock_base_url="http://example.com",
        )
    )
    return Client.from_configuration(api_key="key", configuration=config)


def test_agent_constructor_registers_callable_and_model_tools() -> None:
    def load_profile(customer_id: str) -> dict[str, Any]:
        """Load a customer support profile."""
        return {"customer_id": customer_id, "tier": "enterprise"}

    @depends_on_tool(load_profile, arg_name="profile")
    def build_recommendation(profile: dict[str, Any]) -> dict[str, Any]:
        """Build a support recommendation from a customer profile."""
        return {"recommendation": f"Route {profile['tier']} account to senior support"}

    class SupportSummary(BaseModel):
        """Write the final customer support summary."""

        customer_id: str = Field(..., description="Customer identifier")
        recommendation: str = Field(..., description="Recommended support action")

    agent = Agent(
        name="support-agent",
        client=_make_client(),
        tools=[load_profile, build_recommendation, SupportSummary],
    )

    tools_by_name = {tool.name: tool for tool in agent.list_tools()}

    assert set(tools_by_name) == {"load_profile", "build_recommendation", "SupportSummary"}
    assert all(isinstance(tool, BaseTool) for tool in agent.tools)
    assert load_profile.tool_id == tools_by_name["load_profile"].tool_id

    recommendation_tool = tools_by_name["build_recommendation"]
    assert len(recommendation_tool.dependencies) == 1
    assert recommendation_tool.dependencies[0].tool_id == tools_by_name["load_profile"].tool_id


def test_agent_add_tool_registers_callable_with_options() -> None:
    def classify_ticket(subject: str) -> dict[str, str]:
        """Classify a support ticket subject."""
        return {"subject": subject, "priority": "high"}

    agent = Agent(name="support-agent", client=_make_client())

    tool = agent.add_tool(
        classify_ticket,
        name="classify_support_ticket",
        description="Classify support tickets by priority.",
        tags=["support", "triage"],
    )

    assert agent.get_tool(tool.tool_id) is tool
    assert agent.tools == [tool]
    assert tool.name == "classify_support_ticket"
    assert tool.description == "Classify support tickets by priority."
    assert tool.tags == ["support", "triage"]
    assert classify_ticket.tool_id == tool.tool_id


def test_agent_add_tool_registers_model_as_final_tool() -> None:
    class ResolutionPlan(BaseModel):
        """Write a final support resolution plan."""

        steps: list[str]
        owner: str

    agent = Agent(name="support-agent", client=_make_client())

    tool = agent.add_tool(ResolutionPlan, name="resolution_plan", final_tool=True)

    assert agent.get_tool(tool.tool_id) is tool
    assert tool.name == "resolution_plan"
    assert tool.final_tool is True


def test_agent_constructor_accepts_prebuilt_tool_objects() -> None:
    def lookup_status(order_id: str) -> dict[str, str]:
        """Lookup an order status."""
        return {"order_id": order_id, "status": "in_transit"}

    prebuilt_tool = FunctionTool(
        name="lookup_status",
        description="Lookup an order status.",
        func=lookup_status,
    )

    agent = Agent(name="order-agent", client=_make_client(), tools=[prebuilt_tool])

    assert agent.list_tools() == [prebuilt_tool]
    assert agent.tools == [prebuilt_tool]
    assert lookup_status.tool_id == prebuilt_tool.tool_id
