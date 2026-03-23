# Billing and Usage

This guide explains subscription plans, usage tracking, and invoices in the portal.

## Billing Surfaces

The portal provides four billing pages per organization:

- `Subscription`: current plan and period status
- `Usage`: quota cards and analytics charts for tokens, requests, and storage
- `Executions`: run-level inspection with event tree and structured output
- `Invoices`: historical billing records

![image placeholder: Billing subscription page showing current plan and status](/developer_portal/placeholders/billing-subscription.png "Capture with demo organization and non-sensitive billing labels only")

## Usage Interpretation

Usage is shown as:

- absolute consumed units
- plan limit
- percentage consumed

Color coding helps triage saturation risk:

- normal: healthy headroom
- warning: nearing limits
- critical: near or above safe operating threshold

## Usage Intelligence View

The Usage page now supports richer analysis controls:

- organization selector for multi-org operators
- project slice filtering (for usage records that include project metadata)
- metric filtering (`tokens`, `requests`, `storage`)
- time windows (`24h`, `7d`, `30d`, `90d`, custom)

Visuals include:

- quota cards for tokens, requests, and storage
- timeline chart for usage deltas across the selected window
- project distribution bars for quick cost-center attribution
- execution telemetry overlays (for example token-mix and run-count context)

For per-run drill-down, use the `Executions` page.

## Execution Explorer

Open `Billing -> Executions` (or `/billing/{org_id}/executions`) to inspect individual runs:

1. Select a run in the left rail.
2. Review high-level token and duration stats.
3. Expand action and assignment nodes to drill into tool/system events.
4. Inspect event metadata and sanitized previews for troubleshooting.

The event stream is intentionally redacted and summarized. Sensitive keys and raw secrets are not surfaced in this UI.

## Plan Changes

When you change plans:

1. New limits are applied to the organization.
2. Billing period remains aligned with current cycle rules.
3. Team members immediately see updated usage ceilings.

![image placeholder: Billing usage dashboard with token, request, and storage charts](/developer_portal/placeholders/billing-usage.png "Capture percentage bars using synthetic usage numbers")

![image placeholder: Usage intelligence view with filters, charts, and project distribution](/developer_portal/placeholders/billing-usage-intelligence.png "Capture filters, timeline chart, and project distribution using synthetic, non-sensitive data")

![image placeholder: Executions explorer with run list and per-run event tree](/developer_portal/placeholders/billing-executions-explorer.png "Capture run summary cards, selected run detail, and event tree using synthetic data")

![image placeholder: Billing invoices table with period, amount, and status](/developer_portal/placeholders/billing-invoices.png "Capture test invoice rows with sample amounts only")

## Operational Playbook

1. Review usage weekly.
2. Set alerting near 75% and 90% thresholds.
3. Upgrade before hard limits impact customer traffic.
4. Reconcile invoices monthly with finance systems.
