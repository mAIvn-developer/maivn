# pyright: strict
"""Pydantic descriptor models and state types for the AppEvent v1 contract."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import ClassVar, TypeAlias

from pydantic import BaseModel, ConfigDict, Field, JsonValue

from .._internal.core.entities.sse_event import SSEEvent as RawSSEEvent
from .._internal.utils.reporting.app_event_payloads import APP_EVENT_CONTRACT_VERSION

# MARK: Types

JsonObject: TypeAlias = dict[str, JsonValue]


# MARK: Descriptor Models


class ScopeDescriptor(BaseModel):
    model_config: ClassVar[ConfigDict] = ConfigDict(extra="allow")

    id: str | None = None
    name: str | None = None
    type: str | None = None


class ParticipantDescriptor(BaseModel):
    model_config: ClassVar[ConfigDict] = ConfigDict(extra="allow")

    key: str | None = None
    name: str | None = None
    role: str | None = None


class LifecycleDescriptor(BaseModel):
    model_config: ClassVar[ConfigDict] = ConfigDict(extra="allow")

    phase: str | None = None
    parent_id: str | None = None
    root_id: str | None = None
    run_id: str | None = None
    turn_id: str | None = None


class ToolDescriptor(BaseModel):
    model_config: ClassVar[ConfigDict] = ConfigDict(extra="allow")

    id: str | None = None
    name: str | None = None
    type: str | None = None
    status: str | None = None
    args: JsonObject = Field(default_factory=dict)
    result: JsonValue = None
    error: str | None = None


class AssistantDescriptor(BaseModel):
    model_config: ClassVar[ConfigDict] = ConfigDict(extra="allow")

    id: str | None = None
    delta: str | None = None
    # Signals that this chunk represents a fresh stream (reevaluate cycle,
    # synthesis restart, mid-stream revision) and the UI should OVERWRITE
    # the assistant bubble with this chunk's text rather than append.
    # Declared explicitly so consumers can read it via plain attribute
    # access instead of fishing through ``model_extra`` (which is a
    # Pydantic implementation detail and not guaranteed to survive
    # serialize/deserialize roundtrips).
    replace_content: bool = False


class AssignmentDescriptor(BaseModel):
    model_config: ClassVar[ConfigDict] = ConfigDict(extra="allow")

    id: str | None = None
    agent_name: str | None = None
    status: str | None = None
    task: str | None = None
    swarm_name: str | None = None
    result: JsonValue = None
    error: str | None = None


class EnrichmentDescriptor(BaseModel):
    model_config: ClassVar[ConfigDict] = ConfigDict(extra="allow")

    phase: str | None = None
    message: str | None = None
    memory: JsonObject | None = None
    redaction: JsonObject | None = None
    reevaluate: JsonObject | None = None


class InterruptDescriptor(BaseModel):
    model_config: ClassVar[ConfigDict] = ConfigDict(extra="allow")

    id: str | None = None
    checkpoint_id: str | None = None
    assignment_id: str | None = None
    data_key: str | None = None
    arg_name: str | None = None
    prompt: str | None = None
    tool_name: str | None = None
    input_type: str | None = None
    choices: list[str] = Field(default_factory=list)
    number: int | None = None
    total: int | None = None


class OutputDescriptor(BaseModel):
    model_config: ClassVar[ConfigDict] = ConfigDict(extra="allow")

    response: str | None = None
    result: JsonValue = None
    token_usage: JsonObject | None = None


class ErrorInfoDescriptor(BaseModel):
    model_config: ClassVar[ConfigDict] = ConfigDict(extra="allow")

    message: str | None = None
    details: JsonObject = Field(default_factory=dict)


class SessionDescriptor(BaseModel):
    model_config: ClassVar[ConfigDict] = ConfigDict(extra="allow")

    id: str | None = None
    assistant_id: str | None = None


class ChunkDescriptor(BaseModel):
    model_config: ClassVar[ConfigDict] = ConfigDict(extra="allow")

    text: str | None = None
    progress: float | None = None


class HookDescriptor(BaseModel):
    """Per-fire description of a developer-registered hook callback.

    Emitted when a tool or scope (Agent / Swarm) ``before_execute`` /
    ``after_execute`` callback runs. ``target_*`` fields identify which
    on-screen card the firing should attach to.
    """

    model_config: ClassVar[ConfigDict] = ConfigDict(extra="allow")

    name: str | None = None
    """Display name of the hook callable (its ``__name__``)."""
    stage: str | None = None
    """``"before"`` or ``"after"``."""
    status: str | None = None
    """``"completed"`` or ``"failed"``."""
    error: str | None = None
    """User-facing error message when ``status == "failed"``."""
    elapsed_ms: int | None = None
    """How long the hook callable took to run."""
    target_type: str | None = None
    """``"tool"``, ``"agent"``, or ``"swarm"`` — which card this attaches to."""
    target_id: str | None = None
    """For tools: the per-invocation event ID (correlates to the tool card).
    For agents/swarms: the agent id or swarm name used by the scope card."""
    target_name: str | None = None
    """Display name of the target card."""
    source: str | None = None
    """``"tool"`` / ``"scope"`` / ``"swarm"`` — which level *defined* the hook.

    Lets a UI label a pill even when all three sources hook the same tool
    execution. ``None`` when emitted by callers that predate the field.
    """


# MARK: AppEvent


class AppEvent(BaseModel):
    """Canonical normalized event envelope emitted by the SDK event pipeline.

    ``AppEvent`` is the typed shape consumers receive from
    :func:`~maivn.events.normalize_stream`. ``event_name`` selects which of
    the optional descriptor fields (``tool``, ``assistant``, ``assignment``,
    …) are populated; reporters use that name to dispatch to the right
    handler. ``contract_version`` lets consumers gate on the schema version
    when the SDK evolves.
    """

    model_config: ClassVar[ConfigDict] = ConfigDict(extra="allow")

    contract_version: str = APP_EVENT_CONTRACT_VERSION
    event_name: str
    event_kind: str | None = None
    scope: ScopeDescriptor | None = None
    participant: ParticipantDescriptor | None = None
    lifecycle: LifecycleDescriptor | None = None
    tool: ToolDescriptor | None = None
    assistant: AssistantDescriptor | None = None
    assignment: AssignmentDescriptor | None = None
    enrichment: EnrichmentDescriptor | None = None
    interrupt: InterruptDescriptor | None = None
    output: OutputDescriptor | None = None
    error_info: ErrorInfoDescriptor | None = None
    session: SessionDescriptor | None = None
    chunk: ChunkDescriptor | None = None
    hook: HookDescriptor | None = None


# MARK: Normalization State


@dataclass
class NormalizedStreamState:
    streaming_text_by_id: dict[str, str] = field(default_factory=dict)
    started_system_tools: dict[str, str] = field(default_factory=dict)
    reported_tool_ids: set[str] = field(default_factory=set)
    pending_model_tools: list[dict[str, str]] = field(default_factory=list)
    last_model_tool_result: JsonObject | None = None
    # One-shot flag set whenever a ``reevaluate_accrued`` event fires. The
    # NEXT streamed assistant chunk — regardless of which ``assistant_id``
    # the server attaches to it — is emitted with ``replace_content=True``
    # on the wire so the UI overwrites the prior cycle's text instead of
    # appending to it. The flag is consumed by the first chunk that uses it.
    # Per-assistant tracking is too brittle: when a fresh thread emits chunks
    # under a new ``assistant_id``, a per-ID pending set never matches.
    next_assistant_chunk_replaces: bool = False


__all__ = [
    "APP_EVENT_CONTRACT_VERSION",
    "AppEvent",
    "AssignmentDescriptor",
    "AssistantDescriptor",
    "ChunkDescriptor",
    "EnrichmentDescriptor",
    "ErrorInfoDescriptor",
    "HookDescriptor",
    "InterruptDescriptor",
    "LifecycleDescriptor",
    "NormalizedStreamState",
    "OutputDescriptor",
    "ParticipantDescriptor",
    "RawSSEEvent",
    "ScopeDescriptor",
    "SessionDescriptor",
    "ToolDescriptor",
]
