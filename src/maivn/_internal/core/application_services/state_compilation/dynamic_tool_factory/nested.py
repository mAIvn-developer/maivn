"""Nested invocation helpers for dynamic dependency tools."""

# pyright: strict
from __future__ import annotations

from collections.abc import Callable
from typing import Literal, TypeAlias, cast

from maivn_shared import MemoryAssetsConfig, MemoryConfig, SwarmConfig

from maivn._internal.utils.reporting.context import current_sdk_delivery_mode

# MARK: - Types

MemoryAssetPayload: TypeAlias = dict[str, object]


# MARK: - Mixin


class DynamicToolFactoryNestedInvocationMixin:
    @staticmethod
    def _normalize_included_nested_synthesis(value: object) -> bool | Literal["auto"]:
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
        agent: object,
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
        agent: object,
        swarm_scope: object,
        memory_recall_turn_active: bool = False,
    ) -> MemoryAssetsConfig | None:
        defined_skills: list[MemoryAssetPayload] = []
        bound_resources: list[MemoryAssetPayload] = []
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
    def _coerce_memory_config(value: object) -> MemoryConfig | None:
        if isinstance(value, MemoryConfig):
            return value
        if isinstance(value, dict):
            return MemoryConfig.model_validate(value)
        return None

    def _resolve_scope_memory_config(self, scope: object) -> MemoryConfig | None:
        resolver = _optional_attr(scope, "resolve_memory_config")
        if callable(resolver):
            resolved = cast(Callable[[object | None], object], resolver)(None)
            if isinstance(resolved, MemoryConfig) and resolved.is_configured():
                return resolved
        return self._coerce_memory_config(_optional_attr(scope, "memory_config"))

    def _build_nested_invocation_memory_config(
        self,
        *,
        agent: object,
        swarm_scope: object,
    ) -> MemoryConfig | None:
        return MemoryConfig.merge(
            self._resolve_scope_memory_config(agent),
            self._resolve_scope_memory_config(swarm_scope),
        )

    @staticmethod
    def _merge_payload_list(
        existing: list[MemoryAssetPayload],
        incoming: list[MemoryAssetPayload],
        *,
        identity_keys: tuple[str, ...],
    ) -> list[MemoryAssetPayload]:
        merged = list(existing)
        seen: set[str] = set()

        def _identity(item: MemoryAssetPayload) -> str:
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
        defined_skills: list[MemoryAssetPayload],
        bound_resources: list[MemoryAssetPayload],
        scope: object,
        default_agent_id: str | None = None,
        default_swarm_id: str | None = None,
    ) -> None:
        payloads = self._build_memory_asset_payloads(
            scope,
            default_agent_id=default_agent_id,
            default_swarm_id=default_swarm_id,
        )
        if payloads is None:
            return

        skill_payloads, resource_payloads = payloads
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

    @staticmethod
    def _build_memory_asset_payloads(
        scope: object,
        *,
        default_agent_id: str | None = None,
        default_swarm_id: str | None = None,
    ) -> tuple[list[MemoryAssetPayload], list[MemoryAssetPayload]] | None:
        build_assets = _optional_attr(scope, "build_memory_asset_payloads")
        if not callable(build_assets):
            return None

        raw_payloads = build_assets(
            default_agent_id=default_agent_id,
            default_swarm_id=default_swarm_id,
        )
        if not isinstance(raw_payloads, tuple):
            return None
        raw_payload_tuple = cast(tuple[object, ...], raw_payloads)
        if len(raw_payload_tuple) != 2:
            return None

        skill_payloads_raw, resource_payloads_raw = raw_payload_tuple
        return (
            _coerce_payload_list(skill_payloads_raw),
            _coerce_payload_list(resource_payloads_raw),
        )


# MARK: - Helpers


def _coerce_payload_list(value: object) -> list[MemoryAssetPayload]:
    if not isinstance(value, list):
        return []
    return [
        cast(MemoryAssetPayload, item)
        for item in cast(list[object], value)
        if isinstance(item, dict)
    ]


def _optional_attr(value: object, attr: str) -> object | None:
    return cast(object | None, getattr(value, attr, None))
