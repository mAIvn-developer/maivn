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

```python
AIMessage(content: str)
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
    attachments: list[dict[str, Any]] | None = None,
)
```

### Automatic PII Detection

When you send a `RedactedMessage` containing sensitive data, the server automatically:

1. **Detects PII** in the message content
2. **Stores original values** in `private_data` (server-side only)

The LLM only sees the redacted version with placeholders.

### Detected PII Types

| Type          | Examples                            |
| ------------- | ----------------------------------- |
| `email`       | `user@example.com`                  |
| `phone`       | `+1-555-123-4567`, `(555) 123-4567` |
| `ssn`         | `123-45-6789`                       |
| `credit_card` | `4111-1111-1111-1111`               |
| `iban`        | `DE89370400440532013000`            |
| `swift`       | `DEUTDEFF`                          |
| `person`      | Names detected by NLP               |
| `location`    | Addresses, cities detected by NLP   |
| `ip_address`  | `192.168.1.1`                       |
| `account_id`  | `account id: ABC123`                |

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

Use `known_pii_values` to explicitly declare PII values that should be redacted. These values are seeded into `private_data` and redacted from both the prompt and all tool results, even if auto-detection doesn't catch them:

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
