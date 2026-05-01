# Workspace Operations

This guide covers day-to-day use of Organizations, Projects, and navigation inside the portal.

## Organizations

Organizations define billing ownership and team-level access.

Typical flow:

1. Create an organization.
2. Invite members.
3. Assign role (`owner`, `admin`, `member`, `viewer`).
4. Create projects under that organization.

![Developer Portal workspace dashboard](/developer_portal/maivn_portal_dashboard.png "Organization and project overview context")

### Role Guidance

- `owner`: full control including destructive operations.
- `admin`: team and billing management.
- `member`: day-to-day project operations.
- `viewer`: read-only access.

### Organization Memory Management

Organization settings include memory policy controls for teams using long-running context:

- enable/disable org-level memory features
- set persistence ceiling (`persist_none`, `vector_only`, `vector_plus_graph`)
- set optional retention windows for vector and graph memory
- run explicit purge operations for org/project/session scopes
- manage destructive confirmation for purge workflows (`PURGE_MEMORY`)

These controls are intended for governance and cost/security posture, while per-request metadata still controls invocation-level behavior within the organization ceiling.

Operational guidance:

- treat org policy as the hard guardrail, and request metadata as a narrower override
- use lower memory levels (`none`/`glimpse`) for cost-sensitive workloads
- use higher levels (`focus`/`clarity`) only for workloads that need persistent recall
- use purge controls for lifecycle/compliance workflows

### Project Memory Operations

Project workspaces include dedicated memory pages, listed in the sidebar in this
order:

- `Memory Resources` (`/projects/{project_id}/memory/resources`)
- `Memory Skills` (`/projects/{project_id}/memory/skills`)
- `Memory Insights` (`/projects/{project_id}/memory/insights`)

Use these pages to curate reusable memory assets and manage lifecycle state (create/update/bind/promote/delete/restore).

For full operational guidance, see [Portal Memory Management](portal-memory-management.md).

## Projects

Projects are operational containers for API keys, webhooks, and usage attribution.

Each project includes:

- Name and status
- Organization association
- API keys
- Webhooks

## "Current" Navigation Behavior

Sidebar links like `Current Project -> API Keys` resolve to your most recently visited project.

If no prior project exists, the portal falls back to your first accessible project.

## Recommended Team Workflow

1. Create one organization per company or business unit.
2. Create one project per product/service boundary.
3. Keep production keys isolated to production projects.
4. Use webhooks for automation and audit visibility.

![Developer Portal docs experience](/developer_portal/maivn_portal_docs__getting_started.png "Use integrated docs during team onboarding and workspace setup")

![Organization members and invitations management pages](/developer_portal/placeholders/organization-members-invitations.png "Members and invitations management")
