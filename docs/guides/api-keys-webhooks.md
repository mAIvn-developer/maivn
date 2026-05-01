# API Keys and Webhooks

This guide covers secure key management and webhook operations in the portal.

## API Keys

Use API keys when your application needs server-to-server access.

### Key lifecycle

1. Create key with a descriptive name.
2. Copy plaintext key immediately (shown once).
3. Store in secure secret manager.
4. Rotate or revoke as needed.

![Developer Portal workspace dashboard](/developer_portal/maivn_portal_dashboard.png "Projects are the entry point for API key and webhook management")

![Project API keys table with active keys and the key-creation modal](/developer_portal/placeholders/project-api-keys.png "Project API keys page")

### Visibility model

Key visibility depends on backend authorization and project ownership context.

If project-scoped listing returns empty, the portal now falls back to account-visible keys and filters by project to avoid missing keys tied to your account.

## Webhooks

Webhooks let your systems receive event callbacks from the platform.

Each webhook includes:

- Endpoint URL
- Event list
- Active/inactive state
- Failure counters

### Reliability checklist

- Return a `2xx` status quickly.
- Verify webhook signatures before processing.
- Retry idempotently on duplicates/timeouts.
- Track consecutive failures and disable noisy endpoints.

![Project webhooks list with active endpoints](/developer_portal/placeholders/project-webhooks-list.png "Project webhooks page")

![Webhook delivery history page with status filters and empty state](/developer_portal/placeholders/webhook-deliveries.png "Webhook delivery history")

## Security Checklist

- Never embed API keys in frontend code.
- Scope keys to least privilege.
- Rotate keys on schedule and incident response.
- Use HTTPS-only webhook endpoints.
