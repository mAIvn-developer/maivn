"""Reusable EventBridge for streaming AppEvent v1 payloads to frontends via SSE."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncGenerator
from typing import Any, Literal

from .dedup import build_interrupt_fingerprint, build_status_fingerprint
from .emitters import (
    emit_agent_assignment,
    emit_assistant_chunk,
    emit_enrichment,
    emit_error,
    emit_final,
    emit_interrupt_required,
    emit_status_message,
    emit_system_tool_chunk,
    emit_system_tool_complete,
    emit_system_tool_start,
    emit_tool_event,
)
from .queueing import enqueue_event
from .registry import BridgeRegistry
from .runtime.identity import AssignmentAndScopeResolver, BridgeIdentityState, ToolIdentityResolver
from .runtime.normalization import BridgePayloadNormalizer
from .schema import ValidationMode, validate_event
from .security import BridgeAudience, EventBridgeSecurityPolicy
from .serialization import logger
from .streaming import generate_sse_events, reopen_bridge
from .ui_event import UIEvent

BackpressurePolicy = Literal["block", "drop_oldest", "drop_newest"]
_VALID_BACKPRESSURE: frozenset[str] = frozenset({"block", "drop_oldest", "drop_newest"})
_VALID_VALIDATION_MODES: frozenset[str] = frozenset({"off", "warn", "strict"})


# MARK: EventBridge


class EventBridge:
    """Bridge between SDK execution events and a frontend SSE stream."""

    TERMINAL_EVENTS: frozenset[str] = frozenset({"final", "error", "session_end"})

    def __init__(
        self,
        session_id: str,
        *,
        max_history: int = 500,
        heartbeat_interval: float = 15.0,
        audience: BridgeAudience = "internal",
        queue_maxsize: int = 0,
        backpressure: BackpressurePolicy = "block",
        schema_validation: ValidationMode = "warn",
        dedupe_interrupts: bool = True,
        dedupe_status_messages: bool = False,
        reset_on_session_start: bool = True,
    ) -> None:
        if max_history < 1:
            raise ValueError("max_history must be >= 1")
        if heartbeat_interval <= 0:
            raise ValueError("heartbeat_interval must be > 0")
        if queue_maxsize < 0:
            raise ValueError("queue_maxsize must be >= 0 (0 = unbounded)")
        if backpressure not in _VALID_BACKPRESSURE:
            raise ValueError(
                f"backpressure must be one of {sorted(_VALID_BACKPRESSURE)}, got {backpressure!r}"
            )
        if schema_validation not in _VALID_VALIDATION_MODES:
            raise ValueError(
                f"schema_validation must be one of {sorted(_VALID_VALIDATION_MODES)}, "
                f"got {schema_validation!r}"
            )

        self.session_id = session_id
        self._max_history = max_history
        self._heartbeat_interval = heartbeat_interval
        self._queue_maxsize = queue_maxsize
        self._backpressure: BackpressurePolicy = backpressure
        self._schema_validation: ValidationMode = schema_validation
        self._queue: asyncio.Queue[UIEvent] = asyncio.Queue(maxsize=queue_maxsize)
        self._closed = False
        self._event_history: list[UIEvent] = []
        # Total events ever appended to history; lets us detect when older
        # events have aged out of the buffer so reconnects with a missing
        # cursor can be diagnosed.
        self._history_evictions = 0
        self._security_policy = EventBridgeSecurityPolicy(audience=audience)
        self.audience = self._security_policy.audience
        self._identity_state = BridgeIdentityState()
        self._tool_identity_resolver = ToolIdentityResolver(self._identity_state)
        self._assignment_scope_resolver = AssignmentAndScopeResolver(self._identity_state)
        self._payload_normalizer = BridgePayloadNormalizer(self._identity_state)
        # Dedup of overlapping logical emissions. Reset by reopen() and (when
        # ``reset_on_session_start`` is set) on the ``session_start`` packet,
        # whichever comes first.
        self._dedupe_interrupts = dedupe_interrupts
        self._dedupe_status_messages = dedupe_status_messages
        self._reset_on_session_start = reset_on_session_start
        self._emitted_interrupt_fingerprints: set[tuple[str, str]] = set()
        self._last_status_fingerprint: tuple[str, str] | None = None

    async def _emit_normalized(self, event_type: str, data: dict[str, Any]) -> None:
        validate_event(event_type, data, mode=self._schema_validation)
        bridge_safe_data = self._security_policy.sanitize_event(event_type, data)
        await self._emit_packet(event_type, bridge_safe_data)

    async def _emit_packet(self, event_type: str, data: dict[str, Any]) -> None:
        if self._closed:
            logger.warning("Attempted to emit to closed bridge: %s", self.session_id)
            return

        event = UIEvent(type=event_type, data=data)
        self._event_history.append(event)
        if len(self._event_history) > self._max_history:
            evicted = len(self._event_history) - self._max_history
            self._history_evictions += evicted
            del self._event_history[:evicted]

        await self._enqueue_event(event)
        logger.debug("Emitted %s event for session %s", event_type, self.session_id)

    async def _enqueue_event(self, event: UIEvent) -> None:
        """Place an event on the live queue, applying the backpressure policy."""
        await enqueue_event(self, event)

    async def emit(self, event_type: str, data: dict[str, Any]) -> None:
        """Emit an event to the UI stream."""
        if self._reset_on_session_start and event_type == "session_start":
            self._reset_dedup_state()
        if (
            self._dedupe_status_messages
            and event_type == "status_message"
            and self._should_drop_status_message(data)
        ):
            return
        normalized_data = self._payload_normalizer.normalize_payload(event_type, data)
        if event_type == "interrupt_required" and self._should_drop_interrupt_payload(
            normalized_data
        ):
            return
        await self._emit_normalized(event_type, normalized_data)

    # MARK: Dedup state

    def _reset_dedup_state(self) -> None:
        """Clear interrupt + status_message dedup state. Called on ``reopen()``
        and on ``session_start`` (when enabled).
        """
        self._emitted_interrupt_fingerprints.clear()
        self._last_status_fingerprint = None

    def _should_drop_interrupt(
        self,
        *,
        prompt: str,
        data_key: str,
        arg_name: str | None,
    ) -> bool:
        if not self._dedupe_interrupts:
            return False
        fingerprint = build_interrupt_fingerprint(
            prompt=prompt,
            data_key=data_key,
            arg_name=arg_name,
        )
        if fingerprint in self._emitted_interrupt_fingerprints:
            return True
        self._emitted_interrupt_fingerprints.add(fingerprint)
        return False

    def _should_drop_interrupt_payload(self, data: dict[str, Any]) -> bool:
        prompt = data.get("prompt")
        data_key = data.get("data_key")
        if not isinstance(prompt, str) or not isinstance(data_key, str):
            return False

        arg_name = data.get("arg_name")
        return self._should_drop_interrupt(
            prompt=prompt,
            data_key=data_key,
            arg_name=arg_name if isinstance(arg_name, str) else None,
        )

    def _should_drop_status_message(self, data: dict[str, Any]) -> bool:
        if not self._dedupe_status_messages:
            return False
        fingerprint = build_status_fingerprint(data)
        if fingerprint is None:
            return False
        if fingerprint == self._last_status_fingerprint:
            return True
        self._last_status_fingerprint = fingerprint
        return False

    # MARK: Tool Events

    async def emit_tool_event(
        self,
        tool_name: str,
        tool_id: str,
        status: str,
        args: dict[str, Any] | None = None,
        result: Any = None,
        error: str | None = None,
        agent_name: str | None = None,
        swarm_name: str | None = None,
        tool_type: str | None = None,
    ) -> None:
        """Emit a tool execution event."""
        canonical_tool_id = self._tool_identity_resolver.resolve_tool_id(
            tool_name=tool_name,
            tool_id=tool_id,
            status=status,
            args=args,
            agent_name=agent_name,
            swarm_name=swarm_name,
            tool_type=tool_type,
        )
        await emit_tool_event(
            self._emit_normalized,
            tool_name=tool_name,
            tool_id=canonical_tool_id,
            status=status,
            args=args,
            result=result,
            error=error,
            agent_name=agent_name,
            swarm_name=swarm_name,
            tool_type=tool_type,
        )

    async def emit_system_tool_start(
        self,
        tool_type: str,
        tool_id: str,
        params: dict[str, Any] | None = None,
        agent_name: str | None = None,
        swarm_name: str | None = None,
    ) -> None:
        """Emit a system tool start event."""
        canonical_tool_id = self._tool_identity_resolver.resolve_tool_id(
            tool_name=tool_type,
            tool_id=tool_id,
            status="executing",
            args=params,
            agent_name=agent_name,
            swarm_name=swarm_name,
            tool_type="system",
        )
        await emit_system_tool_start(
            self._emit_normalized,
            tool_type=tool_type,
            tool_id=canonical_tool_id,
            params=params,
            agent_name=agent_name,
            swarm_name=swarm_name,
        )

    async def emit_system_tool_chunk(
        self,
        tool_id: str,
        text: str,
        progress: float | None = None,
    ) -> None:
        """Emit a system tool streaming chunk."""
        canonical_tool_id = self._identity_state.tool_id_aliases.get(tool_id, tool_id)
        await emit_system_tool_chunk(
            self._emit_normalized,
            tool_id=canonical_tool_id,
            text=text,
            progress=progress,
        )

    async def emit_system_tool_complete(
        self,
        tool_id: str,
        result: Any,
    ) -> None:
        """Emit a system tool completion event."""
        canonical_tool_id = self._identity_state.tool_id_aliases.get(tool_id, tool_id)
        await emit_system_tool_complete(
            self._emit_normalized,
            tool_id=canonical_tool_id,
            result=result,
        )

    # MARK: Assistant Events

    async def emit_assistant_chunk(
        self,
        assistant_id: str,
        text: str,
    ) -> None:
        """Emit a streamed assistant response chunk."""
        await emit_assistant_chunk(self._emit_normalized, assistant_id=assistant_id, text=text)

    async def emit_status_message(
        self,
        assistant_id: str,
        message: str,
    ) -> None:
        """Emit a standalone status message."""
        if self._dedupe_status_messages and self._should_drop_status_message(
            {"assistant_id": assistant_id, "message": message}
        ):
            return
        await emit_status_message(self._emit_normalized, assistant_id=assistant_id, message=message)

    async def emit_interrupt_required(
        self,
        interrupt_id: str,
        data_key: str,
        prompt: str,
        arg_name: str | None = None,
        tool_name: str | None = None,
        checkpoint_id: str | None = None,
        assignment_id: str | None = None,
        interrupt_number: int = 1,
        total_interrupts: int = 1,
        input_type: str = "text",
        choices: list[str] | None = None,
    ) -> None:
        """Emit an interrupt request for user input.

        Logical duplicates (same prompt + arg_name/data_key) are dropped
        per turn when ``dedupe_interrupts`` is enabled (default). The
        reporter path and the contract-stream replay can otherwise produce
        different interrupt IDs for the same prompt/field pair, which would
        surface as duplicate prompts in the frontend.
        """
        if self._should_drop_interrupt(prompt=prompt, data_key=data_key, arg_name=arg_name):
            return
        await emit_interrupt_required(
            self._emit_normalized,
            interrupt_id=interrupt_id,
            data_key=data_key,
            prompt=prompt,
            arg_name=arg_name,
            tool_name=tool_name,
            checkpoint_id=checkpoint_id,
            assignment_id=assignment_id,
            interrupt_number=interrupt_number,
            total_interrupts=total_interrupts,
            input_type=input_type,
            choices=choices,
        )

    # MARK: Assignment and Terminal Events

    async def emit_agent_assignment(
        self,
        agent_name: str,
        status: str,
        assignment_id: str | None = None,
        swarm_name: str | None = None,
        task: str | None = None,
        error: str | None = None,
        result: Any | None = None,
    ) -> None:
        """Emit an agent assignment event (for swarms)."""
        canonical_assignment_id = self._assignment_scope_resolver.resolve_agent_assignment_id(
            agent_name=agent_name,
            assignment_id=assignment_id,
            swarm_name=swarm_name,
        )
        await emit_agent_assignment(
            self._emit_normalized,
            agent_name=agent_name,
            status=status,
            assignment_id=canonical_assignment_id,
            swarm_name=swarm_name,
            task=task,
            error=error,
            result=result,
        )

    async def emit_enrichment(
        self,
        *,
        phase: str,
        message: str,
        scope_id: str | None = None,
        scope_name: str | None = None,
        scope_type: str | None = None,
        memory: dict[str, Any] | None = None,
        redaction: dict[str, Any] | None = None,
    ) -> None:
        """Emit an enrichment phase change event."""
        canonical_scope_id = self._assignment_scope_resolver.resolve_scope_id(
            scope_id=scope_id,
            scope_name=scope_name,
            scope_type=scope_type,
        )
        await emit_enrichment(
            self._emit_normalized,
            phase=phase,
            message=message,
            scope_id=canonical_scope_id,
            scope_name=scope_name,
            scope_type=scope_type,
            memory=memory,
            redaction=redaction,
        )

    async def emit_final(self, response: str, result: Any = None) -> None:
        """Emit final completion event."""
        await emit_final(self._emit_normalized, response=response, result=result)

    async def emit_error(self, error: str, details: dict[str, Any] | None = None) -> None:
        """Emit an error event."""
        await emit_error(self._emit_normalized, error=error, details=details)

    # MARK: Streaming and Lifecycle

    async def generate_sse(
        self,
        last_event_id: str | None = None,
        *,
        heartbeat_interval: float | None = None,
    ) -> AsyncGenerator[dict[str, Any], None]:
        """Generate SSE events for streaming to client.

        Keep-alives are emitted as SSE comment frames (``: keepalive ...\\n\\n``)
        that browsers silently ignore — frontends do not need to subscribe
        to or filter a heartbeat event type.

        ``heartbeat_interval`` overrides the bridge default for this stream
        only — useful when a specific client lives behind a proxy with an
        aggressive idle timeout.
        """
        async for event in generate_sse_events(
            self,
            last_event_id=last_event_id,
            heartbeat_interval=heartbeat_interval,
        ):
            yield event

    def get_history(self) -> list[dict[str, Any]]:
        """Get event history as list of dicts."""
        return [event.to_dict() for event in self._event_history]

    def _reset_identity_state(self) -> None:
        """Drop all tool / assignment / scope identity aliases.

        Identity state lasts for the logical lifetime of a turn. ``reopen()``
        resets it as part of the new-turn lifecycle; there is no use case
        for clearing identity without also clearing history + queue, so
        this stays internal.
        """
        self._identity_state = BridgeIdentityState()
        self._tool_identity_resolver = ToolIdentityResolver(self._identity_state)
        self._assignment_scope_resolver = AssignmentAndScopeResolver(self._identity_state)
        self._payload_normalizer = BridgePayloadNormalizer(self._identity_state)

    def close(self) -> None:
        """Close the event bridge."""
        self._closed = True

    def reopen(self) -> None:
        """Reopen a closed bridge for a new turn.

        Clears history, the live queue, identity-state aliases, and dedup
        state so the next turn starts from a clean slate.
        """
        reopen_bridge(self)
        self._reset_identity_state()
        self._reset_dedup_state()
        self._history_evictions = 0


__all__ = [
    "BridgeAudience",
    "BridgeRegistry",
    "EventBridge",
    "EventBridgeSecurityPolicy",
    "UIEvent",
]
