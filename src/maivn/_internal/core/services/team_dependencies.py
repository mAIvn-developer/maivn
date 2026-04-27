"""Swarm member agent dependency helpers."""

from __future__ import annotations

import inspect
import json
from typing import Any

from maivn_shared import (
    AgentDependency,
    ArgsSchema,
    BaseDependency,
    DataDependency,
    InterruptDependency,
    ToolDependency,
    create_uuid,
    to_jsonable,
)
from maivn_shared.domain.entities.dependencies import AwaitForDependency, ReevaluateDependency

TEAM_DEPENDENCY_ARG_SCHEMAS_METADATA_KEY = "team_dependency_arg_schemas"
SWARM_AGENT_DEPENDENCY_CONTEXT_METADATA_KEY = "swarm_agent_dependency_context"
SWARM_AGENT_DEPENDENCY_CONTEXT_KEYS_METADATA_KEY = "swarm_agent_dependency_context_keys"

TeamExecutionControl = AwaitForDependency | ReevaluateDependency


# MARK: Agent Metadata Access


def add_team_dependency(agent: Any, dependency: BaseDependency) -> None:
    """Attach a dependency to an Agent configured as a Swarm team member."""
    if isinstance(dependency, DataDependency):
        raise ValueError(
            "depends_on_private_data is not supported for Swarm member agents. "
            "Use depends_on_private_data on a Swarm-level tool, then make the agent "
            "depend on that tool."
        )
    dependencies = list(getattr(agent, "_team_dependencies", []) or [])
    if not _contains_dependency(dependencies, dependency):
        dependencies.append(dependency)
    agent._team_dependencies = dependencies


def add_team_execution_control(agent: Any, control: TeamExecutionControl) -> None:
    """Attach an execution control to an Agent configured as a Swarm team member."""
    controls = list(getattr(agent, "_team_execution_controls", []) or [])
    if not _contains_execution_control(controls, control):
        controls.append(control)
    agent._team_execution_controls = controls


def get_team_dependencies(agent: Any) -> list[BaseDependency]:
    """Return dependencies attached to a Swarm member Agent."""
    return list(getattr(agent, "_team_dependencies", []) or [])


def get_team_execution_controls(agent: Any) -> list[TeamExecutionControl]:
    """Return execution controls attached to a Swarm member Agent."""
    return list(getattr(agent, "_team_execution_controls", []) or [])


def build_execution_controls_metadata(
    controls: list[TeamExecutionControl],
) -> dict[str, list[dict[str, Any]]]:
    """Group execution controls into ToolSpec metadata shape."""
    grouped: dict[str, list[dict[str, Any]]] = {}
    for control in controls:
        payload = control.model_dump(mode="json")
        payload.pop("arg_name", None)
        payload.pop("name", None)
        items = grouped.setdefault(control.dependency_type, [])
        if payload not in items:
            items.append(payload)
    return grouped


# MARK: Schema Helpers


def build_team_dependency_arg_schemas(
    dependencies: list[BaseDependency],
    swarm_scope: Any,
) -> dict[str, dict[str, Any]]:
    """Build explicit args_schema entries for team dependency context fields."""
    schemas: dict[str, dict[str, Any]] = {}
    for dependency in dependencies:
        arg_name = getattr(dependency, "arg_name", "")
        if not isinstance(arg_name, str) or not arg_name:
            continue
        schema = _build_dependency_schema(dependency, swarm_scope)
        if schema:
            schemas[arg_name] = schema
    return schemas


def apply_team_dependency_arg_schemas(
    args_schema: ArgsSchema,
    metadata: dict[str, Any],
) -> None:
    """Apply generated team dependency schemas to an AgentTool args schema."""
    if not isinstance(args_schema, dict):
        return

    schemas = metadata.get(TEAM_DEPENDENCY_ARG_SCHEMAS_METADATA_KEY)
    if not isinstance(schemas, dict):
        return

    properties = args_schema.setdefault("properties", {})
    if not isinstance(properties, dict):
        return

    required = args_schema.setdefault("required", [])
    if not isinstance(required, list):
        required = []
        args_schema["required"] = required

    for arg_name, schema in schemas.items():
        if not isinstance(arg_name, str) or not isinstance(schema, dict):
            continue
        properties[arg_name] = schema
        if arg_name not in required:
            required.append(arg_name)


def _build_dependency_schema(
    dependency: BaseDependency,
    swarm_scope: Any,
) -> dict[str, Any] | None:
    if isinstance(dependency, AgentDependency):
        agent = resolve_swarm_agent(swarm_scope, dependency.agent_id)
        return _create_tool_dependency_schema(
            tool_id=create_uuid(f"agent_invoke_{agent.id}"),
            tool_name=_get_required_name(agent, "agent"),
            tool_type="agent",
        )

    if isinstance(dependency, ToolDependency):
        tool = resolve_swarm_tool(swarm_scope, dependency.tool_id)
        return _create_tool_dependency_schema(
            tool_id=getattr(tool, "tool_id", dependency.tool_id),
            tool_name=_get_required_name(tool, "tool"),
            tool_type=str(getattr(tool, "tool_type", "func")),
        )

    if isinstance(dependency, InterruptDependency):
        data_key = getattr(dependency, "data_key", None) or dependency.arg_name
        return {
            "type": "interrupt_dependency",
            "interrupt_id": create_uuid(f"interrupt_team_agent_{dependency.arg_name}"),
            "prompt": dependency.prompt,
            "data_key": data_key,
            "description": f"User input: {dependency.prompt}",
        }

    return None


def _create_tool_dependency_schema(
    *,
    tool_id: str,
    tool_name: str,
    tool_type: str,
) -> dict[str, Any]:
    return {
        "type": "tool_dependency",
        "tool_id": tool_id,
        "tool_name": tool_name,
        "tool_type": tool_type,
        "description": f"Output from {tool_name}",
        "output_type": "object",
    }


# MARK: Resolution Helpers


def resolve_swarm_tool(swarm_scope: Any, tool_ref: str) -> Any:
    """Resolve a tool from the Swarm's own tool registry by id or name."""
    for tool in swarm_scope.list_tools():
        if getattr(tool, "tool_id", None) == tool_ref or getattr(tool, "name", None) == tool_ref:
            return tool
    raise ValueError(
        f"Swarm member agent depends on Swarm-level tool '{tool_ref}', but that tool is "
        "not registered on the Swarm."
    )


def resolve_swarm_agent(swarm_scope: Any, agent_ref: str) -> Any:
    """Resolve a Swarm member agent by id or name."""
    for agent in getattr(swarm_scope, "agents", []) or []:
        if getattr(agent, "id", None) == agent_ref or getattr(agent, "name", None) == agent_ref:
            return agent
    raise ValueError(
        f"Swarm member agent depends on Swarm member agent '{agent_ref}', but that agent "
        "is not registered on the Swarm."
    )


def resolve_team_control_reference(swarm_scope: Any, ref: Any) -> tuple[str, str]:
    """Resolve an agent or Swarm tool reference to an invocation control target."""
    if _looks_like_agent(ref):
        agent_id = ref.id
        agent_name = _get_required_name(ref, "agent")
        return create_uuid(f"agent_invoke_{agent_id}"), agent_name

    if isinstance(ref, str):
        matching_agents = [
            agent
            for agent in getattr(swarm_scope, "agents", []) or []
            if getattr(agent, "id", None) == ref or getattr(agent, "name", None) == ref
        ]
        matching_tools = [
            tool
            for tool in swarm_scope.list_tools()
            if getattr(tool, "tool_id", None) == ref or getattr(tool, "name", None) == ref
        ]
        if matching_agents and matching_tools:
            raise ValueError(
                f"Reference '{ref}' matches both a Swarm agent and a Swarm tool. "
                "Pass the concrete Agent or tool object to disambiguate."
            )
        if matching_agents:
            agent = matching_agents[0]
            return create_uuid(f"agent_invoke_{agent.id}"), _get_required_name(agent, "agent")
        if matching_tools:
            tool = matching_tools[0]
            return getattr(tool, "tool_id", ref), _get_required_name(tool, "tool")

    tool_id = getattr(ref, "tool_id", None)
    if isinstance(tool_id, str) and tool_id:
        name = getattr(ref, "name", None) or getattr(ref, "__name__", tool_id)
        return tool_id, str(name)

    if callable(ref):
        name = getattr(ref, "__name__", "")
        return create_uuid(ref), name

    raise ValueError(f"Unable to resolve team dependency reference: {ref!r}")


def _looks_like_agent(value: Any) -> bool:
    return hasattr(value, "agent_id") and hasattr(value, "list_tools")


def _get_required_name(value: Any, label: str) -> str:
    name = getattr(value, "name", None)
    if isinstance(name, str) and name:
        return name
    raise ValueError(f"Swarm member {label} dependencies require named {label}s.")


# MARK: Invocation Helpers


def apply_team_invocation_signature(
    func: Any,
    dependencies: list[BaseDependency],
) -> None:
    """Expose team dependency args in the generated AgentTool signature."""
    arg_names = _dependency_arg_names(dependencies)
    if not arg_names:
        return

    signature = inspect.signature(func)
    base_params = [
        param
        for param in signature.parameters.values()
        if param.kind != inspect.Parameter.VAR_KEYWORD
    ]
    existing = {param.name for param in base_params}
    conflicts = sorted(existing.intersection(arg_names))
    if conflicts:
        raise ValueError(
            "Swarm member agent dependency arg_name conflicts with reserved agent "
            f"invocation parameter(s): {conflicts}"
        )

    dependency_params = [
        inspect.Parameter(
            name,
            inspect.Parameter.KEYWORD_ONLY,
            default=None,
            annotation=Any,
        )
        for name in arg_names
    ]
    func.__signature__ = signature.replace(parameters=[*base_params, *dependency_params])


def build_team_dependency_context(
    raw_kwargs: dict[str, Any],
    dependencies: list[BaseDependency],
) -> dict[str, Any]:
    """Extract dependency context values from generated AgentTool kwargs."""
    context: dict[str, Any] = {}
    for arg_name in _dependency_arg_names(dependencies):
        if arg_name in raw_kwargs and raw_kwargs[arg_name] is not None:
            context[arg_name] = to_jsonable(raw_kwargs[arg_name])
    return context


def format_dependency_context_for_prompt(
    prompt: str,
    dependency_context: dict[str, Any],
) -> str:
    """Append deterministic dependency context to the nested agent prompt."""
    if not dependency_context:
        return prompt

    serialized = json.dumps(
        to_jsonable(dependency_context),
        indent=2,
        sort_keys=True,
        default=str,
    )
    return f"{prompt}\n\nDependency context:\n{serialized}"


def _dependency_arg_names(dependencies: list[BaseDependency]) -> list[str]:
    names: list[str] = []
    for dependency in dependencies:
        arg_name = getattr(dependency, "arg_name", None)
        if isinstance(arg_name, str) and arg_name and arg_name not in names:
            names.append(arg_name)
    return names


# MARK: Comparison Helpers


def _contains_dependency(items: list[BaseDependency], dependency: BaseDependency) -> bool:
    candidate = dependency.model_dump(mode="json")
    return any(item.model_dump(mode="json") == candidate for item in items)


def _contains_execution_control(
    items: list[TeamExecutionControl],
    control: TeamExecutionControl,
) -> bool:
    candidate = control.model_dump(mode="json")
    return any(item.model_dump(mode="json") == candidate for item in items)


__all__ = [
    "SWARM_AGENT_DEPENDENCY_CONTEXT_KEYS_METADATA_KEY",
    "SWARM_AGENT_DEPENDENCY_CONTEXT_METADATA_KEY",
    "TEAM_DEPENDENCY_ARG_SCHEMAS_METADATA_KEY",
    "TeamExecutionControl",
    "add_team_dependency",
    "add_team_execution_control",
    "apply_team_dependency_arg_schemas",
    "apply_team_invocation_signature",
    "build_execution_controls_metadata",
    "build_team_dependency_arg_schemas",
    "build_team_dependency_context",
    "format_dependency_context_for_prompt",
    "get_team_dependencies",
    "get_team_execution_controls",
    "resolve_swarm_agent",
    "resolve_swarm_tool",
    "resolve_team_control_reference",
]
