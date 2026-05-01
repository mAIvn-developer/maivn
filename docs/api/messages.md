# Messages

Message types for communicating with agents.

## Import

```python
from maivn.messages import (
    HumanMessage,
    AIMessage,
    SystemMessage,
    RedactedMessage,
    BaseMessage,
    PrivateData,
)
from maivn import (
    PIIWhitelist,
    PIIWhitelistEntry,
    HIPAA_SAFE_HARBOR_CATEGORIES,
)
```

## HumanMessage

Represents user input to the agent.

```python
HumanMessage(
    content: str,
    attachments: list[dict[str, Any]] | None = None,
)
```

### Example

```python
from maivn.messages import HumanMessage

message = HumanMessage(content='What is the weather in Austin?')
response = agent.invoke([message])
```

### With Attachments

```python
from maivn.messages import HumanMessage

message = HumanMessage(
    content='Use the attached runbook.',
    attachments=[
        {
            'name': 'runbook.txt',
            'mime_type': 'text/plain',
            'text_content': 'Rollback if canary checks fail.',
            'sharing_scope': 'project',
            'tags': ['ops', 'runbook'],
        }
    ],
)
```

Supported attachment content inputs:

- `content_base64`
- `content_bytes`
- `text_content`
- `file`

Attachments are normalized into `additional_kwargs.attachments`.

### Multiple Messages

```python
messages = [
    HumanMessage(content='Hello'),
    HumanMessage(content='Can you help me?'),
]
response = agent.invoke(messages)
```

## AIMessage

Represents assistant responses. Typically returned by the agent, not created manually.
`AIMessage` is re-exported from `langchain_core.messages` and accepts the full set of
keyword arguments documented there (e.g., `tool_calls`, `additional_kwargs`,
`response_metadata`). The minimal shape used in most app code is:

```python
AIMessage(content: str, **kwargs)
```

### Example

```python
from maivn.messages import AIMessage

# Usually from response, but can be constructed
ai_message = AIMessage(content='I can help you with that.')
```

## SystemMessage

System prompt message that sets agent behavior. Can be provided to the agent constructor or included in messages.

```python
SystemMessage(content: str)
```

### Example

```python
from maivn.messages import SystemMessage, HumanMessage

# Option 1: In agent constructor (preferred)
agent = Agent(
    name='helper',
    system_prompt='You are a helpful assistant.',
    api_key='...',
)

# Option 2: In messages (explicit)
messages = [
    SystemMessage(content='You are a helpful assistant.'),
    HumanMessage(content='Hello'),
]
response = agent.invoke(messages)
```

### Automatic Injection

If you provide `system_prompt` to the Agent constructor and your messages don't include a `SystemMessage`, one is automatically injected.

## PrivateData

Structured descriptor for known PII values. Provides custom naming, typing, and metadata that enriches the `private_data_schema` visible to the LLM.

```python
PrivateData(
    value: str,                    # Required: the actual PII value
    name: str | None = None,       # Custom placeholder key name
    pii_type: str | None = None,   # Entity type: 'person', 'phone', 'email', 'ssn', 'date', etc.
    label: str | None = None,      # Human-readable label for the schema
    description: str | None = None, # Description for LLM context
    format: str | None = None,     # Semantic format: 'email', 'phone', 'date', 'ssn', etc.
)
```

### Import

```python
from maivn import PrivateData
# or
from maivn.messages import PrivateData
```

### Example

```python
from maivn.messages import RedactedMessage, PrivateData

message = RedactedMessage(
    content='Process claim for Maria Santos, DOB 1985-07-14.',
    known_pii_values=[
        PrivateData(value='Maria Santos', name='patient_name', pii_type='person',
                    label='Patient Name'),
        PrivateData(value='1985-07-14', name='patient_dob', pii_type='date',
                    label='Date of Birth', format='date'),
        '212-555-0101',  # Raw strings still work
    ],
)
```

When `name` is provided, the private_data key uses your custom name (e.g., `patient_name`) instead of auto-generated keys like `pii_person_1`. The `label`, `description`, and `format` fields are included in the `private_data_schema` the LLM sees, giving it richer context about each field.

### Using PrivateData with Scope private_data

You can also pass a list of `PrivateData` objects to the Agent or Swarm `private_data` field:

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

This is equivalent to `private_data={'patient_name': 'Maria Santos', 'member_id': 'MEM-882441'}` but with richer schema metadata.

## RedactedMessage

Message type for handling sensitive data with automatic PII detection. When you use `RedactedMessage`, the server automatically detects and redacts PII before sending to the LLM.

```python
RedactedMessage(
    content: str,
    known_pii_values: list[str | PrivateData] | None = None,
    pii_whitelist: PIIWhitelist | None = None,
    attachments: list[dict[str, Any]] | None = None,
)
```

The optional `pii_whitelist` field carries a `PIIWhitelist` describing
entity categories, literal values, or regex patterns whose detected spans
should be left in cleartext (audited end-to-end). See
[PIIWhitelist](#piiwhitelist) below or the
[Private Data Guide](../guides/private-data.md#suppressing-redaction-with-piiwhitelist)
for usage and HIPAA `phi_mode` semantics.

### Automatic PII Detection

When you send a `RedactedMessage` containing sensitive data, the runtime automatically:

1. **Detects PII** in the message content
2. **Stores original values** in `private_data` (server-side only)
3. **Replaces raw values with placeholders** before any LLM-visible context is built
4. **Re-checks outbound payloads** before runtime handoff, blocking known-value leaks

Model-visible runtimes only see the redacted version with placeholders unless the user has explicitly authorized a supported system-tool flow.

### Detected PII Types

The detection pipeline targets HIPAA Safe Harbor identifiers plus the
common PCI / banking / governmental categories. Each pattern is paired
with a structural validator so structurally-similar non-PII (order
numbers, internal product codes) is not flagged.

| Type | Examples | Validator |
| --- | --- | --- |
| `email` | `user@example.com` | structural |
| `phone` | `+1-555-123-4567`, `(555) 123-4567` | NANP / E.164 boundaries |
| `ssn` | `123-45-6789`, `123 45 6789`, `123.45.6789` | reject reserved areas (`000`, `666`, `9xx`) |
| `credit_card` | `4111-1111-1111-1111` | Luhn (mod-10) checksum |
| `iban` | `DE89370400440532013000` | per-country length + ISO 13616 mod-97 |
| `swift` | `DEUTDEFF`, `DEUTDEFF500` | ISO 3166 country code + length 8 / 11 |
| `account_id` | `account id: ABC123` | label-anchored |
| `medical_record_number` | `MRN: AB-12345` | label-anchored |
| `vehicle_id` | `1HGCM82633A004352` (VIN) | ISO 3779 alphabet, 17 chars |
| `health_plan_id` | `Member ID: HP-994221` | label-anchored |
| `person` | Names detected by NLP | per-entity confidence |
| `location` | Addresses, cities | per-entity confidence |
| `date` / `datetime` | `2025-04-29` | per-entity confidence |
| `ip_address` | `192.168.1.1` | per-entity confidence |
| `url` | `https://...` | per-entity confidence |
| `license_id` | Driver / professional license | Presidio |
| `passport_id` | US passport numbers | Presidio |
| `bank_account` | US bank account / routing numbers | Presidio |

### Example

```python
from maivn import Agent
from maivn.messages import RedactedMessage

agent = Agent(name='support', api_key='...')

# Use RedactedMessage for automatic PII detection
response = agent.invoke([
    RedactedMessage(content='My email is john@example.com and SSN is 123-45-6789')
])
```

`RedactedMessage` supports the same attachment payload structure as `HumanMessage`.

### Known PII Values

Use `known_pii_values` to explicitly declare PII values that should be redacted. These values are seeded into `private_data` and redacted from both the prompt and all tool results, even if auto-detection doesn't catch them. Matching against known values is case-insensitive, so user-injected casing variants are still scrubbed before they reach model-visible runtimes:

```python
from maivn.messages import RedactedMessage, PrivateData

# Raw strings (auto-detected type and key name)
message = RedactedMessage(
    content='Call 212-555-0101 for updates.',
    known_pii_values=['212-555-0101', '212-555-1234'],
)

# PrivateData objects (custom key name, type, and schema metadata)
message = RedactedMessage(
    content='Process claim for Maria Santos.',
    known_pii_values=[
        PrivateData(value='Maria Santos', name='patient_name', pii_type='person',
                    label='Patient Name'),
        PrivateData(value='MEM-882441', name='member_id', label='Member ID'),
    ],
)
```

### Preview Before Invoke

Use `preview_redaction()` when you need to inspect the exact placeholder keys and private-data changes before running a session:

```python
from maivn import Agent
from maivn.messages import RedactedMessage, PrivateData

agent = Agent(name='support', api_key='...')

preview = agent.preview_redaction(
    RedactedMessage(content='Patient: Maria Santos, DOB: 1985-07-14'),
    known_pii_values=[
        PrivateData(value='Maria Santos', name='patient_name', pii_type='person',
                    label='Patient Name'),
        PrivateData(value='1985-07-14', name='patient_dob', pii_type='date',
                    label='Date of Birth'),
    ],
)

assert 'patient_name' in preview.inserted_keys
assert preview.added_private_data['patient_name'] == 'Maria Santos'
```

When you use `events().invoke(...)` or `events().stream(...)`, the SDK can also surface redaction enrichment phases such as `redaction_previewed` and `message_redaction_applied` with structured `redaction` payload details.

### How Values Are Stored

Redacted values are automatically added to the session's `private_data`:

- **Auto-detected PII**: Key format `pii_{type}_{counter}` (e.g., `pii_email_1`, `pii_phone_2`)
- **PrivateData with name**: Uses the custom name as the key (e.g., `patient_name`, `member_id`)
- Values are stored server-side only
- Same value appearing multiple times uses the same key
- Values can be injected into tools using `@depends_on_private_data`

### Accessing Redacted Values in Tools

```python
from maivn import depends_on_private_data

@agent.toolify(description='Send email to user')
@depends_on_private_data(data_key='pii_email_1', arg_name='email')
def send_email(message: str, email: str) -> dict:
    # 'email' contains the original value 'john@example.com'
    return {'sent': True, 'to': email}
```

See [Private Data Guide](../guides/private-data.md) for more details on the security model.

## PIIWhitelist

Configuration model for suppressing redaction of approved PII spans. The
whitelist is evaluated **after** detection (so the audit trail still
records that PII was present) but **before** registration into
`private_data`, leaving the matched span in cleartext.

```python
from maivn import PIIWhitelist, PIIWhitelistEntry

PIIWhitelist(
    entries: list[PIIWhitelistEntry] = [],
    phi_mode: bool = False,
)

PIIWhitelistEntry(
    entity_type: str | None = None,    # one-of
    pattern: str | None = None,         # one-of
    value: str | None = None,           # one-of
    justification: str = ...,           # required, >= 8 chars
    label: str | None = None,
)
```

### Compliance Knobs

- `phi_mode=True` refuses entity_type whitelist entries for any HIPAA
  Safe Harbor identifier category (raises `ValueError` at construction).
  Use `value` / `pattern` entries for individual approved instances.
- `justification` is required (≥8 chars) and recorded in every
  `WHITELIST_SUPPRESSED` audit emission (SOC-2 / ISO 27001 evidence).
- Both `PIIWhitelist` and `PIIWhitelistEntry` are frozen Pydantic
  models — immutable post-construction.

### HIPAA_SAFE_HARBOR_CATEGORIES

```python
from maivn import HIPAA_SAFE_HARBOR_CATEGORIES
```

Frozenset of canonical entity-type names blocked by `phi_mode=True`.
Use it to validate your own policy before constructing a `PIIWhitelist`.

### Example

```python
from maivn import PIIWhitelist, PIIWhitelistEntry, RedactedMessage

whitelist = PIIWhitelist(
    entries=[
        PIIWhitelistEntry(
            entity_type='url',
            justification='Public marketing URLs needed for citations.',
        ),
        PIIWhitelistEntry(
            value='support@maivn.io',
            justification='Public support address listed on docs site.',
        ),
    ],
)

message = RedactedMessage(
    content='See https://maivn.io and email support@maivn.io',
    pii_whitelist=whitelist,
)
```

See [Private Data Guide § Suppressing Redaction with PIIWhitelist](../guides/private-data.md#suppressing-redaction-with-piiwhitelist)
for full usage and compliance posture.

## BaseMessage

Abstract base class for all message types. Useful for type hints.

```python
from maivn.messages import BaseMessage

def process_messages(messages: list[BaseMessage]) -> None:
    for msg in messages:
        print(type(msg).__name__, msg.content)
```

## Message Patterns

### Simple Invocation

```python
response = agent.invoke([HumanMessage(content='Hello')])
```

### Multi-Turn Conversation

```python
# First turn
response1 = agent.invoke(
    [HumanMessage(content='My name is Alice')],
    thread_id='conv-123',
)

# Second turn (same thread)
response2 = agent.invoke(
    [HumanMessage(content='What is my name?')],
    thread_id='conv-123',
)
```

### With Explicit System Message

```python
messages = [
    SystemMessage(content='You are a Python expert. Be concise.'),
    HumanMessage(content='How do I read a file?'),
]
response = agent.invoke(messages)
```

## See Also

- [Agent](agent.md) - `invoke()` method
- [Getting Started Guide](../guides/getting-started.md) - Usage examples
