from __future__ import annotations

from maivn._internal.core import exceptions


def test_exception_messages() -> None:
    error = exceptions.ToolExecutionError(tool_id="tool", reason="boom")
    assert "tool" in str(error)

    missing = exceptions.ToolNotFoundError(tool_id="missing", available_tools=["a", "b"])
    assert "missing" in str(missing)
    assert "a" in str(missing)

    validation = exceptions.ArgumentValidationError(
        tool_name="tool",
        expected_params=["a", "b"],
        provided_params=["a"],
        details="bad",
    )
    assert "Missing" in str(validation)

    agent_error = exceptions.AgentNotFoundError(agent_id="agent")
    assert "agent" in str(agent_error)

    tool_dep_error = exceptions.ToolDependencyNotFoundError(tool_id="tool")
    assert "Tool result not found" in str(tool_dep_error)

    state_error = exceptions.StateCompilationError(reason="state", context={"k": "v"})
    assert "state" in str(state_error)
    assert "k=v" in str(state_error)

    dyn_error = exceptions.DynamicToolCreationError(tool_type="agent", target_id="id", reason="x")
    assert "agent" in str(dyn_error)

    config_error = exceptions.ConfigurationError(setting="s", issue="bad", suggestion="fix")
    assert "fix" in str(config_error)

    swarm_error = exceptions.SwarmContextError(agent_id="agent")
    assert "agent" in str(swarm_error)

    ser_error = exceptions.SerializationError(data_type="data", operation="serialize", reason="bad")
    assert "serialize" in str(ser_error)

    pyd_error = exceptions.PydanticDeserializationError(
        model_name="Model", reason="bad", field_name="field"
    )
    assert "Model" in str(pyd_error)
