# ruff: noqa: PLR0913 - the wide signatures here are public SDK entry points; collapsing them into a config object would break callers.
"""Run-option resolution, model-choice merging, and structured-output planning."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, cast

from maivn._internal.compat.options import (
    MemoryConfig,
    ModelChoice,
    ModelConfig,
    SessionOrchestrationConfig,
    SystemToolsConfig,
)
from maivn._internal.compat.tooling import compile_tool_metadata
from maivn._internal.models import (
    InvokeResponse,
    JsonObject,
    ModelDirective,
    RoutingPreference,
    RunOptions,
    ToolMetadata,
)
from maivn._internal.scope.types import (
    DEFAULT_MODEL,
    MODEL_DIRECTIVES,
    AutoSkills,
    ReasoningEffort,
    RuntimeModelChoice,
    ScopeResourceBindingType,
)
from maivn.messages import SdkMessagesInput, SystemMessage, to_contract_messages

if TYPE_CHECKING:
    from collections.abc import Sequence

    from maivn_contracts.messages import Message
    from pydantic import BaseModel


def messages_with_system_prompt(
    messages: SdkMessagesInput,
    system_prompt: str | SystemMessage | None,
) -> list[Message]:
    """Prepend a system prompt when the message list does not already include one."""
    normalized = to_contract_messages(messages)
    if system_prompt is None:
        return list(normalized)
    has_system = any(message.role == 'system' for message in normalized)
    if has_system:
        return list(normalized)
    system_message = (
        system_prompt
        if isinstance(system_prompt, SystemMessage)
        else SystemMessage(content=system_prompt)
    )
    return [system_message.to_contract(), *normalized]


def _model_options(options: RunOptions | None, model: str) -> RunOptions:
    if options is not None:
        return options
    if model in MODEL_DIRECTIVES:
        return RunOptions(model=DEFAULT_MODEL, model_directive=cast('ModelDirective', model))
    return RunOptions(model=model)


def scope_options(
    options: RunOptions | None,
    scope_model: str,
    attached_skill_ids: list[str],
    auto_skills: AutoSkills | None,
    *,
    scope_name: str,
    scope_binding_type: ScopeResourceBindingType,
    thread_id: str | None = None,
    model: RuntimeModelChoice | None = None,
    memory_config: MemoryConfig | None = None,
    reasoning: ReasoningEffort | None = None,
    routing_preference: RoutingPreference | None = None,
    system_tools_config: SystemToolsConfig | None = None,
    orchestration_config: SessionOrchestrationConfig | None = None,
    stream_response: bool | None = None,
    timeout: float | None = None,
    max_results: int | None = None,
    origin: str | None = None,
) -> RunOptions:
    """Resolve one scope invocation's run options from scope state and call overrides."""
    resolved = _model_options(options, scope_model)
    updates: dict[str, object] = {}
    if origin is not None:
        updates['origin'] = origin
    # A run must execute as the same scope its resources were bound to. Resources bind
    # under `scope_name` (see aregister_scope_resources_once), so a run that omits it
    # is scoped to the project alone and can never retrieve its own bound resources.
    binding_field = 'agent_id' if scope_binding_type == 'agent' else 'swarm_id'
    updates[binding_field] = scope_name
    # Plain pass-through overrides: present means set, absent means inherit. One
    # loop rather than a branch each, so adding the next one does not push this
    # function over the complexity ceiling again.
    updates.update(
        {
            field: value
            for field, value in (
                ('thread_id', thread_id),
                ('reasoning', reasoning),
                ('routing_preference', routing_preference),
                ('stream_response', stream_response),
                ('timeout', timeout),
                ('max_results', max_results),
            )
            if value is not None
        },
    )
    _update_model_choice(updates, model)
    _update_config_patches(
        updates,
        memory_config=memory_config,
        system_tools_config=system_tools_config,
        orchestration_config=orchestration_config,
    )
    merged_skill_ids = tuple(dict.fromkeys((*resolved.attached_skill_ids, *attached_skill_ids)))
    if merged_skill_ids:
        updates['attached_skill_ids'] = merged_skill_ids
    if auto_skills is not None:
        updates['auto_skills'] = True
        updates['auto_skills_query'] = auto_skills.query
        updates['auto_skills_limit'] = auto_skills.limit
    if not updates:
        return resolved
    return resolved.model_copy(update=updates)


def _memory_policy_payload(config: MemoryConfig) -> JsonObject:
    """Expand the v1-compatible partial config into the closed v2 policy contract."""
    level = config.level or 'clarity'
    retrieval = config.retrieval
    top_k = retrieval.top_k if retrieval is not None else None
    context_caps = {'none': 32, 'glimpse': 2000, 'focus': 6000, 'clarity': 12000}

    def count(explicit: int | None) -> int:
        return explicit if explicit is not None else (top_k if top_k is not None else 3)

    skill_max = None if retrieval is None else retrieval.skill_injection_max_count
    insight_max = None if retrieval is None else retrieval.insight_injection_max_count
    resource_max = None if retrieval is None else retrieval.resource_injection_max_count
    skill_extraction = config.skill_extraction
    insight_extraction = config.insight_extraction
    return {
        'enabled': True if config.enabled is None else config.enabled,
        'level': level,
        'persistence_mode': config.persistence_mode or 'vector_plus_graph',
        'retrieval': {
            'skills_enabled': _enabled_by_default(
                value=None if retrieval is None else retrieval.skills_enabled
            ),
            'insights_enabled': _enabled_by_default(
                value=None if retrieval is None else retrieval.insights_enabled
            ),
            'resources_enabled': _enabled_by_default(
                value=None if retrieval is None else retrieval.resources_enabled
            ),
            'max_skills': count(skill_max),
            'max_insights': count(insight_max),
            'max_resources': count(resource_max),
            'max_context_chars': context_caps[level],
            **(
                {'graph_enabled': retrieval.graph_enabled}
                if retrieval is not None and retrieval.graph_enabled is not None
                else {}
            ),
            **(
                {'max_graph_assertions': retrieval.graph_injection_max_count}
                if retrieval is not None and retrieval.graph_injection_max_count is not None
                else {}
            ),
        },
        'skill_extraction': {
            'enabled': (
                True
                if skill_extraction is None or skill_extraction.enabled is None
                else skill_extraction.enabled
            ),
            'max_count': (
                2
                if skill_extraction is None or skill_extraction.max_count is None
                else skill_extraction.max_count
            ),
        },
        'insight_extraction': {
            'enabled': (
                True
                if insight_extraction is None or insight_extraction.enabled is None
                else insight_extraction.enabled
            ),
            'max_count': (
                2
                if insight_extraction is None or insight_extraction.max_count is None
                else insight_extraction.max_count
            ),
        },
        **(
            {
                'graph_extraction': {
                    'enabled': _enabled_by_default(value=config.graph_extraction.enabled),
                    'max_count': config.graph_extraction.max_count or 16,
                }
            }
            if config.graph_extraction is not None
            else {}
        ),
    }


def _enabled_by_default(*, value: bool | None) -> bool:
    return True if value is None else value


def _update_config_patches(
    updates: dict[str, object],
    *,
    memory_config: MemoryConfig | None,
    system_tools_config: SystemToolsConfig | None,
    orchestration_config: SessionOrchestrationConfig | None,
) -> None:
    """Apply non-empty config metadata patches to the pending option updates."""
    if memory_config is not None:
        updates['memory'] = _memory_policy_payload(memory_config)
    if system_tools_config is not None:
        system_tools_payload = system_tools_config.to_metadata_patch()
        if system_tools_payload:
            updates['system_tools_config'] = system_tools_payload
    if orchestration_config is not None:
        orchestration_payload = orchestration_config.to_metadata_patch()
        if orchestration_payload:
            updates['orchestration_config'] = orchestration_payload


@dataclass(frozen=True, slots=True)
class StructuredOutputPlan:
    """The tools, forcing intent, and options one structured-output invoke runs with."""

    options: RunOptions
    tools: tuple[ToolMetadata, ...]
    force_final_tool: bool


def plan_structured_output(
    options: RunOptions,
    model: type[BaseModel] | None,
    *,
    tools: Sequence[ToolMetadata] = (),
    force_final_tool: bool = False,
) -> StructuredOutputPlan:
    """Compile a typed structured result as the invocation's forced final tool.

    Reuse an existing tool for the same model when present. The schema remains on
    the request for server-side validation while the final tool provides the
    executable developer-facing result contract.
    """
    if model is None:
        return StructuredOutputPlan(
            options=options,
            tools=tuple(tools),
            force_final_tool=force_final_tool,
        )

    planned = list(tools)
    final_tool = next((tool for tool in planned if tool.target is model), None)
    if final_tool is not None:
        promoted = final_tool.model_copy(update={'final_tool': True})
        planned[planned.index(final_tool)] = promoted
        final_tool = promoted
    else:
        final_tool = compile_tool_metadata(
            ToolMetadata(
                name=model.__name__,
                target=model,
                description=_structured_output_description(model),
                final_tool=True,
            ),
        )
        planned.append(final_tool)

    output_schema: JsonObject = {'name': model.__name__, 'schema': final_tool.input_schema}
    return StructuredOutputPlan(
        options=options.model_copy(update={'output_schema': output_schema}),
        tools=tuple(planned),
        force_final_tool=True,
    )


def _structured_output_description(model: type[BaseModel]) -> str:
    """Describe the structured-output tool from the model's own docstring, as v1 did."""
    docstring = (model.__doc__ or '').strip()
    return docstring or f'Structured output schema for {model.__name__}.'


def _update_model_choice(updates: dict[str, object], model: object) -> None:
    if model is None:
        return
    if isinstance(model, str):
        _update_string_model_choice(updates, model)
        return
    if isinstance(model, ModelChoice):
        _update_typed_model_choice(updates, model)
        return
    if isinstance(model, ModelConfig):
        choice = model.response
        if choice is not None:
            _update_typed_model_choice(updates, choice)
        system_model_choices = {
            part: choice.model_dump(mode='json', exclude_none=True)
            for part in ('thinking', 'compose_argument', 'repl')
            if (choice := model.selection_for(part)) is not None
        }
        if system_model_choices:
            updates['system_model_choices'] = system_model_choices
        planning = model.planning
        if planning is not None:
            updates['planning'] = planning
        return
    message = f'unsupported model override type: {type(model).__name__}'
    raise TypeError(message)


def _update_string_model_choice(updates: dict[str, object], model: str) -> None:
    normalized = model.strip()
    if not normalized:
        message = 'model override must not be blank'
        raise ValueError(message)
    if normalized in MODEL_DIRECTIVES:
        updates['model_directive'] = cast('ModelDirective', normalized)
        return
    updates['model'] = normalized
    updates['model_directive'] = 'force'


def _update_typed_model_choice(updates: dict[str, object], model: ModelChoice) -> None:
    if model.tier is not None:
        updates['model_directive'] = model.tier
        return
    if model.model_id is not None:
        updates['model'] = model.model_id
        updates['model_directive'] = 'force'


def response_with_thread_id(response: InvokeResponse, thread_id: str | None) -> InvokeResponse:
    """Stamp the resolved thread id onto a response that did not carry one."""
    if response.thread_id is not None or thread_id is None:
        return response
    return response.model_copy(update={'thread_id': thread_id})
