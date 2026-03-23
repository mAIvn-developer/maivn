from __future__ import annotations

from typing import Literal

import pytest
from maivn_shared import create_uuid

from maivn._internal.utils.decorators import (
    compose_artifact_policy,
    depends_on_agent,
    depends_on_await_for,
    depends_on_interrupt,
    depends_on_private_data,
    depends_on_reevaluate,
    depends_on_tool,
)


def test_depends_on_interrupt_detects_literal_choices() -> None:
    def handler(prompt: str) -> str:
        return "ok"

    @depends_on_interrupt("choice", handler)
    def tool(choice: Literal["a", "b"]) -> str:
        return choice

    deps = getattr(tool, "_dependencies", [])
    assert deps
    dep = deps[0]
    assert dep.input_type == "choice"
    assert dep.choices == ["a", "b"]


def test_depends_on_interrupt_detects_boolean() -> None:
    def handler(prompt: str) -> bool:
        return True

    @depends_on_interrupt("flag", handler)
    def tool(flag: bool) -> bool:
        return flag

    dep = getattr(tool, "_dependencies", [])[0]
    assert dep.input_type == "boolean"


def test_depends_on_tool_uses_callable_uuid() -> None:
    def helper() -> str:
        return "ok"

    @depends_on_tool(helper, "result")
    def tool(result: str) -> str:
        return result

    dep = getattr(tool, "_dependencies", [])[0]
    assert dep.tool_id == create_uuid(helper)
    assert dep.dependency_type == "tool"


def test_depends_on_agent_uses_agent_id_attribute() -> None:
    class AgentRef:
        agent_id = "agent-123"

    @depends_on_agent(AgentRef(), "agent_output")
    def tool(agent_output: str) -> str:
        return agent_output

    dep = getattr(tool, "_dependencies", [])[0]
    assert dep.agent_id == "agent-123"
    assert dep.dependency_type == "agent"


def test_execution_control_decorators_attach_metadata_only_controls() -> None:
    def helper() -> str:
        return "ok"

    @depends_on_reevaluate(helper, timing="before", instance_control="all")
    @depends_on_await_for(helper, timing="after", instance_control="each")
    def tool() -> str:
        return "done"

    controls = getattr(tool, "__maivn_execution_controls__", [])
    assert len(controls) == 2
    assert getattr(tool, "_dependencies", []) == []

    await_for_control = next(
        control for control in controls if control.dependency_type == "await_for"
    )
    reevaluate_control = next(
        control for control in controls if control.dependency_type == "reevaluate"
    )

    assert await_for_control.tool_id == create_uuid(helper)
    assert await_for_control.tool_name == "helper"
    assert await_for_control.timing == "after"
    assert await_for_control.instance_control == "each"

    assert reevaluate_control.tool_id == create_uuid(helper)
    assert reevaluate_control.tool_name == "helper"
    assert reevaluate_control.timing == "before"
    assert reevaluate_control.instance_control == "all"


def test_compose_artifact_policy_attaches_arg_policy_metadata() -> None:
    @compose_artifact_policy("query", mode="require", approval="explicit")
    def tool(query: str) -> str:
        return query

    policies = getattr(tool, "__maivn_arg_policies__", [])
    assert policies == [
        {
            "arg_name": "query",
            "policy": "compose_artifact",
            "mode": "require",
            "approval": "explicit",
        }
    ]


def test_dependency_decorators_validate_arg_name() -> None:
    with pytest.raises(ValueError):

        @depends_on_private_data("key", "missing")
        def tool(existing: str) -> str:
            return existing

    with pytest.raises(ValueError):

        @compose_artifact_policy("missing")
        def other_tool(existing: str) -> str:
            return existing
