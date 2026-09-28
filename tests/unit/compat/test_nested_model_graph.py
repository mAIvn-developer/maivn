"""Regression tests for nested Pydantic model graph compilation."""

from __future__ import annotations

from typing import TYPE_CHECKING, cast

from pydantic import BaseModel

from maivn import Agent
from maivn._internal.compat.decorators import TOOL_DEPENDENCIES_ATTR

if TYPE_CHECKING:
    from maivn._internal.compat.decorators import ToolDependency


def test_compile_tools_discovers_union_and_container_models_without_false_edges() -> None:
    """Composite annotations stay model-authored while every referenced model is registered."""
    agent = Agent(
        name='composite-model-agent',
        api_key='test-key',
        base_url='http://testserver',
    )

    class AlphaComponent(BaseModel):
        alpha: str

    class BetaComponent(BaseModel):
        beta: int

    class ComponentSet(BaseModel):
        selected: AlphaComponent | BetaComponent
        optional: AlphaComponent | None = None
        sequence: list[AlphaComponent]
        mapping: dict[str, AlphaComponent | BetaComponent]

    @agent.toolify(final_tool=True)
    class CompositeResult(BaseModel):
        components: ComponentSet

    _ = CompositeResult
    compiled = agent.compile_tools()

    assert [tool.name for tool in compiled] == [
        'AlphaComponent',
        'BetaComponent',
        'ComponentSet',
        'CompositeResult',
    ]
    assert not hasattr(AlphaComponent, TOOL_DEPENDENCIES_ATTR)
    assert not hasattr(BetaComponent, TOOL_DEPENDENCIES_ATTR)
    assert not hasattr(ComponentSet, TOOL_DEPENDENCIES_ATTR)
    final_dependencies = cast(
        'list[ToolDependency]', getattr(CompositeResult, TOOL_DEPENDENCIES_ATTR)
    )
    assert [(item.arg_name, item.tool_name) for item in final_dependencies] == [
        ('components', 'ComponentSet')
    ]


def test_reused_direct_model_type_stays_model_authored_instead_of_one_shared_result() -> None:
    """One constructor result cannot stand in for several distinct nested values.

    PARALLEL-BENCHMARK-NOTE(2026-08-16): Complex Types uses ``Motor`` for many
    different joints. Turning every direct annotation into a dependency collapsed all
    of those values onto one job-ledger node and left RobotLeg/RobotHead permanently
    ``node_not_ready``. Reused types must remain available constructors without false
    dependency injection; genuinely unique direct models still form the useful DAG.
    """
    agent = Agent(
        name='reused-model-agent',
        api_key='test-key',
        base_url='http://testserver',
    )

    class Motor(BaseModel):
        watts: int

    class Leg(BaseModel):
        knee: Motor
        ankle: Motor

    class Head(BaseModel):
        pan: Motor

    class Dimensions(BaseModel):
        height: int

    class Specifications(BaseModel):
        dimensions: Dimensions

    @agent.toolify(final_tool=True)
    class Robot(BaseModel):
        leg: Leg
        head: Head
        specifications: Specifications

    _ = Robot
    compiled = agent.compile_tools()

    assert [tool.name for tool in compiled] == [
        'Motor',
        'Leg',
        'Head',
        'Dimensions',
        'Specifications',
        'Robot',
    ]
    assert not hasattr(Leg, TOOL_DEPENDENCIES_ATTR)
    assert not hasattr(Head, TOOL_DEPENDENCIES_ATTR)
    specifications_dependencies = cast(
        'list[ToolDependency]', getattr(Specifications, TOOL_DEPENDENCIES_ATTR)
    )
    assert [(item.arg_name, item.tool_name) for item in specifications_dependencies] == [
        ('dimensions', 'Dimensions')
    ]
