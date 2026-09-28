"""Versioned public models for customer-visible SDK run telemetry."""

from __future__ import annotations

from collections.abc import Callable
from datetime import datetime
from typing import Final, Literal, TypeAlias

from pydantic import BaseModel, ConfigDict, Field, StrictFloat, StrictInt, StrictStr

TELEMETRY_SCHEMA_VERSION: Final = '1'

TelemetryEventKind: TypeAlias = Literal['run', 'tool', 'message', 'status', 'unknown']
TelemetryListener: TypeAlias = Callable[['RunTelemetryEvent'], None]


class _TelemetryModel(BaseModel):
    """Closed immutable base for the public telemetry schema."""

    model_config = ConfigDict(extra='forbid', frozen=True)


class RunTelemetryScope(_TelemetryModel):
    """Safe execution-scope names associated with a run event."""

    agent_name: StrictStr | None = Field(default=None, min_length=1)
    swarm_name: StrictStr | None = Field(default=None, min_length=1)


class ToolCallTelemetry(_TelemetryModel):
    """Safe identity and lifecycle metadata for one tool call."""

    call_id: StrictStr | None = Field(default=None, min_length=1)
    name: StrictStr = Field(..., min_length=1)
    tool_type: StrictStr | None = Field(default=None, min_length=1)
    namespace: StrictStr | None = Field(default=None, min_length=1)
    version: StrictStr | None = Field(default=None, min_length=1)


class TokenUsageTelemetry(_TelemetryModel):
    """Token-count metadata reported for a completed run."""

    input_tokens: StrictInt = Field(default=0, ge=0)
    output_tokens: StrictInt = Field(default=0, ge=0)
    cache_read_tokens: StrictInt = Field(default=0, ge=0)
    cache_creation_tokens: StrictInt = Field(default=0, ge=0)
    reasoning_tokens: StrictInt = Field(default=0, ge=0)
    total_tokens: StrictInt = Field(default=0, ge=0)


class RunTelemetryEvent(_TelemetryModel):
    """One versioned, customer-visible run event projected for telemetry."""

    schema_version: Literal['1'] = TELEMETRY_SCHEMA_VERSION
    event_name: StrictStr = Field(..., min_length=1)
    event_kind: TelemetryEventKind
    position: StrictInt = Field(..., ge=0)
    timestamp: datetime | None = None
    event_id: StrictStr | None = Field(default=None, min_length=1)
    session_id: StrictStr | None = Field(default=None, min_length=1)
    thread_id: StrictStr | None = Field(default=None, min_length=1)
    parent_session_id: StrictStr | None = Field(default=None, min_length=1)
    root_event_id: StrictStr | None = Field(default=None, min_length=1)
    correlation_id: StrictStr | None = Field(default=None, min_length=1)
    status: StrictStr | None = Field(default=None, min_length=1)
    duration_ms: StrictFloat | StrictInt | None = Field(default=None, ge=0)
    scope: RunTelemetryScope | None = None
    tool_call: ToolCallTelemetry | None = None
    token_usage: TokenUsageTelemetry | None = None


__all__ = [
    'TELEMETRY_SCHEMA_VERSION',
    'RunTelemetryEvent',
    'RunTelemetryScope',
    'TelemetryListener',
    'TokenUsageTelemetry',
    'ToolCallTelemetry',
]
