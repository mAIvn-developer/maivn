# Private Data

The SDK has several layers for keeping sensitive values out of the LLM
context: dependency injection, placeholder replacement on the way back, and
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

## Placeholder replacement in responses

Sometimes you want the agent's user-facing response to reference private
values *symbolically* and have the SDK rehydrate them on the way back.

The model is told it can write `{_{key}_}` and the runtime will fill them
in after the response leaves the orchestrator:

```python
agent = Agent(
    name='Customer Success Assistant',
    system_prompt=(
        'You are a customer success assistant. Use tools to fetch account context, '
        'then write a short, friendly response. Reference customer details using '
        'placeholders like {_{customer_name}_} and {_{account_id}_}.'
    ),
    api_key='...',
)

agent.private_data = {
    'customer_name': 'Acme Co',
    'account_id': 'ACCT-77342',
}

response = agent.invoke([HumanMessage(content=(
    'Run the account_briefing tool, then write a 2-3 sentence welcome note '
    'using {_{customer_name}_} and {_{account_id}_} exactly.'
))])

# The response text now contains "Hi Acme Co — your account ACCT-77342 is …"
print(response)
```

The LLM never sees the real customer name or account ID during reasoning;
they're substituted in only after the response is produced.

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
several other patterns out of the box. Detected values are stored
server-side keyed to the session and substituted back only where it's safe
to do so (e.g. inside the final tool's structured output, not in the
free-form LLM trace).

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

The email and phone returned by `fetch_customer_profile` are detected by
the server, masked when the next LLM turn sees them, but **rehydrated in
the final tool's arguments and the user-facing response** — so the round
trip works end-to-end without leaking raw PII into the model's
intermediate reasoning.

## Known PII values — explicit list

If you have values you know are sensitive (and might not match the
auto-detector's patterns), declare them up front:

```python
response = agent.invoke(
    messages,
    known_pii_values=[
        'INTERNAL-CASE-7782',
        'project-coastal-shield-rev2',
    ],
)
```

These get the same treatment as auto-detected PII — masked in the LLM
context, rehydrated only in safe spots.

## What's next

- **[Private Data guide](../guides/private-data.md)** — the deeper
  treatment, including custom PII patterns and audit log behavior.
- **[Memory](./memory.md)** — combining memory retrieval with private-data
  workflows.
