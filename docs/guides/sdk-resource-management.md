# SDK Resource Management

This guide covers programmatic memory resource management through the mAIvn SDK.

Use the SDK `Client` when you need to curate memory resources outside of an agent invocation flow, such as admin scripts, deployment pipelines, sync jobs, or operational tooling.

## Scope

The SDK exposes two related management surfaces:

- project memory resources: skills, insights, and bound resources
- organization memory governance: memory policy and purge controls

## Import

```python
from maivn import Client
```

## Create a Reusable Client

```python
client = Client(api_key="your-api-key")
```

You can reuse the same `Client` across:

- admin scripts
- background jobs
- deployment utilities
- multiple agents and swarms

## Organization Memory Governance

Use organization-level controls for hard policy and explicit purge operations.

### Read Policy

```python
policy = client.get_organization_memory_policy("org_123")
print(policy.persistence_ceiling)
```

### Update Policy

```python
updated = client.update_organization_memory_policy(
    "org_123",
    {
        "enabled": True,
        "persistence_ceiling": "vector_plus_graph",
        "vector_retention_days": 365,
        "graph_retention_days": 90,
    },
)
```

### Purge Memory

```python
result = client.purge_organization_memory(
    "org_123",
    project_id="project_456",
)
print(result.tables)
```

Purge requires the explicit confirmation token handled by the SDK method.

## Project Memory Resources

Project resource management includes:

- skills
- insights
- document-backed resources

### List All Project Resources

```python
resources = client.list_project_memory_resources("project_456")
print(len(resources.skills))
print(len(resources.insights))
print(len(resources.resources))
```

### Manage Skills

```python
skill = client.create_memory_skill(
    "project_456",
    {
        "name": "deploy_with_health_checks",
        "description": "Deploy, run health checks, then cut over traffic.",
        "steps": [
            {"index": 1, "action": "deploy service", "tool": "deploy_service"},
            {"index": 2, "action": "run health checks", "tool": "run_health_checks"},
        ],
        "sharing_scope": "project",
    },
)

skills = client.list_memory_skills("project_456", sharing_scope="project")

client.update_memory_skill(
    "project_456",
    skill.id,
    {
        "status": "deprecated",
    },
)
```

### Manage Insights

```python
insight = client.create_memory_insight(
    "project_456",
    {
        "insight_type": "warning",
        "content": "Rollback immediately if canary validation fails.",
        "relevance_score": 0.92,
        "sharing_scope": "project",
    },
)

client.promote_memory_insight(
    "project_456",
    insight.id,
    target_scope="org",
)
```

### Manage Resources

Use the resource-named SDK methods for new code.

```python
resource = client.create_memory_resource(
    "project_456",
    {
        "name": "deploy-runbook.txt",
        "mime_type": "text/plain",
        "content_base64": "U29tZSBiYXNlNjQgY29udGVudA==",
        "description": "Primary deployment runbook.",
        "tags": ["deploy", "runbook"],
        "sharing_scope": "project",
    },
)

listed = client.list_memory_resources("project_456", tags=["deploy"])
detail = client.get_memory_resource("project_456", resource.id)
```

### Replace, Bind, and Restore Resources

```python
replaced = client.replace_memory_resource(
    "project_456",
    resource.id,
    {
        "name": "deploy-runbook.txt",
        "content_base64": "TmV3IGJhc2U2NCBjb250ZW50",
        "mime_type": "text/plain",
    },
)

bound = client.bind_memory_resource(
    "project_456",
    replaced.id,
    binding_type="agent",
    target_id="agent_123",
)

restored = client.restore_memory_resource("project_456", replaced.id)
```

### Unbound Cleanup Review

```python
candidates = client.list_unbound_memory_resource_candidates(
    "project_456",
    min_age_days=90,
)

for candidate in candidates:
    print(candidate.id, candidate.name)
```

## Project Scope vs Org Scope

Project resource endpoints can manage either project-shared or org-shared resources.

Use:

- `sharing_scope="project"` for project-local reuse
- `sharing_scope="org"` for org-shared reuse across projects in the same organization

This applies to:

- `create_memory_skill()` / `update_memory_skill()`
- `create_memory_insight()` / `update_memory_insight()`
- `create_memory_resource()` / `update_memory_resource()`

## Recommended Operating Model

1. Set organization memory policy first.
2. Create project-local resources by default.
3. Promote or create org-shared resources only when reuse across projects is intentional.
4. Use tags and descriptions to keep resources discoverable.
5. Periodically review unbound or superseded resources.

## Related Guides

- [Memory and Recall](memory-and-recall.md)
- [Portal Memory Management](portal-memory-management.md)
- [Client API](../api/client.md)
