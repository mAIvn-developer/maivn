from __future__ import annotations

from maivn_shared import (
    AgentDependency,
    DataDependency,
    InterruptDependency,
    ToolDependency,
    create_uuid,
)

from maivn._internal.core.tool_specs.dependency_detector import DependencyDetector


def test_dependency_detector_builds_interrupt_schema() -> None:
    detector = DependencyDetector()
    dependency = InterruptDependency(
        arg_name="answer",
        prompt="Provide answer",
        input_handler=lambda prompt: "ok",
    )

    schema = detector.detect_dependency([dependency], "answer", "TestContext")

    assert schema is not None
    assert schema["type"] == "interrupt_dependency"
    assert schema["prompt"] == "Provide answer"
    assert schema["data_key"] == "answer"
    assert schema["interrupt_id"] == create_uuid("interrupt_TestContext_answer")


def test_dependency_detector_builds_data_schema() -> None:
    detector = DependencyDetector()
    dependency = DataDependency(arg_name="payload", data_key="payload")

    schema = detector.detect_dependency([dependency], "payload", "Context")

    assert schema == {
        "type": "data_dependency",
        "data_key": "payload",
        "description": "Data from private_data['payload']",
    }


def test_dependency_detector_builds_agent_and_tool_schemas() -> None:
    detector = DependencyDetector()

    agent_dep = AgentDependency(arg_name="agent", agent_id="agent-1")
    tool_dep = ToolDependency(arg_name="tool", tool_id="tool-1", tool_name="tool-name")

    agent_schema = detector.detect_dependency([agent_dep], "agent", "Ctx")
    tool_schema = detector.detect_dependency([tool_dep], "tool", "Ctx")

    assert agent_schema is not None
    assert agent_schema["type"] == "tool_dependency"
    assert agent_schema["tool_type"] == "agent"
    assert agent_schema["tool_id"] == create_uuid("agent_invoke_agent-1")

    assert tool_schema is not None
    assert tool_schema["type"] == "tool_dependency"
    assert tool_schema["tool_type"] == "func"
    assert tool_schema["tool_id"] == "tool-1"


def test_dependency_detector_builds_model_dependency_with_ref() -> None:
    detector = DependencyDetector()

    schema = detector.build_model_tool_dependency(
        tool_id="model-1",
        model_name="MyModel",
        ref_path="#/$defs/MyModel",
    )

    assert schema["tool_id"] == "model-1"
    assert schema["tool_name"] == "MyModel"
    assert schema["tool_type"] == "model"
    assert schema["original_ref"] == "#/$defs/MyModel"
