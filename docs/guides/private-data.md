# Private Data Guide

Securely handle sensitive data like API keys, passwords, and PII using the private data system.

## Overview

The maivn SDK provides a secure mechanism for handling sensitive data:

1. **Automatic PII detection** - Server detects and redacts PII only for `RedactedMessage` user messages
2. **Private data stays protected by default** - Never sent to the model by default
3. **Schema-only planning** - The model sees field names, not values
4. **Runtime injection** - Values injected only at tool execution
5. **Automatic redaction** - Sensitive data in results is removed

## Basic Usage

### Setting Private Data

```python
from maivn import Agent

agent = Agent(name='api_agent', api_key='...')

# Set private data
agent.private_data = {
    'api_key': 'sk-xxx-secret',
    'db_password': 'super-secret-password',
    'user_ssn': '123-45-6789',
}
```

### Injecting Private Data

Use `@depends_on_private_data` to inject values into tools:

```python
from maivn import depends_on_private_data

@agent.toolify(description='Call external API')
@depends_on_private_data(data_key='api_key', arg_name='key')
def call_api(endpoint: str, key: str) -> dict:
    # 'key' contains 'sk-xxx-secret' at runtime
    return {'status': 'success'}
```

### Multiple Values

```python
@agent.toolify(description='Connect to database')
@depends_on_private_data(data_key='db_host', arg_name='host')
@depends_on_private_data(data_key='db_password', arg_name='password')
def connect_db(query: str, host: str, password: str) -> dict:
    return {'connected': True, 'result': [...]}
```

## Security Model

### What the LLM Sees

The model receives a schema describing available private data:

```
Available private data fields:
- api_key (string): API key for external service
- db_password (string): Database password
```

The model **never** sees the actual values by default.

### Protected Runtime Injection

Private data values are injected within the protected runtime:

1. SDK sends a tool request with private-data placeholders
2. The runtime looks up actual values from `private_data`
3. Values are injected into tool arguments
4. The tool executes with real values
5. Outbound payloads are re-checked and blocked if a known private value still appears

### Automatic Redaction

If a tool returns data containing private values, they are automatically redacted:

```python
@agent.toolify()
@depends_on_private_data(data_key='secret', arg_name='secret')
def leaky_tool(secret: str) -> dict:
    # Bad practice: including secret in output
    return {'message': f'Used secret: {secret}'}

# The server automatically redacts 'secret' value from the result
```

## Automatic PII Detection with RedactedMessage

Use `RedactedMessage` to enable automatic PII detection and redaction. The server detects sensitive data and redacts it before sending to the LLM.

### How It Works

1. **Send a RedactedMessage** with sensitive data
2. **The runtime detects PII** in the message content
3. **The runtime replaces values** with placeholders (e.g., `{_{pii_email_1}_}`)
4. **Original values stored** in session's `private_data` (server-side only)
5. **Known values are matched case-insensitively** across outbound payloads, including user-injected literals
6. **The model sees only placeholders**, never the actual sensitive data by default

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

### Detected PII Types

The detection pipeline targets HIPAA Safe Harbor identifiers plus the
common PCI / banking / governmental categories. Each pattern is paired with
a structural validator so structurally-similar non-PII (order numbers,
internal product codes) is not flagged.

| Type | Examples | Validator |
| --- | --- | --- |
| `email` | `user@example.com` | structural |
| `phone` | `+1-555-123-4567`, `(555) 123-4567` | NANP / E.164 boundaries |
| `ssn` | `123-45-6789`, `123 45 6789`, `123.45.6789` | reject reserved areas (`000`, `666`, `9xx`) |
| `credit_card` | `4111-1111-1111-1111` | Luhn (mod-10) checksum required |
| `iban` | `DE89370400440532013000` | per-country length + ISO 13616 mod-97 |
| `swift` | `DEUTDEFF`, `DEUTDEFF500` | ISO 3166 country code + length 8 or 11 |
| `account_id` | `account id: ABC123` | label-anchored |
| `medical_record_number` | `MRN: AB-12345`, `Medical Record Number 0019283` | label-anchored |
| `vehicle_id` | `1HGCM82633A004352` (VIN) | ISO 3779 alphabet + 17 chars |
| `health_plan_id` | `Member ID: HP-994221`, `Policy Number ...` | label-anchored |
| `person` | Names detected by NLP | per-entity confidence |
| `location` | Addresses, cities | per-entity confidence |
| `date` / `datetime` | `2025-04-29`, `April 29, 2025` | per-entity confidence |
| `ip_address` | `192.168.1.1` | per-entity confidence |
| `url` | `https://...` | per-entity confidence |
| `license_id` | Driver license / professional license numbers | Presidio |
| `passport_id` | US passport numbers | Presidio |
| `bank_account` | US bank account / routing numbers | Presidio |

### Key Naming Convention

Redacted values are stored with keys whose format depends on how the value
was registered:

| How registered | Key shape | Example |
| --- | --- | --- |
| Auto-detected by NLP / regex | `pii_{type}_{counter}` | `pii_email_1`, `pii_ssn_1` |
| Declared via `PrivateData(name=...)` | `user_{sanitized_name}` | `user_patient_name` |

The `user_` prefix on user-supplied names prevents a caller from squatting
on a future auto-allocated `pii_*` slot. The same value appearing multiple
times reuses the same key.

### Accessing Redacted Values in Tools

Use `@depends_on_private_data` to access the redacted values:

```python
from maivn import depends_on_private_data

@agent.toolify(description='Send email to user')
@depends_on_private_data(data_key='pii_email_1', arg_name='email')
def send_email(message: str, email: str) -> dict:
    # 'email' contains the original value 'john@example.com'
    return {'sent': True, 'to': email}
```

## Enhanced Known PII with PrivateData

For maximum control over PII handling, use the `PrivateData` model to declare known PII values with custom key names, types, and metadata. This enriches the `private_data_schema` that the LLM sees, enabling better reasoning about redacted fields.

### PrivateData Model

```python
from maivn import PrivateData

PrivateData(
    value='Maria Santos',       # The actual PII value (required)
    name='patient_name',        # Custom key name (instead of pii_person_1)
    pii_type='person',          # Entity type for categorization
    label='Patient Name',       # Human-readable label in schema
    description='Full name of the patient from the intake form',
    format='name',              # Semantic format hint
)
```

### Using PrivateData with RedactedMessage

Pass `PrivateData` objects alongside raw strings in `known_pii_values`:

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
            '212-555-0101',  # Raw strings still work
        ],
    )
])
```

**What the LLM sees** (message content with placeholders):
```
Process claim CLM-2026-4401 for {_{user_patient_name}_}, DOB {_{user_patient_dob}_}.
```

**What the schema includes** (no values, just metadata):
```json
{
  "user_patient_name": {"data_type": "string", "label": "Patient Name", "pii_type": "person"},
  "user_patient_dob": {"data_type": "string", "label": "Date of Birth", "inferred_format": "date"},
  "user_member_id": {"data_type": "string", "label": "Member ID"}
}
```

> **Note**: User-declared `name` values are prefixed with `user_` server-side
> so they cannot collide with auto-generated `pii_*` slots. Tools that
> consume the value via `@depends_on_private_data(data_key=...)` should use
> the prefixed key (`user_patient_name`).

### Using PrivateData with Scope private_data

You can also pass a list of `PrivateData` objects to the Agent or Swarm `private_data` field:

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

# Equivalent to:
agent.private_data = {
    'claims_hotline': 'claims@hospital.com',
    'medical_review_email': 'reviews@hospital.com',
}
# But with richer schema metadata for the LLM.
```

### Benefits of PrivateData

| Feature | Raw String | PrivateData |
|---------|-----------|-------------|
| Auto-detected key name | `pii_person_1` | `user_patient_name` (custom) |
| Entity type | Inferred from regex | Explicitly declared |
| Schema label | None | `"Patient Name"` |
| Schema description | None | Custom description |
| Format hint | Inferred | Explicitly declared |
| Tool result redaction | Yes | Yes |

## Suppressing Redaction with `PIIWhitelist`

Some categories of detected PII are not actually private in every
deployment — public marketing URLs, an organization's own published
support email, or identifiers that a downstream tool *needs* to receive
in cleartext. The `PIIWhitelist` model lets you mark these spans as safe
without weakening the rest of the pipeline.

The whitelist is evaluated **after** detection (so the audit trail still
records that PII was present) but **before** registration into
`private_data` (so no placeholder substitution happens for the approved
span).

### Example

```python
from maivn import PIIWhitelist, PIIWhitelistEntry, RedactedMessage

whitelist = PIIWhitelist(
    entries=[
        # Allow URLs in non-PHI contexts.
        PIIWhitelistEntry(
            entity_type='url',
            justification='Public marketing URLs are needed for citations.',
        ),
        # Allow a specific known-safe email.
        PIIWhitelistEntry(
            value='support@maivn.io',
            justification='Public support address listed on docs site.',
        ),
        # Allow values matching a narrow regex (use sparingly).
        PIIWhitelistEntry(
            pattern=r'^https://docs\.maivn\.io/.*',
            justification='Public docs URLs only.',
        ),
    ],
    phi_mode=False,  # set True for any deployment that handles PHI
)

response = agent.invoke([
    RedactedMessage(
        content='See https://docs.maivn.io/x and email support@maivn.io',
        pii_whitelist=whitelist,
    ),
])
```

### Entry Shapes

Each `PIIWhitelistEntry` sets **exactly one** of:

| Field | Match Behavior | Use When |
| --- | --- | --- |
| `entity_type` | Suppresses every detected span of this category | You trust an entire category in a non-PHI context |
| `value` | Case-insensitive exact match against the detected span | One known-safe specific value |
| `pattern` | Anchored regex match against the detected span | A bounded family of values (e.g. one domain) |

The `justification` field is **required** (≥8 chars) and recorded in
every audit emission for the suppressed span (SOC-2 / ISO 27001 evidence).

### PHI Mode (`phi_mode=True`)

When `phi_mode=True`, the whitelist refuses to construct any
`entity_type` entry that names a HIPAA Safe Harbor identifier category.
This is enforced at construction time (Pydantic validator) so your
application fails loud rather than shipping a non-compliant policy:

```python
from maivn import PIIWhitelist, PIIWhitelistEntry

# This raises ValueError — `ssn` is a Safe Harbor identifier.
PIIWhitelist(
    entries=[PIIWhitelistEntry(entity_type='ssn', justification='nope')],
    phi_mode=True,
)

# This is fine — value/pattern entries are still permitted in PHI mode
# because they target a single approved instance, not a whole category.
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

The `HIPAA_SAFE_HARBOR_CATEGORIES` constant is exported from `maivn` so
you can validate your own policy ahead of construction:

```python
from maivn import HIPAA_SAFE_HARBOR_CATEGORIES
print(sorted(HIPAA_SAFE_HARBOR_CATEGORIES))
# ['account_id', 'biometric_id', 'certificate_id', 'date', 'datetime',
#  'device_id', 'email', 'fax', 'health_plan_id', 'ip_address',
#  'license_id', 'medical_record_number', 'person', 'phone', 'ssn',
#  'swift', 'url', 'vehicle_id', 'iban', 'credit_card']
```

### Where to Set It

The whitelist transports inside `RedactedMessage` (per-message override)
and at `SessionRequest.pii_whitelist` (whole session). The per-message
override wins when both are set.

```python
# Session-level (applies to every RedactedMessage in the session):
client = Client(api_key='...')
client.session_request_defaults.pii_whitelist = whitelist

# Per-message (applies to just this message):
RedactedMessage(content='...', pii_whitelist=whitelist)
```

### Compliance Posture

| Framework | Behavior |
| --- | --- |
| HIPAA Safe Harbor | `phi_mode=True` refuses entity_type whitelist entries for any of the 18 Safe Harbor categories. Use `value` / `pattern` for specific approved instances. |
| SOC-2 / ISO 27001 | `justification` is required (≥8 chars) and included in every `WHITELIST_SUPPRESSED` audit record. |
| FedRAMP AC-3 | `PIIWhitelist` and `PIIWhitelistEntry` are frozen Pydantic models — immutable post-construction. Replacement is the only change mechanism. |
| Tamper-evidence | Each suppression emits an `ACCESSED` audit record with `action=WHITELIST_SUPPRESSED`, the entity type, the justification, and the span length (no raw value). |

## Placeholder Syntax

Use `{_key_}` syntax in prompts to reference private data:

```python
agent = Agent(
    name='notifier',
    system_prompt='''You are a notification assistant.
    The user's phone number is {_phone_number_}.
    Never reveal the actual phone number in your responses.''',
    api_key='...',
)

agent.private_data = {'phone_number': '+1-555-123-4567'}
```

The placeholder is replaced only in tool execution, not in LLM prompts.

## Use Cases

### API Keys

```python
agent.private_data = {
    'openai_key': 'sk-xxx',
    'stripe_key': 'sk_live_xxx',
    'aws_access_key': 'AKIAIOSFODNN7EXAMPLE',
}

@agent.toolify()
@depends_on_private_data(data_key='stripe_key', arg_name='key')
def create_payment(amount: int, key: str) -> dict:
    # Use Stripe API with the key
    return {'payment_id': 'pay_xxx'}
```

### Database Credentials

```python
agent.private_data = {
    'db_host': 'prod-db.example.com',
    'db_user': 'app_user',
    'db_password': 'super-secret',
}

@agent.toolify()
@depends_on_private_data(data_key='db_host', arg_name='host')
@depends_on_private_data(data_key='db_user', arg_name='user')
@depends_on_private_data(data_key='db_password', arg_name='password')
def query_database(sql: str, host: str, user: str, password: str) -> dict:
    # Connect and query
    return {'rows': [...]}
```

### User PII

```python
agent.private_data = {
    'user_email': 'user@example.com',
    'user_phone': '+1-555-123-4567',
    'user_ssn': '123-45-6789',
}

@agent.toolify()
@depends_on_private_data(data_key='user_email', arg_name='email')
def send_notification(message: str, email: str) -> dict:
    # Send email to user
    return {'sent': True}
```

## System Tools Integration

System tools (web search, REPL, think) automatically respect private data boundaries:

### Web Search Privacy

- Never includes private data in search queries
- Search results are filtered for PII before returning
- No private data sent to external search APIs

```python
# Private data is NOT sent to web search
agent.private_data = {'user_email': 'john@example.com'}

# Agent searches for "user preferences" - email stays private
response = agent.invoke([
    HumanMessage(content='Search for information about this user\'s preferences')
])
```

### REPL Code Execution

- Private data values injected into code sandbox
- Output automatically redacted before returning to agent
- No code or data persistence between executions

```python
@agent.toolify()
@depends_on_private_data(data_key='api_key', arg_name='key')
def test_api_connection(key: str) -> dict:
    # Code executes with injected key
    return {'status': 'connected', 'key_valid': len(key) > 0}

# Output shows: {'status': 'connected', 'key_valid': true}
# Actual key value never appears in agent context
```

### Think Tool Privacy

- Receives only schema information about private data
- Never sees actual private values
- Reasoning outputs automatically redacted

### Automatic Redaction in System Tools

All system tools automatically:

1. Receive private data via server-side injection
2. Execute with real values in isolated environment
3. Return results with sensitive data redacted
4. Maintain audit trail of all private data access

## Best Practices

### 1. Never Log Private Data

```python
# BAD
@agent.toolify()
@depends_on_private_data(data_key='secret', arg_name='secret')
def my_tool(secret: str) -> dict:
    print(f'Using secret: {secret}')  # Never do this!
    return {}

# GOOD
@agent.toolify()
@depends_on_private_data(data_key='secret', arg_name='secret')
def my_tool(secret: str) -> dict:
    # Use the secret without logging it
    result = call_api(secret)
    return {'status': 'success'}
```

### 2. Don't Return Private Data

```python
# BAD
def my_tool(secret: str) -> dict:
    return {'secret_used': secret}  # Leaks the secret!

# GOOD
def my_tool(secret: str) -> dict:
    return {'status': 'success'}  # No sensitive data in output
```

### 3. Use Descriptive Key Names

```python
# GOOD
agent.private_data = {
    'stripe_api_key': '...',
    'sendgrid_api_key': '...',
    'production_db_password': '...',
}

# LESS CLEAR
agent.private_data = {
    'key1': '...',
    'key2': '...',
    'password': '...',
}
```

### 4. Separate Environments

```python
# Development
if env == 'development':
    agent.private_data = {
        'api_key': 'dev-key',
        'db_host': 'localhost',
    }

# Production
else:
    agent.private_data = {
        'api_key': os.environ['PROD_API_KEY'],
        'db_host': os.environ['PROD_DB_HOST'],
    }
```

### 6. System Tools Privacy

When using system tools with private data:

```python
# GOOD: System tools automatically protect privacy
agent = Agent(
    name='research_agent',
    system_prompt='''Use web search for current information.
    Use REPL for calculations.
    Never include private data in external calls.''',
    api_key='...'
)

agent.private_data = {'user_email': 'john@example.com'}

# System tools will:
# - Exclude email from web search queries
# - Redact email from calculation results
# - Never expose email to external services
```

### 7. Minimal Exposure

Only include private data that's actually needed:

```python
# GOOD: Only what's needed
agent.private_data = {
    'api_key': '...',  # Actually used by tools
}

# AVOID: Including everything
agent.private_data = {
    'api_key': '...',
    'unused_secret': '...',  # Not used - don't include
    'another_unused': '...',
}
```

## Comparison with Environment Variables

| Aspect              | Private Data | Environment Variables |
| ------------------- | ------------ | --------------------- |
| Scope               | Per-agent    | Process-wide          |
| LLM Visibility      | Schema only  | Not visible           |
| Runtime Injection   | Yes          | No (static)           |
| Automatic Redaction | Yes          | No                    |

Use private data when:

- The LLM needs to know about the data's existence
- Tools need the values injected
- Automatic redaction is important

Use environment variables when:

- Configuration is static
- Data is used at SDK initialization
- No LLM awareness is needed

## See Also

- [Decorators Reference](../api/decorators.md) - `@depends_on_private_data`
- [Messages Reference](../api/messages.md) - `RedactedMessage`
- [Dependencies Guide](dependencies.md) - Combining with other dependencies
