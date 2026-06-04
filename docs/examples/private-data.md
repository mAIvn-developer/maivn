# Private Data

Runnable examples for keeping sensitive values out of the LLM context. The SDK
offers several layers: dependency injection, referencing private values by name
and letting the runtime substitute the real value at execution time, and
automatic detection of PII that slips out of upstream tools.

## `@depends_on_private_data` — inject without disclosure

The LLM sees a schema that says "this argument is required" but never the
actual value. The runtime substitutes the real value just before calling
the tool:

```python
from maivn import Agent, depends_on_private_data
from maivn.messages import HumanMessage

agent = Agent(name='Account Briefing Agent', system_prompt='...', api_key='...')

@depends_on_private_data(arg_name='account_id', data_key='account_id')
@depends_on_private_data(arg_name='customer_name', data_key='customer_name')
@agent.toolify(name='account_briefing', final_tool=True)
def account_briefing(account_id: str, customer_name: str) -> dict:
    return {
        'account_id': account_id,
        'customer_name': customer_name,
        'plan': 'pro',
        'status': 'active',
    }

agent.private_data = {
    'account_id': 'ACCT-91024',
    'customer_name': 'Northwind Medical',
}

agent.invoke([HumanMessage(content='Pull the account briefing.')])
```

Multiple keys can be injected into the same tool. The values are never
serialized into the model's view of the conversation.

## Referencing private values by name in responses

Sometimes you want the agent's user-facing response to reference private
values *symbolically* and have the runtime substitute the real value on the
way back. You declare a private value under a key, instruct the model to
reference that key by name, and the runtime fills in the real value after the
response leaves the model — the model itself never sees it.

```python
agent = Agent(
    name='Customer Success Assistant',
    system_prompt=(
        'You are a customer success assistant. Use tools to fetch account context, '
        'then write a short, friendly response. Reference customer details by their '
        'private-data keys (customer_name, account_id) rather than literal values.'
    ),
    api_key='...',
)

agent.private_data = {
    'customer_name': 'Acme Co',
    'account_id': 'ACCT-77342',
}

response = agent.invoke([HumanMessage(content=(
    'Run the account_briefing tool, then write a 2-3 sentence welcome note '
    'that references the customer_name and account_id private-data keys.'
))])

# The response text now contains "Hi Acme Co — your account ACCT-77342 is …"
print(response)
```

The LLM never sees the real customer name or account ID during reasoning;
they're substituted in only after the response is produced. For the inverse
direction — getting a private value *into* a tool argument — prefer the
`@depends_on_private_data` injection path shown above, which needs no special
referencing convention.

## `RedactedMessage` — redact incoming messages

When the *user's* message contains sensitive content that you don't want
the LLM to see, wrap it in a `RedactedMessage`. The SDK detects PII
patterns, replaces them with placeholders before sending, and reinjects
the real values when needed (e.g. in final-tool arguments):

```python
from maivn.messages import RedactedMessage

response = agent.invoke(
    [RedactedMessage(content=(
        'Please look up case CASE-2026-7781 for dana.lee@example.com '
        '(phone +1-415-555-0199) and acknowledge receipt.'
    ))],
)
```

PII detection covers email, phone, SSN, credit cards, IP addresses, and
several other categories out of the box, each paired with a structural check
to reduce false positives. Detected values are held within the runtime and
substituted back only where it's safe to do so (e.g. inside the final tool's
structured output, not in the free-form LLM trace). Detection is a safety net,
not a guarantee — pair it with `@depends_on_private_data` and `known_pii_values`
for values you already know are sensitive.

## Tool-result PII protection

What if a tool returns PII that wasn't in the original message? The SDK
scans tool results too — anything matching a known PII pattern is
redacted before reaching the LLM's next turn:

```python
from maivn.messages import RedactedMessage

@agent.toolify(name='fetch_customer_profile')
def fetch_customer_profile(case_id: str) -> dict:
    # Simulated DB row — these values were NOT in the user message
    return {
        'case_id': case_id,
        'customer_name': 'Dana Lee',
        'email': 'dana.lee@example.com',
        'phone': '+1 (415) 555-0199',
        'ssn': '321-54-9876',
    }

@agent.toolify(name='confirm_contact', final_tool=True)
def confirm_contact(case_id: str, confirmation_note: str) -> dict:
    return {'case_id': case_id, 'confirmation_note': confirmation_note, 'status': 'ok'}

response = agent.invoke([RedactedMessage(content=(
    'Look up case CASE-2026-7781 and acknowledge receipt. In the '
    'confirmation_note, include the email and phone from the lookup.'
))])
```

The email and phone returned by `fetch_customer_profile` are detected by the
runtime, masked when the next LLM turn sees them, but **substituted back into
the final tool's arguments and the user-facing response** — so the round trip
works end-to-end without leaking raw PII into the model's intermediate
reasoning.

## Known PII values — explicit list

If you have values you know are sensitive (and might not match the
auto-detector's categories), declare them up front on the `RedactedMessage`
via `known_pii_values`:

```python
from maivn.messages import RedactedMessage

response = agent.invoke([
    RedactedMessage(
        content='Acknowledge receipt and reference the internal case.',
        known_pii_values=[
            'INTERNAL-CASE-7782',
            'project-coastal-shield-rev2',
        ],
    ),
])
```

`known_pii_values` accepts raw strings as well as `PrivateData` descriptors.
These get the same treatment as auto-detected PII — masked in the LLM
context, substituted back only in safe spots.

## What's next

- **[Private Data guide](../guides/private-data.md)** — the deeper
  treatment, including the whitelist model and how access to private values is
  tracked.
- **[Memory](./memory.md)** — combining memory retrieval with private-data
  workflows.
