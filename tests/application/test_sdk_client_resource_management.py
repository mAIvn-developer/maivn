from __future__ import annotations

from collections import deque
from typing import Any
from urllib.parse import parse_qs, urlparse

import httpx
import pytest

from maivn import (
    Client,
    MemoryResourceDetail,
    OrganizationMemoryPolicy,
    OrganizationMemoryPurgeResult,
    ProjectMemoryResources,
    RedactedMessage,
    RedactionPreviewRequest,
)


class _QueueHttpClient:
    def __init__(self, responses: list[dict[str, Any] | list[dict[str, Any]]]) -> None:
        self._responses = deque(responses)
        self.calls: list[dict[str, Any]] = []

    def request(
        self,
        method: str,
        url: str,
        *,
        headers: dict[str, str],
        json: dict[str, Any] | None,
    ) -> httpx.Response:
        self.calls.append({"method": method, "url": url, "headers": headers, "json": json})
        payload = self._responses.popleft()
        request = httpx.Request(method, url)
        return httpx.Response(200, json=payload, request=request)


def _skill_payload() -> dict[str, Any]:
    return {
        "id": "skill-1",
        "project_id": "project-1",
        "organization_id": "org-1",
        "agent_id": None,
        "swarm_id": None,
        "sharing_scope": "project",
        "name": "deploy_with_health_checks",
        "description": "Deploy with validation.",
        "steps": [{"index": 1, "action": "deploy"}],
        "preconditions": {},
        "postconditions": {},
        "version": 1,
        "confidence": 1.0,
        "application_count": 0,
        "success_rate": 1.0,
        "origin": "user_defined",
        "status": "active",
        "created_at": "2026-03-06T00:00:00Z",
        "updated_at": "2026-03-06T00:00:00Z",
    }


def _insight_payload() -> dict[str, Any]:
    return {
        "id": "insight-1",
        "project_id": "project-1",
        "organization_id": "org-1",
        "agent_id": None,
        "swarm_id": None,
        "sharing_scope": "project",
        "insight_type": "warning",
        "content": "Rollback if canary fails.",
        "relevance_score": 0.9,
        "decay_model": "exponential",
        "half_life_days": 30,
        "ttl_days": None,
        "origin": "user_promoted",
        "promoted_from_id": None,
        "expires_at": None,
        "created_at": "2026-03-06T00:00:00Z",
        "updated_at": "2026-03-06T00:00:00Z",
    }


def _resource_payload() -> dict[str, Any]:
    return {
        "id": "doc-1",
        "project_id": "project-1",
        "organization_id": "org-1",
        "resource_thread_id": "thread-1",
        "name": "Deploy Runbook",
        "description": "Primary runbook.",
        "tags": ["ops", "deploy"],
        "format": "txt",
        "size_bytes": 128,
        "sharing_scope": "project",
        "binding_type": "portal",
        "bound_agent_id": None,
        "bound_swarm_id": None,
        "registration_status": "registered",
        "page_count": None,
        "extracted_page_count": 0,
        "chunk_count": 0,
        "source_type": "portal",
        "source_url": None,
        "query_count": 0,
        "last_queried_at": None,
        "cleanup_candidate": False,
        "cleanup_candidate_reason": None,
        "created_at": "2026-03-06T00:00:00Z",
        "updated_at": "2026-03-06T00:00:00Z",
    }


def _resource_detail_payload() -> dict[str, Any]:
    payload = _resource_payload()
    payload.update(
        {
            "content_hash": "abc123",
            "storage_bucket": "memory-resources",
            "storage_path": "project-1/abc123/deploy-runbook.txt",
            "metadata": {},
            "superseded_by": None,
            "replaces_resource_id": None,
            "extractor_version": "v1",
            "version_chain": [],
            "extraction_stats": {},
        }
    )
    return payload


def _policy_payload() -> dict[str, Any]:
    return {
        "enabled": True,
        "persistence_ceiling": "vector_plus_graph",
        "vector_retention_days": 365,
        "graph_retention_days": 90,
    }


def test_client_lists_memory_resources_with_filters_and_returns_models(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    http_client = _QueueHttpClient([{"items": [_resource_payload()]}])
    client = Client(api_key="test-key")
    monkeypatch.setattr(client, "_base_url", "http://127.0.0.1:8000")
    monkeypatch.setattr(client, "_get_http_client", lambda: http_client)

    resources = client.list_memory_resources(
        "project-1",
        search="deploy",
        binding_type="portal",
        status="registered",
        tags=["ops", " deploy "],
        limit=10,
        offset=5,
    )

    assert resources[0].name == "Deploy Runbook"
    parsed = urlparse(http_client.calls[0]["url"])
    query = parse_qs(parsed.query)
    assert query["search"] == ["deploy"]
    assert query["binding_type"] == ["portal"]
    assert query["status"] == ["registered"]
    assert query["tags"] == ["ops", "deploy"]
    assert query["limit"] == ["10"]
    assert query["offset"] == ["5"]


def test_client_lists_project_memory_resources(monkeypatch: pytest.MonkeyPatch) -> None:
    http_client = _QueueHttpClient(
        [
            {"items": [_skill_payload()]},
            {"items": [_insight_payload()]},
            {"items": [_resource_payload()]},
        ]
    )
    client = Client(api_key="test-key")
    monkeypatch.setattr(client, "_base_url", "http://127.0.0.1:8000")
    monkeypatch.setattr(client, "_get_http_client", lambda: http_client)

    resources = client.list_project_memory_resources("project-1")

    assert isinstance(resources, ProjectMemoryResources)
    assert resources.skills[0].id == "skill-1"
    assert resources.insights[0].id == "insight-1"
    assert resources.resources[0].id == "doc-1"
    assert resources.resources[0].id == "doc-1"
    assert [call["method"] for call in http_client.calls] == ["GET", "GET", "GET"]


def test_client_manages_organization_memory_policy_and_purge(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    http_client = _QueueHttpClient(
        [
            _policy_payload(),
            _policy_payload(),
            {
                "success": True,
                "project_ids": ["project-1"],
                "session_id": None,
                "tables": ["memory.memory_vectors"],
            },
        ]
    )
    client = Client(api_key="test-key")
    monkeypatch.setattr(client, "_base_url", "http://127.0.0.1:8000")
    monkeypatch.setattr(client, "_get_http_client", lambda: http_client)

    current = client.get_organization_memory_policy("org-1")
    updated = client.update_organization_memory_policy("org-1", current)
    purged = client.purge_organization_memory("org-1", project_id="project-1")

    assert isinstance(current, OrganizationMemoryPolicy)
    assert isinstance(updated, OrganizationMemoryPolicy)
    assert isinstance(purged, OrganizationMemoryPurgeResult)
    assert http_client.calls[0]["method"] == "GET"
    assert http_client.calls[1]["method"] == "PATCH"
    assert http_client.calls[1]["json"]["persistence_ceiling"] == "vector_plus_graph"
    assert http_client.calls[2]["method"] == "POST"
    assert http_client.calls[2]["json"] == {
        "confirm_token": "PURGE_MEMORY",
        "project_id": "project-1",
    }


def test_client_creates_memory_resource_and_returns_detail(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    http_client = _QueueHttpClient([_resource_detail_payload()])
    client = Client(api_key="test-key")
    monkeypatch.setattr(client, "_base_url", "http://127.0.0.1:8000")
    monkeypatch.setattr(client, "_get_http_client", lambda: http_client)

    detail = client.create_memory_resource(
        "project-1",
        {
            "name": "Deploy Runbook",
            "mime_type": "text/plain",
            "content_base64": "U29tZSBiYXNlNjQgY29udGVudA==",
            "sharing_scope": "project",
        },
    )

    assert isinstance(detail, MemoryResourceDetail)
    assert detail.storage_bucket == "memory-resources"
    assert http_client.calls[0]["method"] == "POST"
    assert http_client.calls[0]["url"].endswith("/projects/project-1/memory/resources")


def test_client_previews_redaction(monkeypatch: pytest.MonkeyPatch) -> None:
    http_client = _QueueHttpClient(
        [
            {
                "message": {
                    "type": "redacted",
                    "role": "redacted",
                    "content": "Email {_{pii_email_1}_}",
                },
                "inserted_keys": ["pii_email_1"],
                "added_private_data": {"pii_email_1": "alice@example.com"},
                "merged_private_data": {"existing": "value", "pii_email_1": "alice@example.com"},
                "redacted_message_count": 1,
                "redacted_value_count": 1,
                "matched_known_pii_values": ["alice@example.com"],
                "unmatched_known_pii_values": ["bob@example.com"],
            }
        ]
    )
    client = Client(api_key="test-key")
    monkeypatch.setattr(client, "_base_url", "http://127.0.0.1:8000")
    monkeypatch.setattr(client, "_get_http_client", lambda: http_client)

    result = client.preview_redaction(
        payload=RedactionPreviewRequest(
            message=RedactedMessage(content="Email alice@example.com"),
            private_data={"existing": "value"},
            known_pii_values=["alice@example.com", "bob@example.com"],
        )
    )

    assert result.inserted_keys == ["pii_email_1"]
    assert result.added_private_data["pii_email_1"] == "alice@example.com"
    assert result.merged_private_data["existing"] == "value"
    assert result.matched_known_pii_values == ["alice@example.com"]
    assert result.unmatched_known_pii_values == ["bob@example.com"]
    assert http_client.calls[0]["method"] == "POST"
    assert http_client.calls[0]["url"].endswith("/preview-redaction")
    assert http_client.calls[0]["json"]["private_data"] == {"existing": "value"}
