from __future__ import annotations

import base64
from pathlib import Path
from typing import Any, Protocol, cast

from maivn_shared import MemoryConfig
from maivn_shared.domain.entities.memory_config import is_reserved_memory_metadata_key

_SUPPORTED_SKILL_SHARING_SCOPES = frozenset({"agent", "swarm", "project", "org"})
_DEFAULT_SKILL_SHARING_SCOPE = "project"
_MAX_RESOURCE_INLINE_BYTES = 50 * 1024 * 1024


# MARK: Protocol


class _BaseScopeMemoryProtocol(Protocol):
    memory_config: MemoryConfig
    skills: list[dict[str, Any]]
    resources: list[dict[str, Any]]


# MARK: Mixin


class BaseScopeMemoryMixin:
    # MARK: - Memory Configuration

    @staticmethod
    def coerce_memory_config(value: Any) -> MemoryConfig | None:
        if value is None:
            return None
        if isinstance(value, MemoryConfig):
            return value
        if isinstance(value, dict):
            return MemoryConfig.model_validate(value)
        raise TypeError("memory_config must be a MemoryConfig, dictionary, or None")

    def resolve_memory_config(self, override: Any = None) -> MemoryConfig | None:
        scope = cast(_BaseScopeMemoryProtocol, self)
        return MemoryConfig.merge(scope.memory_config, self.coerce_memory_config(override))

    @staticmethod
    def reject_reserved_memory_metadata_keys(metadata: Any) -> None:
        if not isinstance(metadata, dict):
            return
        reserved_keys = sorted(
            key for key in metadata if isinstance(key, str) and is_reserved_memory_metadata_key(key)
        )
        if reserved_keys:
            joined = ", ".join(reserved_keys)
            raise ValueError(
                "Reserved memory metadata keys are not allowed in metadata; "
                f"use memory_config instead ({joined})"
            )

    # MARK: - Normalization Helpers

    @staticmethod
    def _coerce_string(value: Any) -> str | None:
        if not isinstance(value, str):
            return None
        normalized = value.strip()
        if not normalized:
            return None
        return normalized

    @classmethod
    def _encode_resource_content_base64(cls, raw_resource: dict[str, Any]) -> str | None:
        inline_content_present = any(
            key in raw_resource for key in ("content_bytes", "text_content", "file")
        )
        content_bytes = cls._extract_resource_content_bytes(raw_resource)
        if content_bytes is None:
            if inline_content_present:
                raise ValueError(
                    "resources inline content is invalid; provide non-empty content_bytes, "
                    "text_content, or file"
                )
            return None
        if not content_bytes:
            raise ValueError("resources inline content is empty")
        if len(content_bytes) > _MAX_RESOURCE_INLINE_BYTES:
            raise ValueError("resources inline content exceeds maximum supported size")
        return base64.b64encode(content_bytes).decode("ascii")

    @staticmethod
    def _extract_resource_content_bytes(raw_resource: dict[str, Any]) -> bytes | None:
        content_bytes = raw_resource.get("content_bytes")
        if isinstance(content_bytes, bytes):
            return content_bytes
        if isinstance(content_bytes, bytearray):
            return bytes(content_bytes)

        text_content = raw_resource.get("text_content")
        if isinstance(text_content, str):
            return text_content.encode("utf-8")

        return BaseScopeMemoryMixin._read_resource_file_bytes(raw_resource.get("file"))

    @staticmethod
    def _read_resource_file_bytes(file_value: Any) -> bytes | None:
        if file_value is None:
            return None
        if isinstance(file_value, bytes):
            return file_value
        if isinstance(file_value, bytearray):
            return bytes(file_value)
        if isinstance(file_value, str | Path):
            file_path = Path(file_value)
            if file_path.exists() and file_path.is_file():
                return file_path.read_bytes()
            return None
        if hasattr(file_value, "read"):
            raw = file_value.read()
            if isinstance(raw, str):
                return raw.encode("utf-8")
            if isinstance(raw, bytes):
                return raw
        return None

    # MARK: - Skill Payloads

    def _resolve_default_skill_sharing_scope(self) -> str:
        scope = cast(_BaseScopeMemoryProtocol, self)
        skill_extraction = getattr(scope.memory_config, "skill_extraction", None)
        raw_scope = getattr(skill_extraction, "sharing_scope", None)
        if raw_scope is None:
            return _DEFAULT_SKILL_SHARING_SCOPE

        normalized = str(raw_scope).strip().lower()
        if normalized in _SUPPORTED_SKILL_SHARING_SCOPES:
            return normalized
        return _DEFAULT_SKILL_SHARING_SCOPE

    def _normalize_skill_payload(
        self,
        raw_skill: dict[str, Any],
        *,
        default_sharing_scope: str,
        default_agent_id: str | None,
        default_swarm_id: str | None,
    ) -> dict[str, Any] | None:
        name = self._coerce_string(raw_skill.get("name") or raw_skill.get("title"))
        if name is None:
            return None

        description = self._coerce_string(raw_skill.get("description") or raw_skill.get("content"))
        if description is None:
            description = f"User-defined skill: {name}"

        sharing_scope = self._coerce_string(raw_skill.get("sharing_scope"))
        normalized_scope = (
            sharing_scope.lower() if sharing_scope is not None else default_sharing_scope
        )
        if normalized_scope not in _SUPPORTED_SKILL_SHARING_SCOPES:
            normalized_scope = default_sharing_scope

        steps = raw_skill.get("steps")
        normalized_steps = (
            [step for step in steps if isinstance(step, dict)] if isinstance(steps, list) else []
        )
        preconditions = raw_skill.get("preconditions")
        if not isinstance(preconditions, dict):
            preconditions = {}
        postconditions = raw_skill.get("postconditions")
        if not isinstance(postconditions, dict):
            postconditions = {}
        metadata = raw_skill.get("metadata")
        if not isinstance(metadata, dict):
            metadata = {}

        skill_id = self._coerce_string(raw_skill.get("skill_id") or raw_skill.get("id"))
        agent_id = self._coerce_string(raw_skill.get("agent_id")) or default_agent_id
        swarm_id = self._coerce_string(raw_skill.get("swarm_id")) or default_swarm_id

        payload: dict[str, Any] = {
            "name": name,
            "title": name,
            "description": description,
            "content": description,
            "steps": normalized_steps,
            "preconditions": preconditions,
            "postconditions": postconditions,
            "sharing_scope": normalized_scope,
            "origin": "user_defined",
            "confidence": 1.0,
            "metadata": metadata,
        }
        if skill_id is not None:
            payload["skill_id"] = skill_id
            payload["id"] = skill_id
        if agent_id is not None:
            payload["agent_id"] = agent_id
        if swarm_id is not None:
            payload["swarm_id"] = swarm_id
        return payload

    # MARK: - Resource Payloads

    def _normalize_resource_payload(
        self,
        raw_resource: dict[str, Any],
        *,
        default_binding_type: str,
        default_agent_id: str | None,
        default_swarm_id: str | None,
    ) -> dict[str, Any] | None:
        title = self._coerce_string(raw_resource.get("title") or raw_resource.get("name"))
        resource_id = self._coerce_string(raw_resource.get("resource_id") or raw_resource.get("id"))
        source_url = self._coerce_string(raw_resource.get("source_url") or raw_resource.get("url"))
        content_base64 = self._coerce_string(raw_resource.get("content_base64"))
        if content_base64 is None:
            content_base64 = self._encode_resource_content_base64(raw_resource)
        if title is None:
            title = self._coerce_string(raw_resource.get("filename"))
        if title is None:
            file_value = raw_resource.get("file")
            if isinstance(file_value, str | Path):
                title = self._coerce_string(Path(file_value).name)
            else:
                candidate_name = getattr(file_value, "name", None)
                if isinstance(candidate_name, str):
                    title = self._coerce_string(Path(candidate_name).name)
        if title is None:
            if resource_id is not None:
                title = f"Resource {resource_id}"
            elif source_url is not None:
                title = source_url
            else:
                title = "Bound resource"
        description = self._coerce_string(
            raw_resource.get("description") or raw_resource.get("content")
        )
        if description is None:
            text_content = raw_resource.get("text_content")
            if isinstance(text_content, str) and text_content.strip():
                snippet = text_content.strip()
                if len(snippet) > 500:
                    snippet = snippet[:500].rstrip()
                description = snippet
        tags = raw_resource.get("tags")
        normalized_tags: list[str] = []
        if isinstance(tags, list):
            normalized_tags = [tag.strip() for tag in tags if isinstance(tag, str) and tag.strip()]
        binding_type = (
            self._coerce_string(raw_resource.get("binding_type")) or default_binding_type or "sdk"
        )
        normalized_binding_type = binding_type.lower()
        if normalized_binding_type not in {"message", "agent", "swarm", "portal", "unbound", "sdk"}:
            normalized_binding_type = default_binding_type or "sdk"

        sharing_scope = self._coerce_string(raw_resource.get("sharing_scope"))
        if sharing_scope is not None:
            sharing_scope = sharing_scope.lower()
        if sharing_scope not in {"agent", "swarm", "project", "org"}:
            if normalized_binding_type in {"agent", "swarm"}:
                sharing_scope = normalized_binding_type
            else:
                sharing_scope = "project"

        agent_id = self._coerce_string(raw_resource.get("agent_id")) or (
            default_agent_id if normalized_binding_type == "agent" else None
        )
        swarm_id = self._coerce_string(raw_resource.get("swarm_id")) or (
            default_swarm_id if normalized_binding_type == "swarm" else None
        )

        source_type = self._coerce_string(raw_resource.get("source_type"))
        if source_type is None:
            if normalized_binding_type == "agent":
                source_type = "agent_binding"
            elif normalized_binding_type == "swarm":
                source_type = "swarm_binding"
            else:
                source_type = "attachment"
        mime_type = self._coerce_string(raw_resource.get("mime_type"))

        payload: dict[str, Any] = {
            "title": title,
            "name": title,
            "binding_type": normalized_binding_type,
            "sharing_scope": sharing_scope,
            "source_type": source_type,
            "tags": normalized_tags,
        }
        if resource_id is not None:
            payload["resource_id"] = resource_id
            payload["id"] = resource_id
        if description is not None:
            payload["description"] = description
            payload["content"] = description
        if source_url is not None:
            payload["source_url"] = source_url
            payload["url"] = source_url
        if content_base64 is not None:
            payload["content_base64"] = content_base64
        if mime_type is not None:
            payload["mime_type"] = mime_type
        if agent_id is not None:
            payload["agent_id"] = agent_id
        if swarm_id is not None:
            payload["swarm_id"] = swarm_id
        return payload

    # MARK: - Metadata Assembly

    def build_memory_asset_payloads(
        self,
        *,
        default_agent_id: str | None = None,
        default_swarm_id: str | None = None,
    ) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
        scope = cast(_BaseScopeMemoryProtocol, self)
        default_sharing_scope = self._resolve_default_skill_sharing_scope()
        normalized_skills: list[dict[str, Any]] = []
        for raw_skill in scope.skills:
            normalized = self._normalize_skill_payload(
                raw_skill,
                default_sharing_scope=default_sharing_scope,
                default_agent_id=default_agent_id,
                default_swarm_id=default_swarm_id,
            )
            if normalized is not None:
                normalized_skills.append(normalized)

        normalized_resources: list[dict[str, Any]] = []
        if default_agent_id is not None:
            default_resource_binding_type = "agent"
        elif default_swarm_id is not None:
            default_resource_binding_type = "swarm"
        else:
            default_resource_binding_type = "sdk"

        for raw_resource in scope.resources:
            normalized = self._normalize_resource_payload(
                raw_resource,
                default_binding_type=default_resource_binding_type,
                default_agent_id=default_agent_id,
                default_swarm_id=default_swarm_id,
            )
            if normalized is not None:
                normalized_resources.append(normalized)

        return normalized_skills, normalized_resources

    # MARK: - Payload Merging

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
            for key in identity_keys:
                raw_value = item.get(key)
                if isinstance(raw_value, str) and raw_value.strip():
                    return f"{key}:{raw_value.strip().lower()}"
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

    def apply_memory_assets_to_metadata(
        self,
        metadata: dict[str, Any],
        *,
        overwrite: bool = False,
        default_agent_id: str | None = None,
        default_swarm_id: str | None = None,
    ) -> None:
        if not isinstance(metadata, dict):
            return

        skills, resources = self.build_memory_asset_payloads(
            default_agent_id=default_agent_id,
            default_swarm_id=default_swarm_id,
        )
        if skills:
            if overwrite or not isinstance(metadata.get("memory_defined_skills"), list):
                metadata["memory_defined_skills"] = skills
            else:
                metadata["memory_defined_skills"] = self._merge_payload_list(
                    metadata["memory_defined_skills"],
                    skills,
                    identity_keys=("skill_id", "id", "name"),
                )
        if resources:
            if overwrite or not isinstance(metadata.get("memory_bound_resources"), list):
                metadata["memory_bound_resources"] = resources
            else:
                metadata["memory_bound_resources"] = self._merge_payload_list(
                    metadata["memory_bound_resources"],
                    resources,
                    identity_keys=("resource_id", "id", "title", "name"),
                )
