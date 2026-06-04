# Billing & Usage

`Client.get_usage()` reads your organization's billing and usage data: the
current plan, usage-versus-limits, and a breakdown grouped **organization →
project → API key** (the same breakdown shown on your invoices).

## Import

```python
from maivn import Client, BillingUsage
```

## Authorization

`get_usage()` authenticates with the API key you pass to the `Client` and
requires a **full-access (`all`)** key, because billing is account-level
data. Read-only and restricted keys are rejected with `403`. (Under the hood
the SDK attaches your key to each request; you never set a header yourself.)

The data returned is always scoped to the organization the key belongs to
(derived from the key's project) — you cannot read another organization's usage.

Manage keys and permissions in the mAIvn Developer Portal.

## Method

```python
def get_usage(
    *,
    year: int | None = None,
    month: int | None = None,
) -> BillingUsage
```

| Parameter | Type          | Default | Description                                                          |
| --------- | ------------- | ------- | -------------------------------------------------------------------- |
| `year`    | `int \| None` | `None`  | Calendar year of the period to fetch. Pass together with `month`.    |
| `month`   | `int \| None` | `None`  | Calendar month (1–12). Pass together with `year`.                    |

Omit both `year` and `month` to get the **current calendar month**. Pass both to
fetch a past month. Passing only one raises a validation error (`422`).

## Quickstart

```python
from maivn import Client

client = Client(api_key="...")  # must be a full-access ('all') key

usage = client.get_usage()                      # current month
# usage = client.get_usage(year=2026, month=5)  # a specific past month

print(usage.organization_id)
print(usage.plan.tier if usage.plan else "no plan")
print(f"{usage.usage.tokens_used:,} / {usage.usage.tokens_limit:,} tokens")

for project in usage.breakdown.projects:
    print(project.project_name, f"{project.billed_tokens:,} tokens")
    for key in project.api_keys:
        print("  ", key.api_key_name, f"{key.billed_tokens:,} tokens", key.requests, "req")
```

## Return value

`get_usage()` returns a `BillingUsage`:

| Field             | Type                    | Description                                            |
| ----------------- | ----------------------- | ------------------------------------------------------ |
| `organization_id` | `str`                   | The organization the key belongs to.                   |
| `period_start`    | `str`                   | ISO-8601 start of the billing period (inclusive).      |
| `period_end`      | `str`                   | ISO-8601 end of the billing period (inclusive).        |
| `plan`            | `BillingPlan \| None`   | Current plan summary, or `None` if no active plan.     |
| `usage`           | `BillingCurrentUsage`   | Usage counters and their limits.                       |
| `breakdown`       | `BillingUsageBreakdown` | Usage grouped by project and API key.                  |

`BillingPlan`: `name`, `display_name`, `tier`.

`BillingCurrentUsage`: `tokens_used`, `tokens_used_raw`, `tokens_limit`,
`requests_used`, `requests_limit`, `storage_used`, `storage_limit`. A limit of
`-1` means unlimited.

`BillingUsageBreakdown`: `organization_id`, `period_start`, `period_end`,
`totals` (`BillingUsageTotals`: `billed_tokens`, `raw_tokens`, `requests`), and
`projects` (`list[BillingUsageProject]`).

`BillingUsageProject`: `project_id` (`None` for the unattributed bucket),
`project_name`, `billed_tokens`, `raw_tokens`, `requests`, and `api_keys`
(`list[BillingUsageApiKey]`).

`BillingUsageApiKey`: `api_key_id` (`None` for the **"Unknown API key"** bucket),
`api_key_name`, `billed_tokens`, `raw_tokens`, `requests`.

### Billed vs. raw tokens

`billed_tokens` is the count that depletes quota and drives billing — premium
models cost more, so they bill at a higher rate than baseline models.
`raw_tokens` is what the model providers actually returned. The two are equal
when only baseline-rate models were used.

### Unattributed usage

Usage that can't be tied to a specific API key — for example portal/JWT usage,
internal paths, or usage that predates API-key attribution — is **never
dropped**. It surfaces in a bucket with `api_key_id == None` and the name
`"Unknown API key"` (and, where the project is unknown, `project_id == None`).
Account for it so your own downstream billing reconciles.

All models are exported from `maivn` and tolerate unknown fields, so the SDK
keeps deserializing if the server adds data.

## Errors

| Status | Meaning                                                                       |
| ------ | ----------------------------------------------------------------------------- |
| `401`  | No / invalid API key. Set a valid key on the `Client`.                        |
| `403`  | The key is not full-access (`all`). Use a full-access key for billing.        |
| `404`  | The key's project has no associated organization.                             |
| `422`  | Only one of `year` / `month` was provided — pass both, or neither.            |

## Example: month-over-month per-customer reconciliation

```python
client = Client(api_key="...")

may = client.get_usage(year=2026, month=5)
for project in may.breakdown.projects:
    # Map each project (or API key) to one of your customers and bill accordingly.
    billed = project.billed_tokens
    print(f"{project.project_name}: {billed:,} billed tokens in May 2026")
```

## See Also

- [Client](client.md) — the client that hosts `get_usage()`
- [Configuration](configuration.md) — API key and environment configuration
