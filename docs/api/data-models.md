# Shared Data Models

The typed Pydantic shapes you construct as agent input (messages, private-data descriptors) and read back as agent output (response objects, event streams, token counts), shared across the mAIvn libraries.

These are the structural counterpart to the [`maivn` SDK docs](README.md): that reference describes *behavior* (how to build, invoke, and stream an agent), while this page describes the *shapes* that behavior produces and consumes.

> **Note**
> Most of these models are importable from the public SDK — `maivn`, `maivn.messages`, or `maivn.events`, as shown in each section — and you never reach into a library's internals to use them. A few (like `TokenUsage`) you only ever read off a response and never import. In practice you *read* most of these shapes off a response, so most code needs no explicit import at all.

## SessionResponse and RawSSEEvent

These are the two shapes an invocation hands back.

### SessionResponse

`Agent.invoke()`, `Agent.ainvoke()`, `Swarm.invoke()`, and `Swarm.ainvoke()` all return a single `SessionResponse`. It is the canonical "what happened in this turn" object.

```python
from maivn import Agent
from maivn.messages import HumanMessage

agent = Agent(name='assistant', api_key='...')
response = agent.invoke([HumanMessage(content='Summarize this ticket.')])

print(response.response)   # final assistant text
print(response.result)     # structured / final-tool output, when applicable
print(response.status)     # 'completed' | 'failed' | 'interrupted' | ...
```

| Field          | Type                  | What it holds                                                                                          |
| -------------- | --------------------- | ------------------------------------------------------------------------------------------------------ |
| `response`     | `str \| None`         | The final assistant text — the last entry in `responses`. The field you read most often.               |
| `responses`    | `list[str]`           | All assistant response texts emitted during the turn (status lines plus the final synthesized answer). |
| `result`       | `object \| None`      | Structured or final-tool output, when applicable. With `structured_output(...)` this is your typed model. |
| `status`       | `str \| None`         | Execution status: `pending`, `running`, `waiting_for_tools`, `completed`, `failed`, or `interrupted`.   |
| `error`        | `str \| None`         | A failure message when the turn did not complete cleanly.                                               |
| `token_usage`  | `TokenUsage \| None`  | Token counts for the turn, when available (see [TokenUsage](#tokenusage)).                              |
| `messages`     | `list[BaseMessage]`   | The messages exchanged during the session.                                                              |
| `metadata`     | `dict \| None`        | Response metadata associated with the session, when available.                                          |
| `session_id`   | `str`                 | Unique identifier for the session.                                                                      |
| `thread_id`    | `str \| None`         | Conversation thread identifier, for multi-turn sessions.                                                 |
| `assistant_id` | `str \| None`         | Assistant identifier associated with the session.                                                       |
| `started_at`   | `str`                 | ISO-8601 timestamp when the session was created.                                                        |
| `completed_at` | `str \| None`         | ISO-8601 timestamp when the session completed, when applicable.                                          |

> **`response` vs. `result`**
> `response` is the human-readable answer. `result` is machine-readable output — populated when you force a final tool or request [structured output](../guides/structured-output.md). A typical text turn fills `response` and leaves `result` as `None`; a structured-output turn fills both.

```python
from pydantic import BaseModel

class Summary(BaseModel):
    title: str
    bullets: list[str]

response = agent.structured_output(Summary).invoke(
    [HumanMessage(content='Summarize this ticket.')]
)

summary = response.result   # a typed Summary instance
print(summary.title, summary.bullets)
```

### RawSSEEvent

`Agent.stream()` and `Swarm.stream()` yield a sequence of `RawSSEEvent` values as the turn executes — one per server-sent event. Import it from `maivn.events`. It is a deliberately minimal wire shape:

```python
from maivn.events import RawSSEEvent

# RawSSEEvent fields:
#   name: str               event name from the stream, e.g. 'system_tool_chunk', 'final'
#   payload: JsonValue = {} decoded event payload (JSON value; defaults to an empty dict)
```

You can read it directly when you just need the raw stream:

```python
for event in agent.stream([HumanMessage(content='Think through this.')]):
    if event.name == 'system_tool_chunk':
        print(event.payload.get('text', ''), end='')
    elif event.name == 'final':
        print('\nDone')
```

> **Tip**
> For product integrations (forwarding execution state to a frontend), do not consume the raw stream directly. Normalize it once with `maivn.events.normalize_stream(...)`, which converts these raw `RawSSEEvent` values into a stable, descriptor-rich `AppEvent` contract. See the [Events reference](events.md) and the [Frontend Events guide](../guides/frontend-events.md).

## TokenUsage

`TokenUsage` is the token-count summary attached to `SessionResponse.token_usage`. It normalizes usage across model providers into one consistent shape. You read it directly off the response — no import is required:

```python
usage = response.token_usage
if usage is not None:
    print(usage.total_tokens, 'total')
    print(usage.input_tokens, 'in /', usage.output_tokens, 'out')
```

| Field                   | Type  | Meaning                                              |
| ----------------------- | ----- | ---------------------------------------------------- |
| `total_tokens`          | `int` | Total tokens used (input + output).                  |
| `input_tokens`          | `int` | Input / prompt tokens.                               |
| `output_tokens`         | `int` | Output / completion tokens.                          |
| `cache_read_tokens`     | `int` | Tokens served from the provider's prompt cache.      |
| `cache_creation_tokens` | `int` | Tokens written to the provider's prompt cache.       |
| `reasoning_tokens`      | `int` | Reasoning / extended-thinking tokens, where the model reports them. |

All fields are plain integers and default to `0`, so it is always safe to read and sum them.

> **No cost by design**
> `TokenUsage` carries token *counts* only — there are deliberately no cost-in-currency fields on this model. Token counts are stable, provider-normalized, and safe to log. For billed amounts and quota, read the [billing models](#public-billingusage-models) via `Client.get_usage()`. Cost is account-level data, not a per-turn field.

## Public Billing / Usage Models

These models describe an organization's plan and usage for a billing period. You receive them from `Client.get_usage()` (covered in full in the [Billing & Usage reference](billing.md)); they are listed here as the shapes you read off that call. Every model is importable from `maivn` and ignores unknown fields, so the SDK keeps deserializing if the server adds data.

`get_usage()` returns a single `BillingUsage` snapshot:

| Field             | Type                    | Description                                            |
| ----------------- | ----------------------- | ------------------------------------------------------ |
| `organization_id` | `str`                   | The organization the key belongs to.                   |
| `period_start`    | `str`                   | ISO-8601 start of the billing period (inclusive).      |
| `period_end`      | `str`                   | ISO-8601 end of the billing period (inclusive).        |
| `plan`            | `BillingPlan \| None`   | Current plan summary, or `None` if no active plan.     |
| `usage`           | `BillingCurrentUsage`   | Usage counters and their limits.                       |
| `breakdown`       | `BillingUsageBreakdown` | Usage grouped org → project → API key.                 |

The nested shapes:

- **`BillingPlan`** — `name`, `display_name`, `tier`.
- **`BillingCurrentUsage`** — `tokens_used`, `tokens_used_raw`, `tokens_limit`, `requests_used`, `requests_limit`, `storage_used`, `storage_limit`. A limit of `-1` means unlimited.
- **`BillingUsageBreakdown`** — `organization_id`, `period_start`, `period_end`, `totals` (a `BillingUsageTotals`), and `projects` (a list of `BillingUsageProject`).
- **`BillingUsageTotals`** — `billed_tokens`, `raw_tokens`, `requests`.
- **`BillingUsageProject`** — `project_id` (`None` for the unattributed bucket), `project_name`, `billed_tokens`, `raw_tokens`, `requests`, and `api_keys` (a list of `BillingUsageApiKey`).
- **`BillingUsageApiKey`** — `api_key_id` (`None` for the "Unknown API key" bucket), `api_key_name`, `billed_tokens`, `raw_tokens`, `requests`.

```python
from maivn import Client

client = Client(api_key='...')   # full-access key required for billing
usage = client.get_usage()       # current month; pass year=/month= for a past period

print(usage.plan.tier if usage.plan else 'no plan')
print(f'{usage.usage.tokens_used:,} / {usage.usage.tokens_limit:,} tokens')

for project in usage.breakdown.projects:
    print(project.project_name, f'{project.billed_tokens:,} billed tokens')
    for key in project.api_keys:
        print('  ', key.api_key_name, key.requests, 'requests')
```

> **Billed vs. raw tokens**
> `billed_tokens` is the count that depletes your quota (premium models cost more than baseline-tier ones); `raw_tokens` is what the providers actually returned. They are equal when only baseline-tier models were used. See the [Billing & Usage reference](billing.md) for the full method signature, error codes, and reconciliation examples.

## Privacy Models

These shapes let you declare sensitive data so it is handled safely rather than passed to the model in cleartext. The conceptual model is: the LLM plans against a **schema** (field names, types, and labels), and real values are injected into tools only at execution time. The companion behavior — how to attach `private_data` to an agent and inject values into tools — lives in the [Private Data guide](../guides/private-data.md); this section covers the shapes you construct.

Import them from the SDK:

```python
from maivn.messages import RedactedMessage, PrivateData
from maivn import PIIWhitelist, PIIWhitelistEntry, HIPAA_SAFE_HARBOR_CATEGORIES
```

### PrivateData

A structured descriptor for a known sensitive value. It enriches the schema the model sees with a custom key name, type, and label — without ever exposing the value itself to the model by default.

```python
PrivateData(
    value: str,                     # the actual sensitive value (required)
    name: str | None = None,        # custom placeholder key, e.g. 'patient_name'
    pii_type: str | None = None,    # entity type: 'person', 'phone', 'email', 'date', ...
    label: str | None = None,       # human-readable label shown in the schema
    description: str | None = None, # extra context for the model
    format: str | None = None,      # semantic format hint: 'email', 'phone', 'date', ...
)
```

When you supply `name`, the value is registered under your key (e.g. `patient_name`) instead of an auto-generated one. You can pass `PrivateData` objects either in a message's `known_pii_values` or directly as an agent's `private_data`.

```python
agent = Agent(
    name='intake',
    api_key='...',
    private_data=[
        PrivateData(value='Maria Santos', name='patient_name', pii_type='person',
                    label='Patient Name'),
        PrivateData(value='MEM-882441', name='member_id', label='Member ID'),
    ],
)
```

### RedactedMessage

A message type that triggers automatic PII detection on the user-supplied text. Send one instead of a `HumanMessage` when the input may contain sensitive values you want detected and redacted before any model-visible context is built.

```python
RedactedMessage(
    content: Any,
    known_pii_values: list[str | PrivateData] | None = None,
    pii_whitelist: PIIWhitelist | None = None,
    attachments: list[dict[str, Any]] | None = None,
    allow_attachment_file_paths: bool = True,
)
```

```python
from maivn.messages import RedactedMessage

response = agent.invoke([
    RedactedMessage(content='My email is john@example.com and SSN is 123-45-6789.')
])
```

Use `known_pii_values` to declare values explicitly (raw strings or `PrivateData` descriptors) so they are scrubbed even if auto-detection does not catch them. The runtime detects common identifier categories — email, phone, SSN, credit card, IBAN/SWIFT, medical record numbers, VINs, and named entities such as person, location, date, IP, and URL — each paired with a structural validator to avoid flagging look-alikes like order numbers.

### PIIWhitelist and PIIWhitelistEntry

Configuration for leaving *approved* spans in cleartext. The whitelist is evaluated after detection (so the value is still recorded as having been present) but before the value would be registered as private — so the matched span passes through.

```python
PIIWhitelist(
    entries: tuple[PIIWhitelistEntry, ...] = (),  # a list is also accepted and coerced
    phi_mode: bool = False,
)

PIIWhitelistEntry(
    entity_type: str | None = None,   # one-of: a whole category
    pattern: str | None = None,       # one-of: a regex
    value: str | None = None,         # one-of: a literal value
    justification: str = ...,         # required, >= 8 characters
    label: str | None = None,
)
```

Each entry matches by one of `entity_type`, `pattern`, or `value`, and every entry requires a `justification` of at least 8 characters. Setting `phi_mode=True` refuses `entity_type` entries for any HIPAA Safe Harbor category at construction (raising `ValueError`) — use a specific `value` or `pattern` for individually approved instances instead. Both models are frozen (immutable after construction).

```python
whitelist = PIIWhitelist(
    entries=[
        PIIWhitelistEntry(
            entity_type='url',
            justification='Public marketing URLs needed for citations.',
        ),
        PIIWhitelistEntry(
            value='support@maivn.io',
            justification='Public support address listed on the docs site.',
        ),
    ],
)

message = RedactedMessage(
    content='See https://maivn.io and email support@maivn.io',
    pii_whitelist=whitelist,
)
```

### HIPAA_SAFE_HARBOR_CATEGORIES

An exported constant: a frozenset of the canonical entity-type names that `phi_mode=True` blocks from `entity_type` whitelisting. Use it to validate your own policy before constructing a `PIIWhitelist`.

```python
from maivn import HIPAA_SAFE_HARBOR_CATEGORIES

if 'person' in HIPAA_SAFE_HARBOR_CATEGORIES:
    # ... avoid a category-wide whitelist for names under phi_mode
    ...
```

## Message Types

Messages are how you talk to an agent and how it talks back. They live in `maivn.messages` and are the input shapes for every `invoke()` / `stream()` call.

```python
from maivn.messages import HumanMessage, AIMessage, SystemMessage
```

| Type            | Direction | What it is                                                                       |
| --------------- | --------- | -------------------------------------------------------------------------------- |
| `HumanMessage`  | input     | User input to the agent. Supports `content` plus optional `attachments`.         |
| `AIMessage`     | output    | An assistant response. Usually read off a result, rarely constructed by hand.    |
| `SystemMessage` | input     | A system prompt that sets agent behavior. Often passed as the constructor's `system_prompt`. |

```python
from maivn.messages import SystemMessage, HumanMessage

messages = [
    SystemMessage(content='You are a concise Python expert.'),
    HumanMessage(content='How do I read a file?'),
]
response = agent.invoke(messages)
```

If you set `system_prompt` on the `Agent` constructor and your messages do not include a `SystemMessage`, one is injected automatically. `RedactedMessage` (above) is the privacy-aware sibling of `HumanMessage`. For the full message surface — attachments, multi-turn patterns, and `BaseMessage` for type hints — see the [Messages reference](messages.md).

## See Also

- [SDK Reference](README.md) — the behavioral surface: `Agent`, `Swarm`, tools, events, scheduling.
- [Messages reference](messages.md) — full message and privacy-model details, attachments, and patterns.
- [Private Data guide](../guides/private-data.md) — how the schema-only planning and runtime-injection model works end to end.
- [Billing & Usage reference](billing.md) — `Client.get_usage()`, billed vs. raw tokens, per-customer reconciliation.
- [Events reference](events.md) — normalizing the raw stream into the `AppEvent` contract for frontends.
