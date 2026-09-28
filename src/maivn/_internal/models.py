"""Typed SDK result and event models."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from datetime import datetime
from typing import Any, Literal, TypeAlias, cast

from maivn_contracts.artifacts import ArtifactRef
from maivn_contracts.events import Event
from maivn_contracts.messages import Message
from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    StrictBool,
    StrictInt,
    StrictStr,
    TypeAdapter,
)

from maivn._internal.compat.options import PlanningChoice

JsonObject: TypeAlias = dict[str, Any]
ModelDirective: TypeAlias = Literal['auto', 'fast', 'balanced', 'max', 'ultra', 'force']
# Mirrors maivn_contracts.runtime.RoutingPreference. Declared here, like
# ModelDirective above it, so the SDK's public vocabulary does not depend on
# importing the contracts package at type-check time.
RoutingPreference: TypeAlias = Literal['speed', 'cost', 'quality']


class InvokeAccepted(BaseModel):
    """Response returned by the API after accepting an invoke."""

    model_config = ConfigDict(extra='forbid', frozen=True)

    session_id: StrictStr = Field(..., min_length=1)
    stream_position: StrictInt = Field(..., ge=0)


class ThreadAccepted(BaseModel):
    """Response returned by thread start, message, and approval requests."""

    model_config = ConfigDict(extra='forbid', frozen=True)

    thread_id: StrictStr = Field(..., min_length=1)
    session_id: StrictStr = Field(..., min_length=1)
    stream_position: StrictInt = Field(..., ge=0)


class RunOptions(BaseModel):
    """Per-call options for invoke, stream, and thread requests.

    The timeout and execution-budget compatibility fields are SDK-local. The current
    wire contract has no corresponding request fields; the HTTP timeout is
    live, while the other retained fields are surfaced for v1 callers and are no-ops.
    """

    model_config = ConfigDict(extra='forbid', frozen=True)

    thread_id: StrictStr | None = Field(default=None, min_length=1)
    session_id: StrictStr | None = Field(default=None, min_length=1)
    invocation_id: StrictStr | None = Field(default=None, min_length=1)
    user_id: StrictStr = Field(default='sdk-user', min_length=1)
    project_id: StrictStr | None = Field(default=None, min_length=1)
    agent_id: StrictStr | None = Field(default=None, min_length=1)
    swarm_id: StrictStr | None = Field(default=None, min_length=1)
    client_timezone: StrictStr | None = Field(default=None, min_length=1)
    sdk_deployment_timezone: StrictStr | None = Field(default=None, min_length=1)
    model: StrictStr = Field(default='auto', min_length=1)
    timeout: float | None = Field(
        default=None,
        gt=0,
        description='SDK-local HTTP timeout in seconds; omitted from the v2 wire payload.',
    )
    max_results: StrictInt | None = Field(
        default=None,
        ge=0,
        description=(
            'V1 semantic-search result bound retained for compatibility; the current '
            'v2 invoke wire has no semantic-search field, so this is a documented no-op.'
        ),
    )
    tool_execution_timeout: float | None = Field(
        default=None,
        gt=0,
        description='V1 per-tool timeout retained in SDK config; no v2 wire backend exists yet.',
    )
    dependency_wait_timeout: float | None = Field(
        default=None,
        gt=0,
        description=(
            'V1 dependency wait timeout retained in SDK config; no v2 wire backend exists yet.'
        ),
    )
    total_execution_timeout: float | None = Field(
        default=None,
        gt=0,
        description=(
            'V1 total execution timeout retained in SDK config; no v2 wire backend exists yet.'
        ),
    )
    reasoning: StrictStr | None = Field(default=None, min_length=1)
    model_directive: ModelDirective | None = Field(default=None)
    routing_preference: RoutingPreference | None = Field(
        default=None,
        description=(
            'Ask automatic routing to lean faster, cheaper, or stronger than the '
            'request content alone would pick. Only automatic routing reads it.'
        ),
    )
    system_model_choices: JsonObject | None = Field(
        default=None,
        description='Explicit think and REPL model choices sent in the runtime config.',
    )
    planning: PlanningChoice | None = Field(
        default=None,
        description='Explicit planning model choice sent in the runtime config.',
    )
    max_output: StrictInt | None = Field(default=None, ge=1)
    output_schema: JsonObject | None = Field(
        default=None,
        description='Provider-native structured output name and JSON Schema.',
    )
    memory: JsonObject | None = Field(
        default=None,
        description='Complete invocation-local memory policy sent to the API.',
    )
    system_tools_config: JsonObject | None = Field(default=None)
    orchestration_config: JsonObject | None = Field(default=None)
    followup_questions: JsonObject | None = Field(default=None)
    stream_response: StrictBool | None = Field(default=None)
    stream_deltas: StrictBool | None = Field(
        default=None,
        description='Emit live model text deltas; None lets the call surface decide.',
    )
    resume_from_position: StrictInt = Field(default=0, ge=0)
    attached_skill_ids: tuple[StrictStr, ...] = Field(default=(), max_length=256)
    auto_skills: StrictBool = False
    auto_skills_query: StrictStr | None = Field(default=None, min_length=1, max_length=12000)
    auto_skills_limit: StrictInt | None = Field(default=None, ge=1, le=16)
    auto_skills_skill_set: StrictStr | None = Field(default=None, min_length=1)
    origin: StrictStr | None = Field(
        default=None,
        min_length=1,
        description=(
            'Optional self-reported invoking client (e.g. "studio"), mirrored onto the wire '
            'RunConfig.origin field. Display/filter only, never consulted for authorization.'
        ),
    )


class ApprovalDecision(BaseModel):
    """Thread approval decision submitted to the API."""

    model_config = ConfigDict(extra='forbid', frozen=True)

    approved: StrictBool
    decided_by: StrictStr = Field(..., min_length=1)
    reason: StrictStr | None = Field(default=None, min_length=1)


class ProviderModelUsage(BaseModel):
    """What one provider and model reported for the calls it served."""

    model_config = ConfigDict(extra='forbid', frozen=True)

    provider: StrictStr
    model: StrictStr
    calls: StrictInt = Field(ge=0)
    input_tokens: StrictInt = Field(ge=0)
    output_tokens: StrictInt = Field(ge=0)
    cache_read_input_tokens: StrictInt = Field(ge=0)
    cache_creation_input_tokens: StrictInt = Field(ge=0)


class ProviderUsage(BaseModel):
    """Provider-reported token counts, the numbers a price table is applied to.

    These are what the model provider itself reported, per provider and model, so
    a run can be priced the same way any other tool's run is priced. They are a
    different reading from `InvokeResponse.usage`, which is the platform's own
    count of the same work.

    A terminal response finishes the run, not necessarily its related work. Its
    `scope='run'` block is never settled. A `scope='session'` block is a snapshot
    of the recorded session family: `settled=True` means no known controlled
    sessions or calls are open, with complete call evidence and no truncation.
    It does not close the family against future derived roots. Later reads can
    include more work even after a settled snapshot; `as_of` dates the read.

    `provider_counts_complete` is False when provider counts are missing and
    platform counts stand in for them. Session snapshots also keep it False
    while known work or call evidence is incomplete. Neither flag proves that
    all future related work is finished. Preserve the counts with their status.
    """

    model_config = ConfigDict(extra='forbid', frozen=True)

    scope: Literal['run', 'session']
    settled: StrictBool
    provider_counts_complete: StrictBool
    calls: StrictInt = Field(ge=0)
    input_tokens: StrictInt = Field(ge=0)
    output_tokens: StrictInt = Field(ge=0)
    cache_read_input_tokens: StrictInt = Field(ge=0)
    cache_creation_input_tokens: StrictInt = Field(ge=0)
    by_model: tuple[ProviderModelUsage, ...] = ()
    as_of: datetime | None = None


class InvokeResponse(BaseModel):
    """Typed SDK invoke result."""

    model_config = ConfigDict(extra='forbid', frozen=True)

    final_message: Message
    session_id: StrictStr = Field(..., min_length=1)
    organization_id: StrictStr | None = Field(default=None, min_length=1)
    project_id: StrictStr | None = Field(default=None, min_length=1)
    thread_id: StrictStr | None = Field(
        default=None,
        min_length=1,
        description='V1-compatible thread identifier for follow-up invocations.',
    )
    root_event_id: StrictStr = Field(..., min_length=1)
    event_positions: list[StrictInt] = Field(..., min_length=1)
    usage: JsonObject = Field(default_factory=dict)
    stop_reason: StrictStr | None = Field(default=None)
    tool_call_count: StrictInt = Field(default=0, ge=0)
    tool_names: list[StrictStr] = Field(default_factory=list)
    response: StrictStr = Field(..., description='Convenience alias for final_message.content.')
    private_value_restorations: list[JsonObject] = Field(
        default_factory=list,
        exclude=True,
        description='Local display provenance; JSON Pointers and code-point offsets in response.',
    )
    result: Any | None = Field(default=None, description='V1-compatible structured/final result.')
    assistant_id: StrictStr | None = Field(default=None, description='V1-compatible assistant id.')
    artifacts: tuple[ArtifactRef, ...] = Field(
        default=(),
        description='Frozen returnable artifacts selected by the completed invocation.',
    )

    @property
    def responses(self) -> list[str]:
        """Return the v1-compatible list form used by legacy demos."""
        return [self.response] if self.response else []

    @property
    def provider_usage(self) -> ProviderUsage | None:
        """Return what the model providers reported for this run, if the platform said.

        None means the platform this response came from does not publish provider
        counts, not that the run spent nothing. `Agent.session_usage` is where the
        total lands once work the run scheduled afterwards has finished.
        """
        block = self.usage.get('provider')
        if not isinstance(block, dict):
            return None
        return ProviderUsage.model_validate(cast('JsonObject', block))

    @property
    def token_usage(self) -> JsonObject | None:
        """Return the v1-compatible token telemetry surface for legacy consumers."""
        if 'input_tokens' not in self.usage and 'output_tokens' not in self.usage:
            return None

        input_tokens = _integer_token_count(self.usage.get('input_tokens'))
        output_tokens = _integer_token_count(self.usage.get('output_tokens'))
        cache_read_tokens = _integer_token_count(
            self.usage.get(
                'cache_read_tokens',
                self.usage.get('cache_read_input_tokens'),
            ),
        )
        cache_creation_tokens = _integer_token_count(
            self.usage.get(
                'cache_creation_tokens',
                self.usage.get('cache_creation_input_tokens'),
            ),
        )
        reasoning_tokens = _integer_token_count(self.usage.get('reasoning_tokens'))
        return {
            'input_tokens': input_tokens,
            'output_tokens': output_tokens,
            'cache_read_tokens': cache_read_tokens,
            'cache_creation_tokens': cache_creation_tokens,
            'reasoning_tokens': reasoning_tokens,
            # Preserve the public v1 contract. Durable usage metering independently
            # accounts for all four buckets when enforcing quota and recording cost.
            'total_tokens': input_tokens + output_tokens,
        }

    @classmethod
    def from_payload(cls, payload: JsonObject) -> InvokeResponse:
        """Build an SDK result from an InvokeResult payload."""
        final_message = Message.model_validate(payload['final_message'])
        response_text = final_message.content if isinstance(final_message.content, str) else ''
        usage = payload.get('usage')
        return cls(
            final_message=final_message,
            session_id=str(payload['session_id']),
            thread_id=_thread_id_from_payload(payload),
            root_event_id=str(payload['root_event_id']),
            event_positions=cast('list[int]', payload['event_positions']),
            # payload is JsonObject (dict[str, Any]); narrow the Any-typed value
            # to the declared JsonObject shape once isinstance confirms dict.  boundary
            usage=cast('JsonObject', usage) if isinstance(usage, dict) else {},
            stop_reason=_string_or_none(payload.get('stop_reason')),
            tool_call_count=int(payload.get('tool_call_count', 0)),
            tool_names=[str(name) for name in cast('list[object]', payload.get('tool_names', []))],
            response=response_text,
            result=payload.get('result'),
            assistant_id=_string_or_none(payload.get('assistant_id')),
            artifacts=_artifact_refs_from_payload(payload, final_message),
        )


class StreamEvent(BaseModel):
    """SDK event yielded by stream iterators with a v1-visible projection.

    ``event_type`` and ``data`` always retain the canonical v2 event envelope.
    Stateful stream projection supplies the optional compatibility fields so
    legacy consumers can continue reading ``name`` and ``payload``.
    """

    model_config = ConfigDict(extra='forbid', frozen=True)

    position: StrictInt = Field(..., ge=0)
    event_type: StrictStr = Field(..., min_length=1)
    data: JsonObject
    compat_name: StrictStr | None = Field(default=None, min_length=1, exclude=True)
    compat_payload: JsonObject | None = Field(default=None, exclude=True)

    @property
    def name(self) -> str:
        """Return the v1-visible SSE event name."""
        return self.compat_name or self.event_type

    @property
    def payload(self) -> JsonObject:
        """Return the v1-compatible event payload mapping."""
        if self.compat_payload is not None:
            return self.compat_payload
        payload = self.data.get('payload')
        if isinstance(payload, dict):
            return cast('JsonObject', payload)
        return self.data

    @property
    def event(self) -> Event:
        """Validate and return the canonical event payload."""
        return Event.model_validate(self.data)


class ThreadCheckpoint(BaseModel):
    """Durable checkpoint metadata for one path through a thread."""

    model_config = ConfigDict(extra='forbid', frozen=True)

    checkpoint_id: StrictStr = Field(..., min_length=1)
    parent_checkpoint_id: StrictStr | None = Field(default=None, min_length=1)
    branch_id: StrictStr | None = Field(default=None, min_length=1)
    branch_from_message_id: StrictStr | None = Field(default=None, min_length=1)
    status: StrictStr = Field(..., min_length=1)
    message_count: StrictInt = Field(..., ge=0)
    created_at: StrictStr = Field(..., min_length=1)
    updated_at: StrictStr = Field(..., min_length=1)


class ThreadState(BaseModel):
    """Thread state and replayable history returned by the API."""

    model_config = ConfigDict(extra='forbid', frozen=True)

    thread_id: StrictStr = Field(..., min_length=1)
    status: StrictStr = Field(..., min_length=1)
    history: tuple[Message, ...] = ()
    active_session_id: StrictStr | None = Field(default=None, min_length=1)
    active_checkpoint_id: StrictStr | None = Field(default=None, min_length=1)
    active_branch_id: StrictStr | None = Field(default=None, min_length=1)
    checkpoints: tuple[ThreadCheckpoint, ...] = ()
    interrupt: JsonObject | None = None
    created_at: StrictStr = Field(..., min_length=1)
    updated_at: StrictStr = Field(..., min_length=1)

    @classmethod
    def from_payload(cls, payload: JsonObject) -> ThreadState:
        """Build thread state from the HTTP response body."""
        history = tuple(
            Message.model_validate(item)
            for item in cast('list[object]', payload.get('history', []))
        )
        interrupt = payload.get('interrupt')
        checkpoints = tuple(
            ThreadCheckpoint.model_validate(item)
            for item in cast('list[object]', payload.get('checkpoints', []))
        )
        return cls(
            thread_id=str(payload['thread_id']),
            status=str(payload['status']),
            history=history,
            active_session_id=_string_or_none(payload.get('active_session_id')),
            active_checkpoint_id=_string_or_none(payload.get('active_checkpoint_id')),
            active_branch_id=_string_or_none(payload.get('active_branch_id')),
            checkpoints=checkpoints,
            interrupt=cast('JsonObject', interrupt) if isinstance(interrupt, dict) else None,
            created_at=str(payload['created_at']),
            updated_at=str(payload['updated_at']),
        )


def _empty_input_schema() -> JsonObject:
    return {'type': 'object', 'properties': {}}


class ToolMetadata(BaseModel):
    """Local callable metadata registered through Agent.toolify."""

    model_config = ConfigDict(extra='forbid', frozen=True)

    name: StrictStr = Field(..., min_length=1)
    description: StrictStr | None = Field(default=None, min_length=1)
    always_execute: StrictBool = False
    final_tool: StrictBool = False
    input_schema: JsonObject = Field(default_factory=_empty_input_schema)
    output_schema: object | None = None
    metadata: JsonObject = Field(default_factory=dict)
    tags: tuple[StrictStr, ...] = ()
    target: Callable[..., object] | type[BaseModel] = Field(..., exclude=True)
    before_execute: Callable[..., object] | None = Field(default=None, exclude=True)
    after_execute: Callable[..., object] | None = Field(default=None, exclude=True)


def tool_card_id(payload: JsonObject) -> str | None:
    """Return the id a ``system_tool_start`` payload gives its on-screen tool card.

    Normalization derives the tool event's ``tool_id`` from this precedence, and
    a UI keys its tool cards by it. Anything that has to attach to the same card
    later - a hook firing, for one - must resolve the id the same way or it
    silently targets a card that does not exist.
    """
    raw_tool_call = payload.get('tool_call')
    tool_call: Mapping[str, object] = (
        cast('Mapping[str, object]', raw_tool_call) if isinstance(raw_tool_call, dict) else {}
    )
    candidates: tuple[object, ...] = (
        payload.get('assignment_id'),
        payload.get('tool_id'),
        payload.get('correlation_id'),
        tool_call.get('call_id'),
    )
    for candidate in candidates:
        if isinstance(candidate, str) and candidate.strip():
            return candidate.strip()
    return None


def _string_or_none(value: object) -> str | None:
    if isinstance(value, str) and value:
        return value
    return None


def _integer_token_count(value: object) -> int:
    return value if isinstance(value, int) and not isinstance(value, bool) else 0


def _thread_id_from_payload(payload: JsonObject) -> str | None:
    thread_id = _string_or_none(payload.get('thread_id'))
    if thread_id is not None:
        return thread_id
    run_config = payload.get('run_config')
    if isinstance(run_config, dict):
        return _string_or_none(cast('dict[str, object]', run_config).get('thread_id'))
    return None


def _artifact_refs_from_payload(
    payload: JsonObject,
    final_message: Message,
) -> tuple[ArtifactRef, ...]:
    raw = payload.get('artifact_refs')
    if raw is None:
        return tuple(final_message.artifact_refs or ())
    adapter = TypeAdapter(tuple[ArtifactRef, ...])
    return adapter.validate_python(raw)


__all__ = [
    'ApprovalDecision',
    'InvokeAccepted',
    'InvokeResponse',
    'JsonObject',
    'ModelDirective',
    'ProviderModelUsage',
    'ProviderUsage',
    'RoutingPreference',
    'RunOptions',
    'StreamEvent',
    'ThreadAccepted',
    'ThreadCheckpoint',
    'ThreadState',
    'ToolMetadata',
]
