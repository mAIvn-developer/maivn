# Private Data and PII Protection

Your secrets and your users' personal data never reach the AI model in the clear. This guide shows how the mAIvn SDK keeps API keys, passwords, and PII out of the model — and how to control exactly what is protected.

## Why It Matters

When you build with an LLM, two kinds of sensitive data tend to flow toward the model:

- **Your secrets** — API keys, database passwords, signing tokens that your tools need to do their job.
- **Your users' personal data** — emails, phone numbers, SSNs, medical record numbers, and other PII that shows up in a message.

You usually want the model to *reason about* this data ("call the payments API", "email this customer") without ever *seeing the actual values*. Leaking a live API key or a patient's SSN into a model prompt — or into a tool result, a log line, or an external search query — is exactly the kind of mistake that is hard to undo.

The mAIvn SDK is built so this leak does not happen by default:

1. **The model plans against a schema** — it sees field *names*, *types*, and *labels*, never the raw values.
2. **Real values are injected only at tool-execution time**, inside the runtime, just before your code runs.
3. **Tool results are scanned and redacted** so a private value can't ride back to the model in an output.
4. **User message text can be auto-scrubbed** for PII before it ever reaches the model, via `RedactedMessage`.

The rest of this guide shows the SDK surface that makes each of those true.

## Declaring Private Data

Private data lives on an `Agent` or `Swarm` via the `private_data` field. The simplest form is a plain dictionary of key/value pairs.

```python
from maivn import Agent

agent = Agent(name='api_agent', api_key='...')

agent.private_data = {
    'api_key': 'sk-xxx-secret',
    'db_password': 'super-secret-password',
    'user_ssn': '123-45-6789',
}
```

You can also set it at construction time:

```python
agent = Agent(
    name='api_agent',
    api_key='...',
    private_data={'api_key': 'sk-xxx-secret'},
)
```

`Swarm` accepts the same `private_data` field, shared across its member agents.

### The `PrivateData` Model

For more control, declare values with the `PrivateData` model instead of a bare string. This enriches the schema the model plans against with a custom key name, a human-readable label, an entity type, and a format hint — so the model can reason more precisely about a redacted field without seeing its value.

```python
from maivn import PrivateData

PrivateData(
    value='Maria Santos',       # The actual value (required, never shown to the model)
    name='patient_name',        # Custom key name (instead of an auto-generated one)
    pii_type='person',          # Entity type for categorization
    label='Patient Name',       # Human-readable label in the schema
    description='Full name of the patient from the intake form',
    format='name',              # Semantic format hint
)
```

Pass a list of `PrivateData` objects to `private_data` on an `Agent` or `Swarm`:

```python
agent = Agent(
    name='intake',
    api_key='...',
    private_data=[
        PrivateData(value='claims@hospital.com', name='claims_hotline',
                    label='Claims Hotline', format='email'),
        PrivateData(value='reviews@hospital.com', name='medical_review_email',
                    label='Medical Review Contact', format='email'),
    ],
)
```

> **Note**
> User-supplied `name` values are stored with a `user_` prefix (for example `user_claims_hotline`) so a caller can't collide with an auto-generated slot. Tools that consume the value should reference the prefixed key.

| Feature | Raw string | `PrivateData` |
| --- | --- | --- |
| Key name | Auto-generated | Custom (`user_<name>`) |
| Entity type | Inferred | Explicitly declared |
| Schema label | None | Custom |
| Schema description | None | Custom |
| Format hint | Inferred | Explicitly declared |

## Injecting Values into Tools

A tool receives a real private value through the `@depends_on_private_data` decorator. You map a `data_key` (the name in `private_data`) to an `arg_name` (the parameter on your function).

```python
from maivn import depends_on_private_data

@agent.toolify(description='Call external API')
@depends_on_private_data(data_key='api_key', arg_name='key')
def call_api(endpoint: str, key: str) -> dict:
    # 'key' holds the real 'sk-xxx-secret' value at runtime
    return {'status': 'success'}
```

Stack the decorator to inject several values into the same tool:

```python
@agent.toolify(description='Connect to database')
@depends_on_private_data(data_key='db_host', arg_name='host')
@depends_on_private_data(data_key='db_password', arg_name='password')
def connect_db(query: str, host: str, password: str) -> dict:
    return {'connected': True, 'result': [...]}
```

The model decides *when* to call the tool, but it never supplies these arguments — they are filled in for you. See the [Dependencies guide](dependencies.md) for combining private-data injection with tool, agent, and interrupt dependencies.

## What the Model Sees vs What Runs

This is the core of the security model: **the planning stage and the execution stage see different things.**

**At planning time**, the model receives a schema describing the available private data — names, types, and labels only:

```
Available private data fields:
- api_key (string): API key for external service
- db_password (string): Database password
```

It plans which tools to call and in what order, reasoning purely against that schema. The actual values are never part of the prompt.

**At execution time**, inside the runtime, the real values are looked up and injected into the matching tool arguments just before your function runs. Your tool executes with the real secret; the model is none the wiser.

You can also reference a private value inside a system prompt by its key. The runtime resolves the reference at execution time, so the prompt the model sees carries the placeholder rather than the real value:

```python
agent = Agent(
    name='notifier',
    system_prompt=(
        'You are a notification assistant. '
        "The user's phone is available as the 'phone_number' private value. "
        'Never reveal the actual phone number in your responses.'
    ),
    api_key='...',
)

agent.private_data = {'phone_number': '+1-555-123-4567'}
```

For most cases, prefer injecting the value straight into a tool argument with `@depends_on_private_data(arg_name=...)` (shown above) — it needs no special prompt syntax and keeps the value out of the prompt entirely.

## Automatic Redaction in Tool Results

A tool might accidentally echo a private value back in its output — for example by interpolating a secret into a message string. The runtime scans tool results and redacts any known private value before the result returns to the model, so the leak is contained even when the tool author makes a mistake.

```python
@agent.toolify()
@depends_on_private_data(data_key='secret', arg_name='secret')
def leaky_tool(secret: str) -> dict:
    # Bad practice: putting the secret in the output.
    return {'message': f'Used secret: {secret}'}

# The runtime redacts the 'secret' value from the result
# before the model ever sees it.
```

This is a safety net, not a license to be careless. Treat private values as write-only inside a tool: use them, never return or log them. See [Best Practices](#best-practices) below.

## Automatic PII Detection with `RedactedMessage`

Secrets are values *you* know about. PII is often values you *don't* — whatever a user happens to type into a message. `RedactedMessage` handles that case: it asks the runtime to detect PII in the message text and substitute placeholders before the content reaches the model.

```python
from maivn import Agent
from maivn.messages import RedactedMessage

agent = Agent(name='support', api_key='...')

response = agent.invoke([
    RedactedMessage(content='My email is john@example.com and SSN is 123-45-6789')
])
```

What happens:

1. You send a `RedactedMessage`.
2. The runtime detects PII in the content.
3. Detected values are replaced with stable, opaque placeholders before the text is sent onward.
4. The original values are registered into the session's `private_data` — held within the runtime, not surfaced to the model.
5. The model sees only the placeholders.

Because the detected values land in `private_data`, you can inject them into tools the same way as any other private value. To depend on a specific value, give it a predictable key by declaring it in `known_pii_values` with a `name` (see [the next section](#declaring-known-pii-alongside-the-message)) and reference that key:

```python
@agent.toolify(description='Send email to user')
@depends_on_private_data(data_key='user_email', arg_name='email')
def send_email(message: str, email: str) -> dict:
    # 'email' holds the original 'john@example.com' at runtime.
    return {'sent': True, 'to': email}
```

### Declaring Known PII Alongside the Message

You can mix `PrivateData` descriptors and raw strings in `known_pii_values` to declare values you already know are in the message, with custom keys and labels:

```python
from maivn.messages import RedactedMessage, PrivateData

response = agent.invoke([
    RedactedMessage(
        content='Process claim CLM-2026-4401 for Maria Santos, DOB 1985-07-14.',
        known_pii_values=[
            PrivateData(value='Maria Santos', name='patient_name', pii_type='person',
                        label='Patient Name'),
            PrivateData(value='1985-07-14', name='patient_dob', pii_type='date',
                        label='Date of Birth', format='date'),
            PrivateData(value='MEM-882441', name='member_id', label='Member ID'),
            '212-555-0101',  # Raw strings still work.
        ],
    )
])
```

The model then sees opaque placeholders in place of those values — each detected value is swapped for a stable token keyed to its name (for example, the span for `patient_name` becomes a placeholder bound to `user_patient_name`), and the schema gains metadata (names, labels, types) with no values attached.

## Allow-Listing Safe Values

Not every detected span is actually private. A public marketing URL, your organization's own published support email, or an identifier a downstream tool legitimately needs in cleartext should pass through untouched. `PIIWhitelist` lets you approve those spans without weakening protection for everything else.

The whitelist is evaluated *after* detection (so the fact that PII was present is still recorded) but *before* the value is registered into `private_data` (so no placeholder is substituted for the approved span).

```python
from maivn import PIIWhitelist, PIIWhitelistEntry, RedactedMessage

whitelist = PIIWhitelist(
    entries=[
        # Allow every URL in a non-PHI context.
        PIIWhitelistEntry(
            entity_type='url',
            justification='Public marketing URLs are needed for citations.',
        ),
        # Allow one specific known-safe email.
        PIIWhitelistEntry(
            value='support@maivn.io',
            justification='Public support address listed on the docs site.',
        ),
        # Allow a bounded family of values via an anchored pattern (use sparingly).
        PIIWhitelistEntry(
            pattern=r'^https://docs\.maivn\.io/.*',
            justification='Public docs URLs only.',
        ),
    ],
    phi_mode=False,  # set True for any deployment handling PHI
)

response = agent.invoke([
    RedactedMessage(
        content='See https://docs.maivn.io/x and email support@maivn.io',
        pii_whitelist=whitelist,
    ),
])
```

### Entry Shapes

Each `PIIWhitelistEntry` sets **exactly one** matcher, plus a required `justification`:

| Field | Match behavior | Use when |
| --- | --- | --- |
| `entity_type` | Suppresses every detected span of this category | You trust an entire category in a non-PHI context |
| `value` | Case-insensitive exact match against the detected span | One known-safe specific value |
| `pattern` | Anchored regex match against the detected span | A bounded family of values (for example one domain) |

The `justification` field is required (minimum 8 characters) and recorded with each suppression as compliance evidence.

### PHI Mode

When `phi_mode=True`, the whitelist refuses to construct any `entity_type` entry that names a HIPAA Safe Harbor identifier category. This is enforced at construction time, so your application fails loudly rather than shipping a non-compliant policy:

```python
from maivn import PIIWhitelist, PIIWhitelistEntry

# Raises ValueError — `ssn` is a Safe Harbor identifier.
PIIWhitelist(
    entries=[PIIWhitelistEntry(entity_type='ssn', justification='nope')],
    phi_mode=True,
)

# Allowed — value/pattern entries target a single approved instance,
# not a whole category, so they remain valid in PHI mode.
PIIWhitelist(
    entries=[
        PIIWhitelistEntry(
            value='hospital-public@example.org',
            justification='Hospital published support address; legal-approved.',
        ),
    ],
    phi_mode=True,
)
```

`PIIWhitelist` and `PIIWhitelistEntry` are frozen models — immutable after construction. To change a policy, build a new one.

### Where to Set the Whitelist

A whitelist travels on the `pii_whitelist` field of an individual `RedactedMessage`, so you scope it to exactly the messages where the approved spans appear. Reuse the same `PIIWhitelist` object across messages to apply a consistent policy.

```python
# Attach the whitelist to each RedactedMessage that needs it:
RedactedMessage(content='...', pii_whitelist=whitelist)
```

## Supported PII Types and Safe Harbor Categories

Detection targets HIPAA Safe Harbor identifiers plus common PCI, banking, and governmental categories. Each pattern is paired with a structural validator so structurally similar non-PII (order numbers, internal product codes) is far less likely to be flagged.

| Type | Examples | Validation approach |
| --- | --- | --- |
| `email` | `user@example.com` | structural |
| `phone` | `+1-555-123-4567`, `(555) 123-4567` | NANP / E.164 boundaries |
| `ssn` | `123-45-6789` | rejects reserved area codes |
| `credit_card` | `4111-1111-1111-1111` | Luhn (mod-10) checksum |
| `iban` | `DE89370400440532013000` | per-country length + ISO 13616 mod-97 |
| `swift` | `DEUTDEFF`, `DEUTDEFF500` | ISO 3166 country code + length 8 or 11 |
| `medical_record_number` | `MRN: AB-12345` | label-anchored |
| `vehicle_id` | `1HGCM82633A004352` (VIN) | ISO 3779 alphabet + 17 chars |
| `health_plan_id` | `Member ID: HP-994221` | label-anchored |
| `person` | Names | confidence-scored detection |
| `location` | Addresses, cities | confidence-scored detection |
| `date` / `datetime` | `2025-04-29`, `April 29, 2025` | confidence-scored detection |
| `ip_address` | `192.168.1.1` | confidence-scored detection |
| `url` | `https://...` | confidence-scored detection |

The full set of HIPAA Safe Harbor categories is exported as a public constant so you can validate your own policy before constructing a whitelist:

```python
from maivn import HIPAA_SAFE_HARBOR_CATEGORIES

print(sorted(HIPAA_SAFE_HARBOR_CATEGORIES))
# ['account_id', 'biometric_id', 'certificate_id', 'credit_card', 'date',
#  'datetime', 'device_id', 'email', 'fax', 'health_plan_id', 'iban',
#  'ip_address', 'license_id', 'medical_record_number', 'person', 'phone',
#  'ssn', 'swift', 'url', 'vehicle_id']
```

## Previewing Redaction

Before you run a session, you can preview exactly what the runtime will do to a `RedactedMessage` — which placeholders get inserted, which values get added to `private_data`, and which of your declared known values actually matched. Use `preview_redaction()` on any `Agent` or `Swarm`.

```python
from maivn import Agent, RedactedMessage

agent = Agent(name='support', api_key='...')

preview = agent.preview_redaction(
    RedactedMessage(content='Contact me at alice@example.com'),
    known_pii_values=['alice@example.com', 'bob@example.com'],
)

assert len(preview.inserted_keys) == 1  # one email detected and redacted
assert preview.matched_known_pii_values == ['alice@example.com']
```

`preview_redaction()` accepts a `RedactedMessage` plus optional `known_pii_values` and `private_data`, and returns a `RedactionPreviewResponse` with:

| Field | Description |
| --- | --- |
| `message` | The redacted message with placeholders applied |
| `inserted_keys` | Newly inserted placeholder keys |
| `added_private_data` | Only the values added by this preview |
| `merged_private_data` | Existing plus newly added private data |
| `redacted_message_count` | Number of messages redacted |
| `redacted_value_count` | Number of values redacted |
| `matched_known_pii_values` | Declared values that were found |
| `unmatched_known_pii_values` | Declared values that were not found |

If you prefer to build the request explicitly, `RedactionPreviewRequest` is exported from `maivn` for use with `Client.preview_redaction(...)`.

## System Tools Respect the Same Boundary

The built-in system tools (`web_search`, `repl`, `think`) honor the same private-data rules as your own tools:

- **`web_search`** never includes private values in outbound queries, and results are scanned for PII before returning.
- **`repl`** can run with injected private values, and its output is redacted before it returns to the model.
- **`think`** receives only the private-data schema, never the raw values, and its reasoning output is redacted.

In other words, enabling system tools does not open a side channel around the protection model. See the [System Tools guide](system-tools.md) for the full surface.

```python
agent.private_data = {'user_email': 'john@example.com'}

# The agent can search for context without leaking the email
# into the external search query.
response = agent.invoke([
    HumanMessage(content="Search for information about this user's preferences")
])
```

## Best Practices

**Treat private values as write-only inside a tool.** Use them to do work; never return them in a result or write them to a log.

```python
# Avoid:
def my_tool(secret: str) -> dict:
    print(f'Using secret: {secret}')   # leaks to logs
    return {'secret_used': secret}     # leaks back toward the model

# Prefer:
def my_tool(secret: str) -> dict:
    result = call_api(secret)
    return {'status': 'success'}
```

**Use descriptive key names** (`stripe_api_key`, not `key1`) so the schema the model plans against is self-explanatory.

**Include only what's needed.** Every value in `private_data` enlarges the schema the model sees. Don't register secrets a session won't use.

**Turn on PHI mode for any healthcare deployment.** `phi_mode=True` prevents whole-category whitelist entries for Safe Harbor identifiers, so an over-broad policy fails at construction instead of in production.

## How Private Data Compares to Environment Variables

| Aspect | Private data | Environment variables |
| --- | --- | --- |
| Scope | Per-agent / per-swarm | Process-wide |
| Model visibility | Schema only | Not visible |
| Runtime injection | Yes | No (static) |
| Automatic redaction | Yes | No |

Use **private data** when the model needs to know a value *exists*, tools need it injected, or you want automatic redaction. Use **environment variables** for static configuration consumed at SDK initialization where no model awareness is needed.

## Next Steps

- [Dependencies](dependencies.md) — combine `@depends_on_private_data` with tool, agent, and interrupt dependencies.
- [System Tools](system-tools.md) — how `web_search`, `repl`, and `think` honor the privacy boundary.
- [Decorators Reference](../api/decorators.md) — the full `@depends_on_private_data` signature.
- [Messages Reference](../api/messages.md) — `RedactedMessage`, `PrivateData`, and `preview_redaction()`.
- [Private Data Examples](../examples/private-data.md) — runnable placeholder and `RedactedMessage` walkthroughs.
