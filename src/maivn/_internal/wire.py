"""Wire constants and payload builders."""

from __future__ import annotations

import copy
from collections.abc import Callable, Mapping, Sequence
from inspect import Parameter, signature
from typing import TYPE_CHECKING, cast, get_type_hints
from uuid import uuid4

from pydantic import BaseModel, TypeAdapter

from maivn._internal.compat.decorators import (
    ARGUMENT_PRODUCER_REQUIREMENTS_ATTR,
    COMPOSE_ARGUMENT_POLICIES_ATTR,
    INTERRUPT_DEPENDENCIES_ATTR,
    TOOL_DEPENDENCIES_ATTR,
    AgentDependency,
    ArgumentProducerRequirement,
    ComposeArgumentPolicy,
    InterruptDependency,
    ToolDependency,
)
from maivn._internal.compat.options import SystemToolsConfig
from maivn._internal.config import DEFAULT_TOOL_EXECUTION_TIMEOUT
from maivn.messages import to_contract_messages

if TYPE_CHECKING:
    from maivn._internal.models import JsonObject, RunOptions, ToolMetadata
    from maivn.messages import SdkMessagesInput

_MAX_AGENT_SERIALIZATION_DEPTH = 16

INVOKE_PATH = '/v1/invoke'
ARTIFACTS_PATH = '/v1/artifacts'
ARTIFACT_PATH = '/v1/artifacts/{artifact_id}'
ARTIFACT_DOWNLOAD_PATH = '/v1/artifacts/{artifact_id}/download'
ARTIFACT_PREVIEW_PATH = '/v1/artifacts/{artifact_id}/revisions/{revision}/preview'
SESSION_EVENTS_PATH = '/v1/sessions/{session_id}/events'
SESSION_CANCEL_PATH = '/v1/sessions/{session_id}/cancel'
SESSION_USAGE_PATH = '/v1/sessions/{session_id}/usage'
TOOL_RESULT_PATH = '/v1/sessions/{session_id}/tools/{call_id}/result'
TOOL_RESULT_BATCH_PATH = '/v1/sessions/{session_id}/tools/results'
THREADS_PATH = '/v1/threads'
THREAD_MESSAGES_PATH = '/v1/threads/{thread_id}/messages'
THREAD_TIME_TRAVEL_PATH = '/v1/threads/{thread_id}/time-travel'
THREAD_EVENTS_PATH = '/v1/threads/{thread_id}/events'
THREAD_APPROVALS_PATH = '/v1/threads/{thread_id}/approvals/{interrupt_id}'
THREAD_INTERRUPT_RESPONSES_PATH = '/v1/threads/{thread_id}/interrupts/{interrupt_id}/responses'
THREAD_STATE_PATH = '/v1/threads/{thread_id}'
SKILLS_PATH = '/v1/skills'
SKILL_PATH = '/v1/skills/{skill_id}'
SKILL_SETS_PATH = '/v1/skill-sets'
SKILL_SET_PATH = '/v1/skill-sets/{set_id}'
SKILL_SET_SKILLS_COLLECTION_PATH = '/v1/skill-sets/{set_id}/skills'
SKILL_SET_SKILLS_PATH = '/v1/skill-sets/{set_id}/skills/{skill_id}'
SKILL_SET_CHILD_SET_PATH = '/v1/skill-sets/{set_id}/skill-sets/{child_set_id}'
MEMORY_SKILLS_PATH = '/v1/memory/skills'
MEMORY_ENRICHMENTS_PATH = '/v1/memory/enrichments/{session_id}'
MEMORY_SKILL_PATH = '/v1/memory/skills/{memory_id}'
MEMORY_INSIGHTS_PATH = '/v1/memory/insights'
MEMORY_INSIGHT_PATH = '/v1/memory/insights/{memory_id}'
MEMORY_INSIGHT_PROMOTE_PATH = '/v1/memory/insights/{memory_id}/promote'
RESOURCES_PATH = '/v1/resources'
RESOURCE_PATH = '/v1/resources/{resource_id}'
RESOURCE_BIND_PATH = '/v1/resources/{resource_id}/bind'
RESOURCE_RESTORE_PATH = '/v1/resources/{resource_id}/restore'
RESOURCE_REBIND_PATH = '/v1/resources/{resource_id}/rebind'
DEFAULT_USER_ID = 'sdk-user'
DEFAULT_MODEL = 'auto'


def message_payloads(messages: SdkMessagesInput) -> list[JsonObject]:
    """Dump SDK messages to wire JSON objects."""
    return [
        message.model_dump(mode='json', exclude_none=True)
        for message in to_contract_messages(messages)
    ]


def run_config_payload(options: RunOptions) -> JsonObject:
    """Build a RunConfig JSON payload.

    Carries who the run executes as. Skills are invocation-local, like tools, and are
    sent by ``skill_selection_payload`` instead: RunConfig forbids unknown fields, so
    putting them here made every skill-bearing invoke fail validation at the server.
    """
    payload: JsonObject = {
        'user_id': options.user_id,
        'thread_id': options.thread_id or _new_id('thr'),
    }
    _set_optional(payload, 'session_id', options.session_id)
    _set_optional(payload, 'invocation_id', options.invocation_id)
    _set_optional(payload, 'project_id', options.project_id)
    _set_optional(payload, 'agent_id', options.agent_id)
    _set_optional(payload, 'swarm_id', options.swarm_id)
    _set_optional(payload, 'client_timezone', options.client_timezone)
    _set_optional(payload, 'sdk_deployment_timezone', options.sdk_deployment_timezone)
    _set_optional(payload, 'model_directive', options.model_directive)
    _set_optional(payload, 'routing_preference', options.routing_preference)
    _set_optional(payload, 'system_model_choices', options.system_model_choices)
    if options.planning is not None:
        payload['planning'] = options.planning.model_dump(mode='json', exclude_none=True)
    _set_optional(payload, 'origin', options.origin)
    return payload


def skill_selection_payload(options: RunOptions) -> JsonObject:
    """Build the invocation-local skill selection fields for an invoke body."""
    payload: JsonObject = {}
    if options.attached_skill_ids:
        payload['attached_skill_ids'] = list(options.attached_skill_ids)
    if options.auto_skills:
        payload['auto_skills'] = True
    _set_optional(payload, 'auto_skills_query', options.auto_skills_query)
    _set_optional(payload, 'auto_skills_limit', options.auto_skills_limit)
    _set_optional(payload, 'auto_skills_skill_set', options.auto_skills_skill_set)
    return payload


def optional_run_options(options: RunOptions) -> JsonObject:
    """Build optional model, reasoning, response-streaming, and max-output fields."""
    payload: JsonObject = {'model': options.model}
    _set_optional(payload, 'reasoning', options.reasoning)
    if options.max_output is not None:
        payload['max_output'] = options.max_output
    if options.output_schema is not None:
        payload['output_schema'] = options.output_schema
    if options.memory is not None:
        payload['memory'] = options.memory
    if options.system_tools_config is not None:
        payload['system_tools_config'] = options.system_tools_config
    if options.followup_questions is not None:
        payload['followup_questions'] = options.followup_questions
    if options.stream_response:
        payload['stream_response'] = True
    if options.stream_deltas is False:
        payload['stream_deltas'] = False
    return payload


def invocation_options(
    tools: Sequence[ToolMetadata],
    *,
    private_data: Mapping[object, object] | None,
    force_final_tool: bool,
) -> JsonObject:
    """Build invocation-local tool and private-data wire fields."""
    if not tools and not private_data and not force_final_tool:
        return {}
    payload: JsonObject = {}
    if tools:
        payload['tools'] = [_function_tool_spec(tool) for tool in tools]
        reevaluate_controls = _reevaluate_controls(tools)
        if reevaluate_controls:
            payload['reevaluate_controls'] = reevaluate_controls
        final_tool_ids = [tool.name for tool in tools if tool.final_tool]
        if final_tool_ids:
            payload['final_tool_ids'] = final_tool_ids
        payload.update(_agent_delegation(tools))
    if private_data:
        payload['private_data'] = {str(key): value for key, value in private_data.items()}
    if force_final_tool:
        payload['force_final_tool'] = True
    return payload


def _agent_delegation(
    tools: Sequence[ToolMetadata],
    *,
    ancestors: tuple[int, ...] = (),
) -> JsonObject:
    """Build the agents this run may delegate to, and the tool arguments they satisfy.

    A tool declaring `depends_on_agent` holds a live Agent object. The server cannot import
    a customer's Python, so what crosses the wire is the agent's executable definition - its
    persona, its model, the tools it may reach - and the edge saying which argument it fills.

    Until this existed the server saw only flat function tools and could not know an agent
    was involved, which is why the SDK ended up doing the delegating: it was the only place
    that still knew.
    """
    agents: dict[str, JsonObject] = {}
    edges: list[JsonObject] = []
    for tool in tools:
        for dependency in _agent_dependencies(tool):
            agent = dependency.agent_ref
            name = _agent_name(agent, dependency.agent_id)
            if name not in agents:
                agents[name] = _agent_spec(agent, name, ancestors=ancestors)
            edges.append(
                {
                    'tool_id': tool.name,
                    'agent_name': name,
                    'arg_name': dependency.arg_name,
                    # The declaring tool's own schema is the authority on
                    # optionality; the server presents optional edges as a
                    # model-visible toggle instead of always delegating.
                    'optional': _dependency_arg_is_optional(tool, dependency.arg_name),
                },
            )
    if not edges:
        return {}
    return {'agents': list(agents.values()), 'agent_tool_dependencies': edges}


def _dependency_arg_is_optional(tool: ToolMetadata, arg_name: str) -> bool:
    """Report whether the tool's own definition gives the dependency arg a default.

    The dependency argument is stripped from the model-facing input schema, so
    the schema cannot answer this - the authority is the declaring callable or
    model. A BaseModel field with a default, or a function parameter with a
    default, is optional; anything else is a required dependency that always
    delegates. When the target cannot be introspected, required is the safe
    default: it preserves the always-delegate behavior.
    """
    target = tool.target
    if isinstance(target, type) and issubclass(target, BaseModel):
        field = target.model_fields.get(arg_name)
        return field is not None and not field.is_required()
    callable_target = cast('Callable[..., object]', target)
    try:
        parameter = signature(callable_target).parameters.get(arg_name)
    except (TypeError, ValueError):
        return False
    return parameter is not None and parameter.default is not Parameter.empty


def _agent_dependencies(tool: ToolMetadata) -> list[AgentDependency]:
    raw = getattr(tool.target, TOOL_DEPENDENCIES_ATTR, None)
    if not isinstance(raw, list):
        return []
    return [item for item in cast('list[object]', raw) if isinstance(item, AgentDependency)]


def _agent_name(agent: object, fallback: str) -> str:
    name = getattr(agent, 'name', None)
    return name if isinstance(name, str) and name else fallback


def _agent_path(agent: object, ancestors: tuple[int, ...]) -> tuple[int, ...]:
    if id(agent) in ancestors:
        message = 'Agent dependency cycle cannot be serialized'
        raise ValueError(message)
    if len(ancestors) >= _MAX_AGENT_SERIALIZATION_DEPTH:
        message = 'Agent dependency serialization exceeds maximum depth 16'
        raise ValueError(message)
    return (*ancestors, id(agent))


def _agent_spec(
    agent: object,
    name: str,
    *,
    ancestors: tuple[int, ...] = (),
) -> JsonObject:
    path = _agent_path(agent, ancestors)
    spec: JsonObject = {'name': name}
    system_tools = getattr(agent, 'system_tools_config', None)
    if isinstance(system_tools, SystemToolsConfig):
        policy = system_tools.to_metadata_patch()
        if policy:
            spec['system_tools_config'] = policy
    _set_optional(spec, 'system_prompt', _string_attr(agent, 'system_prompt'))
    _set_optional(spec, 'model', _string_attr(agent, 'model'))
    _set_optional(spec, 'description', _string_attr(agent, 'description'))
    if getattr(agent, 'use_as_final_output', False) is True:
        spec['use_as_final_output'] = True
    nested_synthesis = getattr(agent, 'included_nested_synthesis', 'auto')
    if nested_synthesis in (True, False):
        spec['included_nested_synthesis'] = nested_synthesis
    compiled = _compiled_agent_tools(agent)
    if compiled:
        spec['tool_ids'] = [tool.name for tool in compiled]
        # The specs travel whole: the server runs the delegate, and tool ids alone name
        # tools the server has never seen - which is how a delegate once arrived with an
        # empty tool list.
        spec['tools'] = [_function_tool_spec(tool) for tool in compiled]
        spec.update(_agent_delegation(compiled, ancestors=path))
        final_tool_ids = [tool.name for tool in compiled if tool.final_tool]
        if final_tool_ids:
            spec['final_tool_ids'] = final_tool_ids
    peer_dependencies = _peer_dependencies(agent)
    if peer_dependencies:
        spec['peer_dependencies'] = peer_dependencies
    return spec


def _peer_dependencies(agent: object) -> list[JsonObject]:
    """Serialize the edges declared on a swarm member: peer agents and tools it needs.

    These were recorded by `swarm.member.depends_on_agent/depends_on_tool` and, until now,
    read by nothing - the member DAG existed only in the developer's code.
    """
    raw = getattr(agent, TOOL_DEPENDENCIES_ATTR, None)
    if not isinstance(raw, list):
        return []
    dependencies: list[JsonObject] = []
    for item in cast('list[object]', raw):
        if isinstance(item, AgentDependency):
            dependencies.append(
                {
                    'kind': 'agent',
                    'name': _agent_name(item.agent_ref, item.agent_id),
                    'arg_name': item.arg_name,
                },
            )
        elif isinstance(item, ToolDependency):
            dependencies.append(
                {
                    'kind': 'tool',
                    'name': item.tool_name,
                    'arg_name': item.arg_name,
                },
            )
    return dependencies


def swarm_roster_payload(members: Sequence[object]) -> JsonObject:
    """Build the swarm roster wire fields: every member as an executable agent spec.

    The server plans over this roster and runs each planned member as a child session.
    Without it a swarm run collapses to a single scoped run over the swarm's own tools -
    which is exactly what every v2 swarm did before this existed.
    """
    if not members:
        return {}
    agents: list[JsonObject] = []
    for index, member in enumerate(members):
        name = _agent_name(member, f'member-{index + 1}')
        agents.append(_agent_spec(member, name))
    return {'agents': agents, 'swarm_roster': True}


def _register_tool(collected: dict[str, ToolMetadata], tool: ToolMetadata) -> None:
    existing = collected.get(tool.name)
    if existing is not None and existing.target is not tool.target:
        message = f'Conflicting local tool registration for {tool.name!r}: different targets'
        raise ValueError(message)
    collected.setdefault(tool.name, tool)


def invocation_tools(
    tools: Sequence[ToolMetadata],
    members: Sequence[object],
) -> list[ToolMetadata]:
    """Validate and collect the single broker namespace before starting an invoke."""
    collected: dict[str, ToolMetadata] = {}
    for tool in (*tools, *delegate_tools(tools), *swarm_member_tools(members)):
        _register_tool(collected, tool)
    return list(collected.values())


def swarm_member_tools(members: Sequence[object]) -> list[ToolMetadata]:
    """Return every member's compiled tools, for local execution registration.

    Member runs happen server-side, but their tools are the developer's callables in this
    process: the child sessions' tool calls broker back over this client's stream.
    """
    collected: dict[str, ToolMetadata] = {}
    for member in members:
        for compiled in _reachable_agent_tools(member, ()):
            _register_tool(collected, compiled)
    return list(collected.values())


def _string_attr(agent: object, attribute: str) -> str | None:
    value = getattr(agent, attribute, None)
    return value if isinstance(value, str) and value else None


def _compiled_agent_tools(agent: object) -> list[ToolMetadata]:
    compile_tools = getattr(agent, 'compile_tools', None)
    if not callable(compile_tools):
        return []
    return list(cast('Sequence[ToolMetadata]', compile_tools()))


def _reachable_agent_tools(agent: object, ancestors: tuple[int, ...]) -> list[ToolMetadata]:
    path = _agent_path(agent, ancestors)
    compiled = _compiled_agent_tools(agent)
    nested = [
        child
        for tool in compiled
        for dependency in _agent_dependencies(tool)
        for child in _reachable_agent_tools(dependency.agent_ref, path)
    ]
    return [*compiled, *nested]


def delegate_tools(tools: Sequence[ToolMetadata]) -> list[ToolMetadata]:
    """Return every delegate agent's compiled tools, for local execution registration.

    The server runs a delegate as a nested session, but the delegate's tools are still the
    developer's own callables living in this process. The local tool runtime must know them
    by name, or the child's brokered tool calls arrive for tools nobody registered.
    """
    collected: dict[str, ToolMetadata] = {}
    for tool in tools:
        for dependency in _agent_dependencies(tool):
            for compiled in _reachable_agent_tools(dependency.agent_ref, ()):
                _register_tool(collected, compiled)
    return list(collected.values())


# The server uses this verbatim as its broker wait deadline, and the SDK withholds a
# layer's outcomes until the slowest tool in that layer finishes. A hardcoded 30s
# therefore expired whole layers server-side on slower graphs, ended the run, and left
# the late result batch rejected with 409 invoke_not_running.
# The contract caps timeout_ms at 600000 (maivn_contracts.tools.model.TimeoutMs), so the
# configured budget is clamped rather than sent whole - an over-cap value is rejected
# with 422 before the run starts.
_MAX_CONTRACT_TIMEOUT_MS = 600_000
_TOOL_TIMEOUT_MS = min(int(DEFAULT_TOOL_EXECUTION_TIMEOUT * 1000), _MAX_CONTRACT_TIMEOUT_MS)


def _function_tool_spec(tool: ToolMetadata) -> JsonObject:
    payload: JsonObject = {
        'tool_id': tool.name,
        'namespace': 'sdk',
        'version': 'v1',
        'idempotency': 'non_idempotent' if tool.metadata.get('destructive') else 'unknown',
        'timeout_ms': _TOOL_TIMEOUT_MS,
        'kind': 'function',
        'name': tool.name,
        'description': tool.description or f'Execute {tool.name}.',
        'input_schema': copy.deepcopy(tool.input_schema),
    }
    if isinstance(tool.output_schema, dict):
        payload['output_schema'] = dict(cast('Mapping[str, object]', tool.output_schema))
    if isinstance(tool.target, type) and issubclass(tool.target, BaseModel):
        payload['model_constructor'] = True
    if tool.always_execute:
        # Preserve the developer's deterministic execution declaration on the wire.
        payload['always_execute'] = True
    tool_edges = _tool_dependencies(tool)
    if tool_edges:
        payload['tool_dependencies'] = tool_edges
    producer_requirements = _argument_producer_requirements(tool)
    if producer_requirements:
        payload['argument_producer_requirements'] = producer_requirements
    _apply_compose_argument_policies(payload, tool)
    interrupt_edges = _interrupt_dependencies(tool)
    if interrupt_edges:
        payload['interrupt_dependencies'] = interrupt_edges
    return payload


def _tool_dependencies(tool: ToolMetadata) -> list[JsonObject]:
    """Serialize tool->tool argument edges declared via depends_on_tool.

    Each edge says that this tool's `arg_name` is produced by `tool_name`'s
    result, preserving the developer-authored dependency contract.
    """
    raw = getattr(tool.target, TOOL_DEPENDENCIES_ATTR, None)
    if not isinstance(raw, list):
        return []
    edges: list[JsonObject] = []
    for item in cast('list[object]', raw):
        if not isinstance(item, ToolDependency):
            continue
        edge: JsonObject = {'tool_name': item.tool_name, 'arg_name': item.arg_name}
        if item.revision_policy is not None:
            edge['revision_policy'] = item.revision_policy
        if item.result_scope != 'invocation':
            edge['result_scope'] = item.result_scope
        edges.append(edge)
    return edges


def _argument_producer_requirements(tool: ToolMetadata) -> list[JsonObject]:
    """Serialize caller-authored reference requirements without result injection."""
    raw = getattr(tool.target, ARGUMENT_PRODUCER_REQUIREMENTS_ATTR, None)
    if not isinstance(raw, list):
        return []
    return [
        {
            'argument_path': item.argument_path,
            'producer_tool_name': item.producer_tool_name,
            'reference_kind': item.reference_kind,
        }
        for item in cast('list[object]', raw)
        if isinstance(item, ArgumentProducerRequirement)
    ]


def _apply_compose_argument_policies(payload: JsonObject, tool: ToolMetadata) -> None:
    """Project composition policy beside the argument schema it governs."""
    raw = getattr(tool.target, COMPOSE_ARGUMENT_POLICIES_ATTR, None)
    if not isinstance(raw, list):
        return
    schema = payload.get('input_schema')
    if not isinstance(schema, dict):
        return
    typed_schema = cast('dict[str, object]', schema)
    raw_properties = typed_schema.get('properties')
    if not isinstance(raw_properties, dict):
        return
    properties = cast('dict[str, object]', raw_properties)
    for item in cast('list[object]', raw):
        if not isinstance(item, ComposeArgumentPolicy):
            continue
        raw_argument_schema = properties.get(item.argument_path)
        if not isinstance(raw_argument_schema, dict):
            message = f"compose_argument target '{item.argument_path}' has no argument schema"
            raise TypeError(message)
        argument_schema = cast('dict[str, object]', raw_argument_schema)
        argument_schema['x-maivn-compose-argument-policy'] = {
            'mode': item.mode,
            'approval': item.approval,
        }


def _interrupt_dependencies(tool: ToolMetadata) -> list[JsonObject]:
    """Serialize human-owned arguments without serializing their local callbacks."""
    raw = getattr(tool.target, INTERRUPT_DEPENDENCIES_ATTR, None)
    if not isinstance(raw, list):
        return []
    return [
        {
            'arg_name': dependency.arg_name,
            'prompt_source': dependency.prompt_source,
            'question': _interrupt_question(dependency),
            'response_schema': _interrupt_response_schema(tool, dependency),
        }
        for dependency in cast('list[object]', raw)
        if isinstance(dependency, InterruptDependency)
    ]


def _interrupt_question(dependency: InterruptDependency) -> str:
    if dependency.prompt_source == 'agent_generated':
        return ''
    if dependency.prompt_source == 'fallback':
        return f'{dependency.arg_name}: '
    return dependency.prompt


def _interrupt_response_schema(
    tool: ToolMetadata,
    dependency: InterruptDependency,
) -> JsonObject:
    annotation: object | None = None
    try:
        annotation = get_type_hints(tool.target).get(dependency.arg_name)
    except (NameError, TypeError):
        try:
            annotation = signature(tool.target).parameters[dependency.arg_name].annotation
        except (KeyError, TypeError, ValueError):
            annotation = None
    if annotation is not None and annotation is not Parameter.empty:
        try:
            schema = TypeAdapter(annotation).json_schema()
        except (TypeError, ValueError):
            schema = _fallback_interrupt_schema(dependency)
    else:
        schema = _fallback_interrupt_schema(dependency)
    declared_schema = _fallback_interrupt_schema(dependency)
    if dependency.input_type != 'text' and schema.get('type') != declared_schema['type']:
        # PARALLEL-BENCHMARK-NOTE(2026-08-16): explicit interrupt UI semantics
        # are authoritative even when the runtime callback still receives a
        # broad string value. Otherwise ``input_type='boolean'`` compiles back
        # to ``{'type': 'string'}`` and Studio can only offer free text.
        schema = declared_schema
    if dependency.choices:
        schema = {**schema, 'enum': list(dependency.choices)}
    return schema


def _fallback_interrupt_schema(dependency: InterruptDependency) -> JsonObject:
    schema_type = {
        'boolean': 'boolean',
        'number': 'number',
        'choice': 'string',
        'text': 'string',
    }[dependency.input_type]
    return {'type': schema_type}


def _reevaluate_controls(tools: Sequence[ToolMetadata]) -> list[JsonObject]:
    """Flatten tool-local reevaluate metadata into the additive invoke wire field."""
    controls: list[JsonObject] = []
    for tool in tools:
        execution_controls = tool.metadata.get('execution_controls')
        if not isinstance(execution_controls, Mapping):
            continue
        controls_mapping = cast('Mapping[str, object]', execution_controls)
        reevaluate = controls_mapping.get('reevaluate')
        if not isinstance(reevaluate, list):
            continue
        for raw_control in cast('list[object]', reevaluate):
            if not isinstance(raw_control, Mapping):
                continue
            control_mapping = cast('Mapping[str, object]', raw_control)
            tool_id = control_mapping.get('tool_id')
            tool_name = control_mapping.get('tool_name')
            timing = control_mapping.get('timing')
            instance_control = control_mapping.get('instance_control')
            if not all(
                isinstance(value, str) for value in (tool_id, tool_name, timing, instance_control)
            ):
                continue
            controls.append(
                {
                    'kind': 'reevaluate',
                    'dependent_tool_id': tool.name,
                    'dependent_tool_name': tool.name,
                    'tool_id': tool_id,
                    'tool_name': tool_name,
                    'timing': timing,
                    'instance_control': instance_control,
                }
            )
    return controls


def resume_params(options: RunOptions) -> Mapping[str, int] | None:
    """Build SSE resume query parameters."""
    if options.resume_from_position <= 0:
        return None
    return {'from_position': options.resume_from_position}


def _set_optional(payload: JsonObject, key: str, value: object | None) -> None:
    if value is not None:
        payload[key] = value


def _new_id(prefix: str) -> str:
    return f'{prefix}-{uuid4().hex}'


__all__ = [
    'ARTIFACTS_PATH',
    'ARTIFACT_DOWNLOAD_PATH',
    'ARTIFACT_PATH',
    'ARTIFACT_PREVIEW_PATH',
    'DEFAULT_MODEL',
    'DEFAULT_USER_ID',
    'INVOKE_PATH',
    'MEMORY_INSIGHTS_PATH',
    'MEMORY_INSIGHT_PATH',
    'MEMORY_INSIGHT_PROMOTE_PATH',
    'MEMORY_SKILLS_PATH',
    'MEMORY_SKILL_PATH',
    'RESOURCES_PATH',
    'RESOURCE_BIND_PATH',
    'RESOURCE_PATH',
    'RESOURCE_REBIND_PATH',
    'RESOURCE_RESTORE_PATH',
    'SESSION_CANCEL_PATH',
    'SESSION_EVENTS_PATH',
    'SESSION_USAGE_PATH',
    'SKILLS_PATH',
    'SKILL_PATH',
    'SKILL_SETS_PATH',
    'SKILL_SET_CHILD_SET_PATH',
    'SKILL_SET_PATH',
    'SKILL_SET_SKILLS_COLLECTION_PATH',
    'SKILL_SET_SKILLS_PATH',
    'THREADS_PATH',
    'THREAD_APPROVALS_PATH',
    'THREAD_EVENTS_PATH',
    'THREAD_INTERRUPT_RESPONSES_PATH',
    'THREAD_MESSAGES_PATH',
    'THREAD_STATE_PATH',
    'THREAD_TIME_TRAVEL_PATH',
    'TOOL_RESULT_BATCH_PATH',
    'TOOL_RESULT_PATH',
    'delegate_tools',
    'invocation_options',
    'invocation_tools',
    'message_payloads',
    'optional_run_options',
    'resume_params',
    'run_config_payload',
    'skill_selection_payload',
    'swarm_member_tools',
    'swarm_roster_payload',
]
