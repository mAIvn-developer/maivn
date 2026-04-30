from __future__ import annotations

from typing import Any, Literal

from maivn_shared import MemoryAssetsConfig, MemoryConfig, SwarmConfig

from maivn._internal.utils.reporting.context import current_sdk_delivery_mode


class DynamicToolFactoryNestedInvocationMixin:
    @staticmethod
    def _normalize_included_nested_synthesis(value: Any) -> bool | Literal["auto"]:
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

    def _build_nested_invocation_swarm_config(
        self,
        *,
        agent: Any,
        agent_id: str,
        use_as_final_output: bool,
        resolved_nested_synthesis: bool | Literal["auto"],
    ) -> SwarmConfig:
        return SwarmConfig(
            agent_invocation=True,
            use_as_final_output=use_as_final_output,
            invoked_agent_id=getattr(agent, "id", agent_id),
            invoked_agent_name=getattr(agent, "name", None),
            included_nested_synthesis=resolved_nested_synthesis,
            sdk_delivery_mode=current_sdk_delivery_mode.get(),
        )

    def _build_nested_invocation_memory_assets_config(
        self,
        *,
        agent: Any,
        swarm_scope: Any,
        memory_recall_turn_active: bool = False,
    ) -> MemoryAssetsConfig | None:
        defined_skills: list[dict[str, Any]] = []
        bound_resources: list[dict[str, Any]] = []
        if memory_recall_turn_active:
            recall_turn_active: bool | None = True
        else:
            recall_turn_active = None
        self._merge_memory_assets(
            defined_skills=defined_skills,
            bound_resources=bound_resources,
            scope=agent,
            default_agent_id=getattr(agent, "id", None),
            default_swarm_id=getattr(swarm_scope, "id", None),
        )
        self._merge_memory_assets(
            defined_skills=defined_skills,
            bound_resources=bound_resources,
            scope=swarm_scope,
            default_swarm_id=getattr(swarm_scope, "id", None),
        )

        config = MemoryAssetsConfig.model_validate(
            {
                "defined_skills": defined_skills,
                "bound_resources": bound_resources,
                "recall_turn_active": recall_turn_active,
            }
        )
        return config if config.is_configured() else None

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
        existing: list[dict[str, Any]],
        incoming: list[dict[str, Any]],
        *,
        identity_keys: tuple[str, ...],
    ) -> list[dict[str, Any]]:
        merged: list[dict[str, Any]] = [item for item in existing if isinstance(item, dict)]
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

        return merged

    def _merge_memory_assets(
        self,
        *,
        defined_skills: list[dict[str, Any]],
        bound_resources: list[dict[str, Any]],
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
            defined_skills[:] = self._merge_payload_list(
                defined_skills,
                skill_payloads,
                identity_keys=("skill_id", "id", "name"),
            )
        if resource_payloads:
            bound_resources[:] = self._merge_payload_list(
                bound_resources,
                resource_payloads,
                identity_keys=("resource_id", "id", "title", "name"),
            )
