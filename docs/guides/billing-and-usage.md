# Token and Usage Tracking

Every run reports how many tokens it used, so you can see exactly how much each
invocation consumed and track consumption over time. This guide covers the
per-response `TokenUsage` model, how usage adds up across nested and multi-agent
runs, and the account-level usage and plan models you can read through the SDK.

## Why It Matters

Token consumption is the unit that drives your usage. Knowing how many tokens a
given run spent lets you compare prompts, spot expensive paths, attribute usage
to a customer or feature, and stay ahead of plan limits — all without leaving
your application code.

The SDK gives you two views of this data:

- **Per-run usage** — a `TokenUsage` summary attached to every response, so you
  can read consumption immediately after a call.
- **Account usage and plan** — aggregated `BillingUsage` for your organization,
  broken down by project and API key, read on demand through the `Client`.

Token data is additive and optional. Reading it never changes how an agent
plans or executes; it is reporting only.

## Reading Token Usage Off a Response

Every `invoke()` / `ainvoke()` call returns a `SessionResponse`. When usage
data is available, it carries a `token_usage` field holding a `TokenUsage`
model.

```python
from maivn import Agent
from maivn.messages import HumanMessage

agent = Agent(name='assistant', api_key='...')

response = agent.invoke([HumanMessage(content='Summarize the latest release notes.')])

print(response.response)  # the final assistant text

usage = response.token_usage
if usage is not None:
    print(f'input:  {usage.input_tokens:,}')
    print(f'output: {usage.output_tokens:,}')
    print(f'total:  {usage.total_tokens:,}')
```

> **Note**
> `token_usage` is optional (`TokenUsage | None`). Always guard for `None`
> before reading fields — usage may be absent for some responses (for example
> certain error or interrupted states).

### The `TokenUsage` model

`TokenUsage` is a small, provider-normalized model: the same field names apply
no matter which underlying model served the call.

| Field                   | Type  | Description                                              |
| ----------------------- | ----- | -------------------------------------------------------- |
| `total_tokens`          | `int` | Total tokens used (input + output).                      |
| `input_tokens`          | `int` | Input / prompt tokens.                                   |
| `output_tokens`         | `int` | Output / completion tokens.                              |
| `cache_read_tokens`     | `int` | Tokens served from the provider's prompt cache.          |
| `cache_creation_tokens` | `int` | Tokens written to the provider's prompt cache.           |
| `reasoning_tokens`      | `int` | Reasoning / extended-thinking tokens, where applicable.  |

All fields are integers and default to `0`, so it is always safe to read and
sum them once you have a non-`None` `TokenUsage`.

```python
usage = response.token_usage
if usage is not None and usage.reasoning_tokens > 0:
    print(f'reasoning tokens: {usage.reasoning_tokens:,}')
if usage is not None and usage.cache_read_tokens > 0:
    print(f'served from cache: {usage.cache_read_tokens:,}')
```

> **Note**
> `TokenUsage` carries **token counts only** — there are no cost fields by
> design. For account-level totals and plan limits, read [account
> usage](#reading-current-usage-and-quota-via-the-sdk) instead.

## How Usage Aggregates Across Nested and Multi-Agent Runs

A single turn may involve more than one model call. An agent can plan with one
model, call tools, and synthesize a final answer — and a `Swarm` can route work
across several member agents within the same turn. Conceptually:

- Usage is **captured for each LLM call** as it happens.
- Those per-call counts are **propagated through execution** as the run
  progresses.
- They are **aggregated into one session-level summary** for the whole turn.

That means the `token_usage` on the `SessionResponse` you get back from
`agent.invoke(...)` or `swarm.invoke(...)` already reflects the *entire* turn —
planning, tool-driven steps, nested agent work, and final synthesis — rolled
into a single normalized `TokenUsage`. You do not need to add anything up by
hand.

```python
from maivn import Swarm
from maivn.messages import HumanMessage

swarm = Swarm(name='research-team', agents=[...])  # each member agent carries its own api_key

response = swarm.invoke(HumanMessage(content='Research and draft a one-paragraph summary.'))

# Already aggregated across every member agent and step in this turn.
if response.token_usage is not None:
    print(f'turn total: {response.token_usage.total_tokens:,} tokens')
```

> **Note**
> The summary is per turn. To track consumption over many turns of a
> conversation, accumulate the `total_tokens` from each response yourself, or
> read account-level usage (below) for a period-wide rollup.

## Public Usage and Plan Models

For account-level reporting, the SDK exposes a set of read-only models that
describe your organization's plan and aggregated usage for a billing period.
They are all importable from `maivn`:

```python
from maivn import (
    BillingUsage,
    BillingPlan,
    BillingCurrentUsage,
    BillingUsageBreakdown,
    BillingUsageProject,
    BillingUsageApiKey,
    BillingUsageTotals,
)
```

| Model                   | What it holds                                                                |
| ----------------------- | ---------------------------------------------------------------------------- |
| `BillingUsage`          | Top-level snapshot: `organization_id`, `period_start`, `period_end`, `plan`, `usage`, `breakdown`. |
| `BillingPlan`           | Current plan summary: `name`, `display_name`, `tier`.                        |
| `BillingCurrentUsage`   | Counters and limits for the period (see below).                              |
| `BillingUsageBreakdown` | The org → project → API-key breakdown for the period.                        |
| `BillingUsageProject`   | Usage attributed to one project, split by API key.                           |
| `BillingUsageApiKey`    | Usage attributed to one API key.                                             |
| `BillingUsageTotals`    | Period totals across every project and API key.                              |

### Usage counters and limits

`BillingCurrentUsage` reports what has been consumed in the period and the
applicable limits:

- `tokens_used`, `tokens_used_raw`, `tokens_limit`
- `requests_used`, `requests_limit`
- `storage_used`, `storage_limit`

A limit of `-1` means **unlimited**.

### Billed vs. raw tokens

The breakdown models expose two token counts side by side:

- `billed_tokens` — the count that depletes your quota (some models count at a
  higher multiplier).
- `raw_tokens` — what the model providers actually returned.

They are equal when only baseline-tier models were used. `BillingUsageProject`,
`BillingUsageApiKey`, and `BillingUsageTotals` each carry both, plus a
`requests` count.

### Unattributed usage

Usage that can't be tied to a specific API key — for example portal usage or
usage that predates key attribution — is never dropped. It surfaces in a bucket
with `api_key_id == None` named `"Unknown API key"` (and, where the project is
unknown, `project_id == None`). Account for it when you reconcile your own
downstream billing.

All of these models tolerate unknown fields, so the SDK keeps deserializing if
the mAIvn service adds new data.

## Reading Current Usage and Quota via the SDK

Read your organization's plan and aggregated usage with `Client.get_usage()`.
It returns a single `BillingUsage` for a billing period.

```python
from maivn import Client

client = Client(api_key='...')  # billing requires a full-access key

usage = client.get_usage()                      # current calendar month
# usage = client.get_usage(year=2026, month=5)  # a specific past month

# Plan + period
print(usage.plan.tier if usage.plan else 'no plan')
print(usage.period_start, '->', usage.period_end)

# Quota: consumed vs. limit (-1 means unlimited)
counters = usage.usage
print(f'{counters.tokens_used:,} / {counters.tokens_limit:,} tokens')
print(f'{counters.requests_used:,} / {counters.requests_limit:,} requests')
```

Walk the breakdown to attribute consumption to a project or API key — for
example to bill your own downstream customers:

```python
for project in usage.breakdown.projects:
    print(project.project_name, f'{project.billed_tokens:,} billed tokens')
    for key in project.api_keys:
        print('  ', key.api_key_name,
              f'{key.billed_tokens:,} tokens', key.requests, 'req')
```

> **Note**
> Omit both `year` and `month` to read the **current** calendar month; pass
> **both** to read a past month. The data is always scoped to the organization
> your key belongs to. See the [SDK Reference (Billing)](../api/billing.md) for
> the full method signature, parameters, and error cases.

A simple quota guard before launching a large batch:

```python
usage = client.get_usage()
counters = usage.usage

if counters.tokens_limit != -1:  # -1 = unlimited
    remaining = counters.tokens_limit - counters.tokens_used
    if remaining < 1_000_000:
        print(f'Low on quota: {remaining:,} tokens left this period.')
```

## Next Steps

- [Weighted Token Billing](./weighted-token-billing.md) - how provider-neutral
  model tiers translate raw usage into billed usage.
- [Sessions, Invocation, and Streaming](./sessions-and-streaming.md) — read
  responses and live events from a run, including where usage data appears.
- [SDK Reference (Billing)](../api/billing.md) — full `get_usage()` signature,
  return fields, and errors.
- [Structured Output](./structured-output.md) — typed results via
  `response.result`.
- [Multi-Agent](./multi-agent.md) — how swarms route a turn across member
  agents (whose usage rolls into one summary).
