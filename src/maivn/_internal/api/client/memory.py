from __future__ import annotations

from typing import Any, Protocol, TypeVar

from pydantic import BaseModel

from maivn._internal.api.resource_models import (
    MemoryInsight,
    MemoryResource,
    MemoryResourceDetail,
    MemorySkill,
    MemoryUnboundResourceCandidate,
    OrganizationMemoryPolicy,
    OrganizationMemoryPurgeResult,
    ProjectMemoryResources,
)

ModelT = TypeVar("ModelT", bound=BaseModel)


class _ClientMemoryProtocol(Protocol):
    def _get_json(self, path: str) -> Any: ...

    def _post_json(self, path: str, payload: dict[str, Any] | None = None) -> Any: ...

    def _patch_json(self, path: str, payload: dict[str, Any]) -> Any: ...

    def _delete_json(self, path: str) -> Any: ...

    @staticmethod
    def _with_query(path: str, params: dict[str, Any]) -> str: ...

    @staticmethod
    def _extract_items(payload: Any) -> list[dict[str, Any]]: ...

    @staticmethod
    def _validate_model(model_type: type[ModelT], payload: Any) -> ModelT: ...


class ClientMemoryMixin:
    # MARK: - Organization Memory

    def get_organization_memory_policy(
        self: _ClientMemoryProtocol,
        org_id: str,
    ) -> OrganizationMemoryPolicy:
        payload = self._get_json(f"/organizations/{org_id}/memory-policy")
        return self._validate_model(OrganizationMemoryPolicy, payload)

    def update_organization_memory_policy(
        self: _ClientMemoryProtocol,
        org_id: str,
        policy: OrganizationMemoryPolicy | dict[str, Any],
    ) -> OrganizationMemoryPolicy:
        payload = (
            policy.model_dump(mode="json")
            if isinstance(policy, OrganizationMemoryPolicy)
            else dict(policy)
        )
        result = self._patch_json(f"/organizations/{org_id}/memory-policy", payload)
        return self._validate_model(OrganizationMemoryPolicy, result)

    def purge_organization_memory(
        self: _ClientMemoryProtocol,
        org_id: str,
        *,
        confirm_token: str = "PURGE_MEMORY",
        project_id: str | None = None,
        session_id: str | None = None,
    ) -> OrganizationMemoryPurgeResult:
        payload: dict[str, Any] = {"confirm_token": confirm_token}
        if project_id is not None:
            payload["project_id"] = project_id
        if session_id is not None:
            payload["session_id"] = session_id
        result = self._post_json(f"/organizations/{org_id}/memory-policy/purge", payload)
        return self._validate_model(OrganizationMemoryPurgeResult, result)

    # MARK: - Project Memory Skills

    def list_memory_skills(
        self: _ClientMemoryProtocol,
        project_id: str,
        *,
        search: str | None = None,
        sharing_scope: str | None = None,
        origin: str | None = None,
        status: str | None = None,
        limit: int | None = None,
        offset: int | None = None,
    ) -> list[MemorySkill]:
        path = self._with_query(
            f"/projects/{project_id}/memory/skills",
            {
                "search": search,
                "sharing_scope": sharing_scope,
                "origin": origin,
                "status": status,
                "limit": limit,
                "offset": offset,
            },
        )
        payload = self._get_json(path)
        return [self._validate_model(MemorySkill, item) for item in self._extract_items(payload)]

    def get_memory_skill(
        self: _ClientMemoryProtocol,
        project_id: str,
        skill_id: str,
    ) -> MemorySkill:
        payload = self._get_json(f"/projects/{project_id}/memory/skills/{skill_id}")
        return self._validate_model(MemorySkill, payload)

    def create_memory_skill(
        self: _ClientMemoryProtocol,
        project_id: str,
        payload: dict[str, Any],
    ) -> MemorySkill:
        result = self._post_json(f"/projects/{project_id}/memory/skills", dict(payload))
        return self._validate_model(MemorySkill, result)

    def update_memory_skill(
        self: _ClientMemoryProtocol,
        project_id: str,
        skill_id: str,
        payload: dict[str, Any],
    ) -> MemorySkill:
        result = self._patch_json(f"/projects/{project_id}/memory/skills/{skill_id}", dict(payload))
        return self._validate_model(MemorySkill, result)

    def delete_memory_skill(self: _ClientMemoryProtocol, project_id: str, skill_id: str) -> None:
        self._delete_json(f"/projects/{project_id}/memory/skills/{skill_id}")

    # MARK: - Project Memory Insights

    def list_memory_insights(
        self: _ClientMemoryProtocol,
        project_id: str,
        *,
        search: str | None = None,
        sharing_scope: str | None = None,
        insight_type: str | None = None,
        origin: str | None = None,
        limit: int | None = None,
        offset: int | None = None,
    ) -> list[MemoryInsight]:
        path = self._with_query(
            f"/projects/{project_id}/memory/insights",
            {
                "search": search,
                "sharing_scope": sharing_scope,
                "insight_type": insight_type,
                "origin": origin,
                "limit": limit,
                "offset": offset,
            },
        )
        payload = self._get_json(path)
        return [self._validate_model(MemoryInsight, item) for item in self._extract_items(payload)]

    def get_memory_insight(
        self: _ClientMemoryProtocol,
        project_id: str,
        insight_id: str,
    ) -> MemoryInsight:
        payload = self._get_json(f"/projects/{project_id}/memory/insights/{insight_id}")
        return self._validate_model(MemoryInsight, payload)

    def create_memory_insight(
        self: _ClientMemoryProtocol,
        project_id: str,
        payload: dict[str, Any],
    ) -> MemoryInsight:
        result = self._post_json(f"/projects/{project_id}/memory/insights", dict(payload))
        return self._validate_model(MemoryInsight, result)

    def update_memory_insight(
        self: _ClientMemoryProtocol,
        project_id: str,
        insight_id: str,
        payload: dict[str, Any],
    ) -> MemoryInsight:
        result = self._patch_json(
            f"/projects/{project_id}/memory/insights/{insight_id}",
            dict(payload),
        )
        return self._validate_model(MemoryInsight, result)

    def promote_memory_insight(
        self: _ClientMemoryProtocol,
        project_id: str,
        insight_id: str,
        *,
        target_scope: str,
    ) -> MemoryInsight:
        result = self._post_json(
            f"/projects/{project_id}/memory/insights/{insight_id}/promote",
            {"target_scope": target_scope},
        )
        return self._validate_model(MemoryInsight, result)

    def delete_memory_insight(
        self: _ClientMemoryProtocol,
        project_id: str,
        insight_id: str,
    ) -> None:
        self._delete_json(f"/projects/{project_id}/memory/insights/{insight_id}")

    # MARK: - Project Memory Resources

    def list_memory_resources(
        self: _ClientMemoryProtocol,
        project_id: str,
        *,
        search: str | None = None,
        binding_type: str | None = None,
        status: str | None = None,
        tags: list[str] | None = None,
        limit: int | None = None,
        offset: int | None = None,
    ) -> list[MemoryResource]:
        path = self._with_query(
            f"/projects/{project_id}/memory/resources",
            {
                "search": search,
                "binding_type": binding_type,
                "status": status,
                "tags": [tag.strip() for tag in tags or [] if isinstance(tag, str) and tag.strip()],
                "limit": limit,
                "offset": offset,
            },
        )
        payload = self._get_json(path)
        return [self._validate_model(MemoryResource, item) for item in self._extract_items(payload)]

    def get_memory_resource(
        self: _ClientMemoryProtocol,
        project_id: str,
        resource_id: str,
    ) -> MemoryResourceDetail:
        payload = self._get_json(f"/projects/{project_id}/memory/resources/{resource_id}")
        return self._validate_model(MemoryResourceDetail, payload)

    def create_memory_resource(
        self: _ClientMemoryProtocol,
        project_id: str,
        payload: dict[str, Any],
    ) -> MemoryResourceDetail:
        result = self._post_json(f"/projects/{project_id}/memory/resources", dict(payload))
        return self._validate_model(MemoryResourceDetail, result)

    def update_memory_resource(
        self: _ClientMemoryProtocol,
        project_id: str,
        resource_id: str,
        payload: dict[str, Any],
    ) -> MemoryResourceDetail:
        result = self._patch_json(
            f"/projects/{project_id}/memory/resources/{resource_id}",
            dict(payload),
        )
        return self._validate_model(MemoryResourceDetail, result)

    def replace_memory_resource(
        self: _ClientMemoryProtocol,
        project_id: str,
        resource_id: str,
        payload: dict[str, Any],
    ) -> MemoryResourceDetail:
        result = self._post_json(
            f"/projects/{project_id}/memory/resources/{resource_id}/replace",
            dict(payload),
        )
        return self._validate_model(MemoryResourceDetail, result)

    def delete_memory_resource(
        self: _ClientMemoryProtocol,
        project_id: str,
        resource_id: str,
    ) -> None:
        self._delete_json(f"/projects/{project_id}/memory/resources/{resource_id}")

    def restore_memory_resource(
        self: _ClientMemoryProtocol,
        project_id: str,
        resource_id: str,
    ) -> MemoryResourceDetail:
        result = self._post_json(f"/projects/{project_id}/memory/resources/{resource_id}/restore")
        return self._validate_model(MemoryResourceDetail, result)

    def rebind_memory_resource_to_portal(
        self: _ClientMemoryProtocol,
        project_id: str,
        resource_id: str,
    ) -> MemoryResourceDetail:
        result = self._post_json(f"/projects/{project_id}/memory/resources/{resource_id}/rebind")
        return self._validate_model(MemoryResourceDetail, result)

    def bind_memory_resource(
        self: _ClientMemoryProtocol,
        project_id: str,
        resource_id: str,
        *,
        binding_type: str,
        target_id: str,
    ) -> MemoryResourceDetail:
        result = self._post_json(
            f"/projects/{project_id}/memory/resources/{resource_id}/bind",
            {"binding_type": binding_type, "target_id": target_id},
        )
        return self._validate_model(MemoryResourceDetail, result)

    def list_unbound_memory_resource_candidates(
        self: _ClientMemoryProtocol,
        project_id: str,
        *,
        min_age_days: int | None = None,
        limit: int | None = None,
        offset: int | None = None,
    ) -> list[MemoryUnboundResourceCandidate]:
        path = self._with_query(
            f"/projects/{project_id}/memory/resources/unbound/candidates",
            {
                "min_age_days": min_age_days,
                "limit": limit,
                "offset": offset,
            },
        )
        payload = self._get_json(path)
        return [
            self._validate_model(MemoryUnboundResourceCandidate, item)
            for item in self._extract_items(payload)
        ]

    def list_project_memory_resources(
        self: Any,
        project_id: str,
    ) -> ProjectMemoryResources:
        return ProjectMemoryResources(
            skills=self.list_memory_skills(project_id),
            insights=self.list_memory_insights(project_id),
            resources=self.list_memory_resources(project_id),
        )
