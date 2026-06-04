"""Swarm member agent dependency helpers."""

# pyright: strict
from __future__ import annotations

import inspect
import json
from collections.abc import Callable, Mapping, Sequence
from typing import Any, Final, Protocol, TypeAlias, TypeGuard, cast

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
from pydantic import JsonValue

from maivn._internal.core.entities.tools import BaseTool

JsonObject: TypeAlias = dict[str, JsonValue]

TEAM_DEPENDENCY_ARG_SCHEMAS_METADATA_KEY = "team_dependency_arg_schemas"
SWARM_AGENT_DEPENDENCY_CONTEXT_METADATA_KEY = "swarm_agent_dependency_context"
SWARM_AGENT_DEPENDENCY_CONTEXT_KEYS_METADATA_KEY = "swarm_agent_dependency_context_keys"
TEAM_DEPENDENCIES_ATTR: Final = "_team_dependencies"
TEAM_EXECUTION_CONTROLS_ATTR: Final = "_team_execution_controls"


# MARK: Types

TeamExecutionControl = AwaitForDependency | ReevaluateDependency
# Runtime-only inspect.Signature metadata. Static call sites receive concrete kwargs at runtime.
RUNTIME_DEPENDENCY_ANNOTATION: Final[object] = Any


class SignatureWritable(Protocol):
    __signature__: inspect.Signature


class AgentReference(Protocol):
    @property
    def id(self) -> str: ...

    @property
    def name(self) -> str | None: ...


class SwarmScope(Protocol):
    @property
    def agents(self) -> Sequence[AgentReference]: ...

    def list_tools(self) -> Sequence[BaseTool]: ...


TeamControlReference: TypeAlias = str | AgentReference | BaseTool | Callable[..., object]


# MARK: Agent Metadata Access


def add_team_dependency(agent: AgentReference, dependency: BaseDependency) -> None:
    """Attach a dependency to an Agent configured as a Swarm team member."""
    if isinstance(dependency, DataDependency):
        raise ValueError(
            "depends_on_private_data is not supported for Swarm member agents. "
            + "Use depends_on_private_data on a Swarm-level tool, then make the agent "
            + "depend on that tool."
        )
    dependencies = get_team_dependencies(agent)
    if not _contains_dependency(dependencies, dependency):
        dependencies.append(dependency)
    setattr(agent, TEAM_DEPENDENCIES_ATTR, dependencies)


def add_team_execution_control(agent: AgentReference, control: TeamExecutionControl) -> None:
    """Attach an execution control to an Agent configured as a Swarm team member."""
    controls = get_team_execution_controls(agent)
    if not _contains_execution_control(controls, control):
        controls.append(control)
    setattr(agent, TEAM_EXECUTION_CONTROLS_ATTR, controls)


def get_team_dependencies(agent: AgentReference) -> list[BaseDependency]:
    """Return dependencies attached to a Swarm member Agent."""
    return list(cast(list[BaseDependency], getattr(agent, TEAM_DEPENDENCIES_ATTR, []) or []))


def get_team_execution_controls(agent: AgentReference) -> list[TeamExecutionControl]:
    """Return execution controls attached to a Swarm member Agent."""
    return list(
        cast(list[TeamExecutionControl], getattr(agent, TEAM_EXECUTION_CONTROLS_ATTR, []) or [])
    )


def build_execution_controls_metadata(
    controls: list[TeamExecutionControl],
) -> dict[str, list[JsonObject]]:
    """Group execution controls into ToolSpec metadata shape."""
    grouped: dict[str, list[JsonObject]] = {}
    for control in controls:
        payload = cast(JsonObject, control.model_dump(mode="json"))
        _ = payload.pop("arg_name", None)
        _ = payload.pop("name", None)
        items = grouped.setdefault(control.dependency_type, [])
        if payload not in items:
            items.append(payload)
    return grouped


# MARK: Schema Helpers


def build_team_dependency_arg_schemas(
    dependencies: list[BaseDependency],
    swarm_scope: SwarmScope,
) -> dict[str, JsonObject]:
    """Build explicit args_schema entries for team dependency context fields."""
    schemas: dict[str, JsonObject] = {}
    for dependency in dependencies:
        arg_name = dependency.arg_name
        if not arg_name:
            continue
        schema = _build_dependency_schema(dependency, swarm_scope)
        if schema:
            schemas[arg_name] = schema
    return schemas


def apply_team_dependency_arg_schemas(
    args_schema: ArgsSchema,
    metadata: Mapping[str, object],
) -> None:
    """Apply generated team dependency schemas to an AgentTool args schema."""
    if not isinstance(args_schema, dict):
        return

    schemas = metadata.get(TEAM_DEPENDENCY_ARG_SCHEMAS_METADATA_KEY)
    if not isinstance(schemas, dict):
        return

    properties_obj = args_schema.setdefault("properties", {})
    if not isinstance(properties_obj, dict):
        return
    properties = cast(JsonObject, properties_obj)

    required_obj = args_schema.setdefault("required", [])
    if not isinstance(required_obj, list):
        required_obj = []
        args_schema["required"] = required_obj
    required = cast(list[JsonValue], required_obj)

    for arg_name, schema in cast(Mapping[object, object], schemas).items():
        if not isinstance(arg_name, str) or not isinstance(schema, dict):
            continue
        properties[arg_name] = cast(JsonValue, schema)
        if arg_name not in required:
            required.append(arg_name)


def _build_dependency_schema(
    dependency: BaseDependency,
    swarm_scope: SwarmScope,
) -> JsonObject | None:
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
            tool_id=tool.tool_id,
            tool_name=_get_required_name(tool, "tool"),
            tool_type=_get_tool_type(tool),
        )

    if isinstance(dependency, InterruptDependency):
        data_key = _optional_str_attr(dependency, "data_key") or dependency.arg_name
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
) -> JsonObject:
    return {
        "type": "tool_dependency",
        "tool_id": tool_id,
        "tool_name": tool_name,
        "tool_type": tool_type,
        "description": f"Output from {tool_name}",
        "output_type": "object",
    }


# MARK: Resolution Helpers


def resolve_swarm_tool(swarm_scope: SwarmScope, tool_ref: str) -> BaseTool:
    """Resolve a tool from the Swarm's own tool registry by id or name."""
    for tool in swarm_scope.list_tools():
        if tool.tool_id == tool_ref or tool.name == tool_ref:
            return tool
    raise ValueError(
        f"Swarm member agent depends on Swarm-level tool '{tool_ref}', but that tool is "
        + "not registered on the Swarm."
    )


def resolve_swarm_agent(swarm_scope: SwarmScope, agent_ref: str) -> AgentReference:
    """Resolve a Swarm member agent by id or name."""
    for agent in swarm_scope.agents:
        if agent.id == agent_ref or agent.name == agent_ref:
            return agent
    raise ValueError(
        f"Swarm member agent depends on Swarm member agent '{agent_ref}', but that agent "
        + "is not registered on the Swarm."
    )


def resolve_team_control_reference(
    swarm_scope: SwarmScope,
    ref: TeamControlReference,
) -> tuple[str, str]:
    """Resolve an agent or Swarm tool reference to an invocation control target."""
    if _looks_like_agent(ref):
        agent_id = ref.id
        agent_name = _get_required_name(ref, "agent")
        return create_uuid(f"agent_invoke_{agent_id}"), agent_name

    if isinstance(ref, str):
        matching_agents = [
            agent for agent in swarm_scope.agents if agent.id == ref or agent.name == ref
        ]
        matching_tools = [
            tool for tool in swarm_scope.list_tools() if tool.tool_id == ref or tool.name == ref
        ]
        if matching_agents and matching_tools:
            raise ValueError(
                f"Reference '{ref}' matches both a Swarm agent and a Swarm tool. "
                + "Pass the concrete Agent or tool object to disambiguate."
            )
        if matching_agents:
            agent = matching_agents[0]
            return create_uuid(f"agent_invoke_{agent.id}"), _get_required_name(agent, "agent")
        if matching_tools:
            tool = matching_tools[0]
            return tool.tool_id, _get_required_name(tool, "tool")

    tool_id_reference = _resolve_tool_id_reference(ref)
    if tool_id_reference is not None:
        return tool_id_reference

    if callable(ref):
        name = _callable_name(ref)
        return create_uuid(ref), name

    raise ValueError(f"Unable to resolve team dependency reference: {ref!r}")


def _looks_like_agent(value: TeamControlReference) -> TypeGuard[AgentReference]:
    has_id = _optional_str_attr(value, "id") is not None
    has_tool_id = _optional_str_attr(value, "tool_id") is not None
    return has_id and not has_tool_id


def _get_required_name(value: AgentReference | BaseTool, label: str) -> str:
    name = value.name
    if name:
        return name
    raise ValueError(f"Swarm member {label} dependencies require named {label}s.")


def _get_tool_type(tool: BaseTool) -> str:
    return str(cast(object, getattr(tool, "tool_type", "func")))


def _resolve_tool_id_reference(value: object) -> tuple[str, str] | None:
    tool_id = _optional_str_attr(value, "tool_id")
    if tool_id is None:
        return None
    name = _optional_str_attr(value, "name") or _optional_str_attr(value, "__name__") or tool_id
    return tool_id, name


def _optional_str_attr(value: object, attr: str) -> str | None:
    attr_value = cast(object | None, getattr(value, attr, None))
    return attr_value if isinstance(attr_value, str) and attr_value else None


def _callable_name(func: Callable[..., object]) -> str:
    name = cast(object, getattr(func, "__name__", ""))
    return name if isinstance(name, str) else ""


# MARK: Invocation Helpers


def apply_team_invocation_signature(
    func: Callable[..., object],
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
            + f"invocation parameter(s): {conflicts}"
        )

    dependency_params = [
        inspect.Parameter(
            name,
            inspect.Parameter.KEYWORD_ONLY,
            default=None,
            annotation=RUNTIME_DEPENDENCY_ANNOTATION,
        )
        for name in arg_names
    ]
    signature_target = cast(SignatureWritable, func)
    signature_target.__signature__ = signature.replace(
        parameters=[*base_params, *dependency_params]
    )


def build_team_dependency_context(
    raw_kwargs: Mapping[str, object],
    dependencies: list[BaseDependency],
) -> dict[str, object]:
    """Extract dependency context values from generated AgentTool kwargs."""
    context: dict[str, object] = {}
    for arg_name in _dependency_arg_names(dependencies):
        if arg_name in raw_kwargs and raw_kwargs[arg_name] is not None:
            context[arg_name] = to_jsonable(raw_kwargs[arg_name])
    return context


def format_dependency_context_for_prompt(
    prompt: str,
    dependency_context: Mapping[str, object],
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
        arg_name = dependency.arg_name
        if arg_name and arg_name not in names:
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
