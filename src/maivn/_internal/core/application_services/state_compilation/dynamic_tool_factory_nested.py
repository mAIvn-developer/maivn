from __future__ import annotations

from typing import Any

from maivn_shared import SWARM_AGENT_INVOCATION_METADATA_KEY, MemoryConfig

from maivn._internal.utils.reporting.context import current_sdk_delivery_mode


class DynamicToolFactoryNestedInvocationMixin:
    @staticmethod
    def _normalize_included_nested_synthesis(value: Any) -> bool | str:
        if isinstance(value, bool):
            return value
        if isinstance(value, str):
            normalized = value.strip().lower()
            if normalized == "auto":
                return "auto"
            if normalized in {"true", "1", "yes", "on"}:
                return True
            if normalized in {"false", "0", "no", "off"}:
                return False
        return "auto"

    def _build_nested_invocation_metadata(
        self,
        *,
        agent: Any,
        swarm_scope: Any,
        agent_id: str,
        use_as_final_output: bool,
        resolved_nested_synthesis: bool | str,
        memory_recall_turn_active: bool = False,
    ) -> dict[str, Any]:
        metadata: dict[str, Any] = {
            SWARM_AGENT_INVOCATION_METADATA_KEY: True,
            "swarm_use_as_final_output": use_as_final_output,
            "swarm_invoked_agent_id": getattr(agent, "id", agent_id),
            "swarm_invoked_agent_name": getattr(agent, "name", None),
            "swarm_included_nested_synthesis": resolved_nested_synthesis,
            "maivn_sdk_delivery_mode": current_sdk_delivery_mode.get(),
        }
        if memory_recall_turn_active:
            metadata["memory_recall_turn_active"] = True
        self._merge_memory_assets(
            metadata,
            scope=agent,
            default_agent_id=getattr(agent, "id", None),
            default_swarm_id=getattr(swarm_scope, "id", None),
        )
        self._merge_memory_assets(
            metadata,
            scope=swarm_scope,
            default_swarm_id=getattr(swarm_scope, "id", None),
        )

        return metadata

    @staticmethod
    def _coerce_memory_config(value: Any) -> MemoryConfig | None:
        if isinstance(value, MemoryConfig):
            return value
        if isinstance(value, dict):
            return MemoryConfig.model_validate(value)
        return None

    def _resolve_scope_memory_config(self, scope: Any) -> MemoryConfig | None:
        resolver = getattr(scope, "resolve_memory_config", None)
        if callable(resolver):
            resolved = resolver(None)
            if isinstance(resolved, MemoryConfig) and resolved.is_configured():
                return resolved
        return self._coerce_memory_config(getattr(scope, "memory_config", None))

    def _build_nested_invocation_memory_config(
        self,
        *,
        agent: Any,
        swarm_scope: Any,
    ) -> MemoryConfig | None:
        return MemoryConfig.merge(
            self._resolve_scope_memory_config(agent),
            self._resolve_scope_memory_config(swarm_scope),
        )

    @staticmethod
    def _merge_payload_list(
        metadata: dict[str, Any],
        key: str,
        incoming: list[dict[str, Any]],
        *,
        identity_keys: tuple[str, ...],
    ) -> None:
        existing_raw = metadata.get(key)
        existing = (
            [item for item in existing_raw if isinstance(item, dict)]
            if isinstance(existing_raw, list)
            else []
        )

        merged: list[dict[str, Any]] = list(existing)
        seen: set[str] = set()

        def _identity(item: dict[str, Any]) -> str:
            for candidate_key in identity_keys:
                raw_value = item.get(candidate_key)
                if isinstance(raw_value, str) and raw_value.strip():
                    return f"{candidate_key}:{raw_value.strip().lower()}"
            return ""

        for item in merged:
            identifier = _identity(item)
            if identifier:
                seen.add(identifier)

        for item in incoming:
            if not isinstance(item, dict):
                continue
            identifier = _identity(item)
            if identifier and identifier in seen:
                continue
            if identifier:
                seen.add(identifier)
            merged.append(item)

        if merged:
            metadata[key] = merged

    def _merge_memory_assets(
        self,
        metadata: dict[str, Any],
        *,
        scope: Any,
        default_agent_id: str | None = None,
        default_swarm_id: str | None = None,
    ) -> None:
        build_assets = getattr(scope, "build_memory_asset_payloads", None)
        if not callable(build_assets):
            return

        raw_payloads = build_assets(
            default_agent_id=default_agent_id,
            default_swarm_id=default_swarm_id,
        )
        if not isinstance(raw_payloads, tuple) or len(raw_payloads) != 2:
            return

        skill_payloads_raw, resource_payloads_raw = raw_payloads
        skill_payloads = skill_payloads_raw if isinstance(skill_payloads_raw, list) else []
        resource_payloads = resource_payloads_raw if isinstance(resource_payloads_raw, list) else []
        if skill_payloads:
            self._merge_payload_list(
                metadata,
                "memory_defined_skills",
                skill_payloads,
                identity_keys=("skill_id", "id", "name"),
            )
        if resource_payloads:
            self._merge_payload_list(
                metadata,
                "memory_bound_resources",
                resource_payloads,
                identity_keys=("resource_id", "id", "title", "name"),
            )
