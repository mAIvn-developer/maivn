"""Reusable EventBridge for streaming AppEvent v1 payloads to frontends via SSE."""

from __future__ import annotations

import asyncio
import uuid
from collections.abc import AsyncIterator, Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

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
from .runtime.identity import AssignmentAndScopeResolver, BridgeIdentityState, ToolIdentityResolver
from .runtime.normalization import BridgePayloadNormalizer
from .security import BridgeAudience, EventBridgeSecurityPolicy
from .serialization import logger, safe_json_dumps
from .streaming import generate_sse_events, reopen_bridge

# MARK: UIEvent


@dataclass
class UIEvent:
    """A single event to be delivered to the frontend via SSE."""

    type: str
    data: dict[str, Any]
    id: str = ""
    timestamp: str = ""

    def __post_init__(self) -> None:
        if not self.id:
            self.id = str(uuid.uuid4())
        if not self.timestamp:
            self.timestamp = datetime.now(UTC).isoformat()

    def to_sse(self) -> dict[str, Any]:
        """Build an ``EventSourceResponse``-compatible payload."""
        payload = {
            "id": self.id,
            "type": self.type,
            "data": self.data,
            "timestamp": self.timestamp,
        }
        return {
            "event": self.type,
            "id": self.id,
            "data": safe_json_dumps(payload),
        }

    def to_dict(self) -> dict[str, Any]:
        """Serialize for history/snapshot APIs."""
        return {
            "id": self.id,
            "type": self.type,
            "data": self.data,
            "timestamp": self.timestamp,
        }


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
        security_policy: EventBridgeSecurityPolicy | None = None,
    ) -> None:
        if security_policy is not None and audience != "internal":
            raise ValueError("Specify either audience or security_policy, not both")
        self.session_id = session_id
        self._max_history = max_history
        self._heartbeat_interval = heartbeat_interval
        self._queue: asyncio.Queue[UIEvent] = asyncio.Queue()
        self._closed = False
        self._event_history: list[UIEvent] = []
        self._security_policy = security_policy or EventBridgeSecurityPolicy(audience=audience)
        self.audience = self._security_policy.audience
        self._identity_state = BridgeIdentityState()
        self._tool_identity_resolver = ToolIdentityResolver(self._identity_state)
        self._assignment_scope_resolver = AssignmentAndScopeResolver(self._identity_state)
        self._payload_normalizer = BridgePayloadNormalizer(self._identity_state)

    async def _emit_normalized(self, event_type: str, data: dict[str, Any]) -> None:
        bridge_safe_data = self._security_policy.sanitize_event(event_type, data)
        await self._emit_packet(event_type, bridge_safe_data)

    async def _emit_packet(self, event_type: str, data: dict[str, Any]) -> None:
        if self._closed:
            logger.warning("Attempted to emit to closed bridge: %s", self.session_id)
            return

        event = UIEvent(type=event_type, data=data)
        self._event_history.append(event)
        if len(self._event_history) > self._max_history:
            del self._event_history[: -self._max_history]
        await self._queue.put(event)
        logger.debug("Emitted %s event for session %s", event_type, self.session_id)

    async def emit(self, event_type: str, data: dict[str, Any]) -> None:
        """Emit an event to the UI stream."""
        normalized_data = self._payload_normalizer.normalize_payload(event_type, data)
        await self._emit_normalized(event_type, normalized_data)

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
        """Emit an interrupt request for user input."""
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
    ) -> AsyncIterator[dict[str, Any]]:
        """Generate SSE events for streaming to client."""
        async for event in generate_sse_events(self, last_event_id=last_event_id):
            yield event

    def get_history(self) -> list[dict[str, Any]]:
        """Get event history as list of dicts."""
        return [event.to_dict() for event in self._event_history]

    def close(self) -> None:
        """Close the event bridge."""
        self._closed = True

    def reopen(self) -> None:
        """Reopen a closed bridge for a new turn."""
        reopen_bridge(self)


# MARK: BridgeRegistry


class BridgeRegistry:
    """Manages a collection of EventBridge instances keyed by session ID."""

    def __init__(self) -> None:
        self._bridges: dict[str, EventBridge] = {}

    def get(self, session_id: str) -> EventBridge | None:
        """Get an event bridge by session ID."""
        return self._bridges.get(session_id)

    def create(
        self,
        session_id: str,
        *,
        factory: Callable[[str], EventBridge] | None = None,
    ) -> EventBridge:
        """Create a new event bridge for a session."""
        if session_id in self._bridges:
            self._bridges[session_id].close()
        bridge = factory(session_id) if factory else EventBridge(session_id)
        self._bridges[session_id] = bridge
        return bridge

    def remove(self, session_id: str) -> None:
        """Remove and close an event bridge."""
        if session_id in self._bridges:
            self._bridges[session_id].close()
            del self._bridges[session_id]


__all__ = [
    "BridgeAudience",
    "BridgeRegistry",
    "EventBridge",
    "EventBridgeSecurityPolicy",
    "UIEvent",
]
