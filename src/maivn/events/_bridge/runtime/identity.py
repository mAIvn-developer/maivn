from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from .helpers import build_fallback_id, fingerprint_mapping, normalize_key_part, normalize_text

ToolBaseSignature = tuple[str, str, str | None, str | None]
ToolFullSignature = tuple[str, str, str | None, str | None, str | None]


# MARK: Identity State


@dataclass
class BridgeIdentityState:
    tool_id_aliases: dict[str, str] = field(default_factory=dict)
    active_tool_ids_by_base_signature: dict[ToolBaseSignature, list[str]] = field(
        default_factory=dict
    )
    active_tool_ids_by_full_signature: dict[ToolFullSignature, list[str]] = field(
        default_factory=dict
    )
    tool_base_signature_by_id: dict[str, ToolBaseSignature] = field(default_factory=dict)
    tool_full_signature_by_id: dict[str, ToolFullSignature] = field(default_factory=dict)
    agent_assignment_aliases: dict[str, str] = field(default_factory=dict)
    agent_assignment_id_by_key: dict[tuple[str | None, str], str] = field(default_factory=dict)
    scope_id_aliases: dict[str, str] = field(default_factory=dict)
    scope_id_by_key: dict[tuple[str, str], str] = field(default_factory=dict)


# MARK: Tool Identity


@dataclass
class ToolIdentityResolver:
    state: BridgeIdentityState

    def _tool_base_signature(
        self,
        *,
        tool_name: str,
        tool_type: str | None,
        agent_name: str | None,
        swarm_name: str | None,
    ) -> ToolBaseSignature:
        return (
            normalize_key_part(tool_type) or "func",
            normalize_key_part(tool_name) or "unknown",
            normalize_key_part(agent_name),
            normalize_key_part(swarm_name),
        )

    def _tool_full_signature(
        self,
        *,
        tool_name: str,
        tool_type: str | None,
        agent_name: str | None,
        swarm_name: str | None,
        args: dict[str, Any] | None,
    ) -> ToolFullSignature:
        base_signature = self._tool_base_signature(
            tool_name=tool_name,
            tool_type=tool_type,
            agent_name=agent_name,
            swarm_name=swarm_name,
        )
        return (*base_signature, fingerprint_mapping(args))

    @staticmethod
    def _append_unique(mapping: dict[Any, list[str]], key: Any, value: str) -> None:
        existing = mapping.setdefault(key, [])
        if value not in existing:
            existing.append(value)

    @staticmethod
    def _remove_from_mapping(mapping: dict[Any, list[str]], key: Any, value: str) -> None:
        existing = mapping.get(key)
        if not existing:
            return
        mapping[key] = [item for item in existing if item != value]
        if not mapping[key]:
            del mapping[key]

    def _find_unique_active_tool_by_base_signature(self, key: ToolBaseSignature) -> str | None:
        candidates = [
            tool_id
            for tool_id in self.state.active_tool_ids_by_base_signature.get(key, [])
            if tool_id in self.state.tool_base_signature_by_id
        ]
        if len(candidates) == 1:
            return candidates[0]
        return None

    def _find_unique_active_tool_by_full_signature(self, key: ToolFullSignature) -> str | None:
        candidates = [
            tool_id
            for tool_id in self.state.active_tool_ids_by_full_signature.get(key, [])
            if tool_id in self.state.tool_base_signature_by_id
        ]
        if len(candidates) == 1:
            return candidates[0]
        return None

    def _register_active_tool(
        self,
        canonical_tool_id: str,
        *,
        base_signature: ToolBaseSignature,
        full_signature: ToolFullSignature,
    ) -> None:
        self.state.tool_base_signature_by_id[canonical_tool_id] = base_signature
        self.state.tool_full_signature_by_id[canonical_tool_id] = full_signature
        self._append_unique(
            self.state.active_tool_ids_by_base_signature,
            base_signature,
            canonical_tool_id,
        )
        self._append_unique(
            self.state.active_tool_ids_by_full_signature,
            full_signature,
            canonical_tool_id,
        )

    def _retire_active_tool(self, canonical_tool_id: str) -> None:
        base_signature = self.state.tool_base_signature_by_id.pop(canonical_tool_id, None)
        full_signature = self.state.tool_full_signature_by_id.pop(canonical_tool_id, None)
        if base_signature is not None:
            self._remove_from_mapping(
                self.state.active_tool_ids_by_base_signature,
                base_signature,
                canonical_tool_id,
            )
        if full_signature is not None:
            self._remove_from_mapping(
                self.state.active_tool_ids_by_full_signature,
                full_signature,
                canonical_tool_id,
            )

    def resolve_tool_id(
        self,
        *,
        tool_name: str,
        tool_id: str,
        status: str,
        args: dict[str, Any] | None,
        agent_name: str | None,
        swarm_name: str | None,
        tool_type: str | None,
    ) -> str:
        normalized_tool_id = normalize_text(tool_id) or tool_id
        existing_alias = self.state.tool_id_aliases.get(normalized_tool_id)
        base_signature = self._tool_base_signature(
            tool_name=tool_name,
            tool_type=tool_type,
            agent_name=agent_name,
            swarm_name=swarm_name,
        )
        full_signature = self._tool_full_signature(
            tool_name=tool_name,
            tool_type=tool_type,
            agent_name=agent_name,
            swarm_name=swarm_name,
            args=args,
        )
        args_fingerprint = full_signature[-1]
        normalized_status = (normalize_key_part(status) or "executing").lower()

        if existing_alias is not None:
            canonical_tool_id = existing_alias
        elif normalized_status == "executing":
            canonical_tool_id = (
                self._find_unique_active_tool_by_full_signature(full_signature)
                or (
                    self._find_unique_active_tool_by_base_signature(base_signature)
                    if args_fingerprint is None
                    else None
                )
                or normalized_tool_id
            )
        else:
            canonical_tool_id = (
                self._find_unique_active_tool_by_base_signature(base_signature)
                or normalized_tool_id
            )

        self.state.tool_id_aliases[normalized_tool_id] = canonical_tool_id
        if normalized_status == "executing":
            self._register_active_tool(
                canonical_tool_id,
                base_signature=base_signature,
                full_signature=full_signature,
            )
        elif normalized_status in {"completed", "failed"}:
            self._retire_active_tool(canonical_tool_id)

        return canonical_tool_id


# MARK: Assignment and Scope Identity


@dataclass
class AssignmentAndScopeResolver:
    state: BridgeIdentityState

    def resolve_agent_assignment_id(
        self,
        *,
        agent_name: str,
        assignment_id: str | None,
        swarm_name: str | None,
    ) -> str:
        normalized_assignment_id = normalize_text(assignment_id)
        agent_key = (
            normalize_key_part(swarm_name),
            normalize_key_part(agent_name) or "unknown-agent",
        )
        if (
            normalized_assignment_id
            and normalized_assignment_id in self.state.agent_assignment_aliases
        ):
            return self.state.agent_assignment_aliases[normalized_assignment_id]
        canonical_assignment_id = self.state.agent_assignment_id_by_key.get(agent_key)
        if canonical_assignment_id is None:
            canonical_assignment_id = normalized_assignment_id or build_fallback_id(
                "agent",
                swarm_name,
                agent_name,
            )
            self.state.agent_assignment_id_by_key[agent_key] = canonical_assignment_id
        if normalized_assignment_id:
            self.state.agent_assignment_aliases[normalized_assignment_id] = canonical_assignment_id
        return canonical_assignment_id

    def resolve_scope_id(
        self,
        *,
        scope_id: str | None,
        scope_name: str | None,
        scope_type: str | None,
    ) -> str | None:
        normalized_scope_type = normalize_key_part(scope_type)
        normalized_scope_name = normalize_text(scope_name)
        normalized_scope_id = normalize_text(scope_id)
        if (
            normalized_scope_type is None
            and normalized_scope_name is None
            and normalized_scope_id is None
        ):
            return None
        if normalized_scope_id and normalized_scope_id in self.state.scope_id_aliases:
            return self.state.scope_id_aliases[normalized_scope_id]
        scope_key = (
            normalized_scope_type or "scope",
            normalize_key_part(normalized_scope_name)
            or normalize_key_part(normalized_scope_id)
            or "unknown",
        )
        canonical_scope_id = self.state.scope_id_by_key.get(scope_key)
        if canonical_scope_id is None:
            canonical_scope_id = normalized_scope_id or build_fallback_id(
                normalized_scope_type or "scope",
                normalized_scope_name,
            )
            self.state.scope_id_by_key[scope_key] = canonical_scope_id
        if normalized_scope_id:
            self.state.scope_id_aliases[normalized_scope_id] = canonical_scope_id
        return canonical_scope_id
