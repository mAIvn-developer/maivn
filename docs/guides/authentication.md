# Authentication & API Keys

Every request the mAIvn SDK makes on your behalf is tied to an API key. The key tells the hosted orchestrator *who you are*, *which project the work belongs to*, and *what that key is allowed to do*. This guide shows how to authenticate with the SDK, how key scopes shape what you can call, what a rejected key looks like in Python, and how to keep keys out of harm's way.

## What This Is and Why It Matters

When you build with mAIvn, your tool code runs locally — but the *planning* happens on a hosted orchestrator. To reach that orchestrator, the SDK needs to prove the request is yours. That proof is an API key: a secret string you create once in the mAIvn Developer Portal and hand to the SDK.

Think of it like the key to a building. The SDK presents it on every request; the server checks that it is valid and that it has permission for what you're asking. A good key gets you in and lets you do your job. A missing, expired, or under-privileged key gets you turned away — and the SDK turns that "no" into a clear Python exception you can catch.

You never have to assemble auth headers or manage tokens by hand. You give the SDK a key, and it does the rest.

## How the SDK Authenticates

There are two equivalent ways to authenticate, and they produce the same result. Pick whichever fits how your application is structured.

### Pass `api_key` directly

The simplest path: hand your key to the `Agent` (or `Swarm`) constructor. The SDK creates and manages an HTTP client for you behind the scenes.

```python
import os

from maivn import Agent
from maivn.messages import HumanMessage

agent = Agent(
    name='assistant',
    api_key=os.environ['MAIVN_API_KEY'],
)

response = agent.invoke([HumanMessage(content='Hello')])
print(response.response)
```

The constructor does **not** read environment variables automatically. Read the key explicitly (as above) or pass a preconfigured `Client`. If you provide neither `api_key` nor `client`, construction fails fast:

```python
from maivn import Agent

# Raises ValueError: 'Agent requires either a Client instance or an api_key.'
agent = Agent(name='assistant')
```

### Pass a preconfigured `Client`

For anything beyond a single agent, build a `Client` once and share it. A `Client` owns the key, the connection pool, and timeout configuration, and multiple agents can reuse it.

```python
from maivn import Agent, Client

client = Client(api_key=os.environ['MAIVN_API_KEY'])

researcher = Agent(name='researcher', client=client)
writer = Agent(name='writer', client=client)

# Both agents authenticate with the same key and share connections.
```

You can also build the client from the environment, which reads `MAIVN_API_KEY` (along with optional timeout and timezone variables) for you:

```python
from maivn import ClientBuilder

client = ClientBuilder.from_environment()  # reads MAIVN_API_KEY
agent = Agent(name='assistant', client=client)
```

> **Note**
> When you pass `api_key` to several agents, the SDK caches and reuses one underlying `Client` per (key, base URL, timeout) combination automatically — so `api_key=...` and an explicit shared `Client` converge on the same connection reuse. See the [Client reference](../api/client.md) for the full constructor.

### What travels on the wire

Under the hood, the SDK sends your key as the `X-API-Key` request header on every call to the orchestrator. You do not set this header yourself; supplying `api_key` is the entire contract. Your *tool code* is never transmitted — only tool schemas and the request payload — so the key authorizes orchestration, not code upload.

## Key Scopes From the SDK Consumer's Point of View

A key is more than an on/off switch. Each key carries a **scope** that bounds what it can do. You choose the scope when you create the key in the portal; the SDK then operates within it. From a consumer's perspective there are three useful tiers.

| Scope | What it unlocks | Typical use |
| --- | --- | --- |
| **Full-access (`all`)** | Everything the SDK exposes, including account-level reads such as billing and usage data. | A trusted backend service that also needs to reconcile usage. |
| **Read-only** | Invocation and read paths, but not account-level or mutating operations reserved for full-access keys. | A service that runs agents but should never touch billing or destructive operations. |
| **Restricted** | A deliberately narrowed subset chosen in the portal. | A key handed to a specific environment, customer integration, or limited workload. |

The practical rule: **match the key to the job.** Running agents, streaming events, and using `private_data` work with a standard key. Reading account-level billing data is the clearest example of an operation that requires the full-access (`all`) tier — `Client.get_usage()` rejects read-only and restricted keys. See [Billing & Usage](../api/billing.md) for that boundary in detail.

> **Tip**
> Scope is enforced by the server, not guessed by the SDK. If you're unsure whether a key can perform an operation, the safest test is to call it and handle the rejection (next section) — but the cleaner habit is to provision the right scope up front and keep agent-running keys least-privileged.

### Don't confuse key scope with tool permissions

There is a *separate*, finer-grained concept inside the SDK: the `@toolify(permissions=...)` declaration, which lets a *tool* advertise the categories of work it performs (`read`, `write`, `delete`, and so on) so your host can enforce least-privilege at the tool layer. That is about what an individual tool is allowed to do once a request is already authenticated — it is not the same thing as the API key's account scope. See [Decorators](../api/decorators.md) for `PermissionFlag` / `PermissionSet`.

## What Happens on a Rejected or Insufficient Key

Authentication failures surface as Python exceptions, not silent empty responses. Two distinct cases matter.

**No key configured, or the server rejects the key (401/403).** When you invoke an agent without a usable key, or the server rejects the credential, the SDK raises an authentication error. You don't need to import a special class to handle it — catch the public base exception `MaivnError` (importable from `maivn_shared`), which every SDK error subclasses.

```python
from maivn import Agent
from maivn.messages import HumanMessage
from maivn_shared import MaivnError

agent = Agent(name='assistant', api_key='definitely-not-valid')

try:
    response = agent.invoke([HumanMessage(content='Hello')])
except MaivnError as exc:
    # Covers authentication rejection (401/403) and any other SDK error.
    print('Request failed:', exc)
    print(exc.to_dict())  # structured payload for logs / API responses
```

- A **401** means the key is missing or invalid — there is nothing to retry until you supply a valid key.
- A **403** means the key is valid but *not permitted* for what you asked. The most common cause is calling an account-level operation (such as billing) with a key that is not full-access. Don't retry; provision or swap in a key with the right scope.

`MaivnError` carries structured fields you can log or surface: `message`, `error_code`, a `context` dict, and the underlying `cause`. Two helpers are useful here:

```python
try:
    ...
except MaivnError as exc:
    payload = exc.to_dict()                       # structured dict for logging
    enriched = exc.with_context(request_id='abc') # returns a new error with merged context
```

> **Note**
> The SDK does define a dedicated `ServerAuthenticationError` internally, but it is **not** part of the public importable surface. Catch authentication failures via the `MaivnError` base, which is the supported public contract.

**A non-auth request rejection (other 4xx).** If the server accepts your key but rejects the *request* for a domain reason short of authentication — for example an invalid option combination it validates server-side — the SDK raises a plain Python `RuntimeError` whose message includes the server's human-readable reason. Catch it to surface that reason to the caller:

```python
try:
    response = agent.invoke([HumanMessage(content='...')])
except RuntimeError as exc:
    # Message form: 'the mAIvn service rejected the request (<status>): <detail>'
    print('Rejected:', exc)
except MaivnError as exc:
    print('SDK error:', exc)
```

Catch `RuntimeError` for request-level rejections and `MaivnError` for the broad SDK-error case. Neither auth rejections nor validation rejections should be blindly retried — fix the credential or the request first.

For the complete catalog of what the SDK raises across validation, network, and tool-execution phases — not just the auth slice covered here — see the [Errors & Exceptions reference](../api/errors.md).

## Where Keys Come From and Rotating Them

API keys are created, listed, and revoked in the mAIvn Developer Portal, scoped to a project. The portal page is the canonical reference for that workflow — see [API Keys and Webhooks](api-keys-webhooks.md) for the create/copy/store/revoke mechanics and screenshots. This section covers only what matters once the key is in the SDK's hands.

Because the key is bound to a project (and that project to an organization), account-level reads like usage are always scoped to *your* organization — a key cannot read another organization's data.

**Rotating without downtime** is straightforward because authentication is just a string the SDK reads at startup. Create the new key, deploy it to your secret store, restart or re-instantiate your clients so they pick up the new value, confirm traffic is flowing, then revoke the old key. Since a `Client` reads the key when it is constructed, the cleanest rotation is to build a fresh `Client` (or restart the process) with the new key rather than mutating a live one.

## Security Best Practices

**Read keys from the environment; never hard-code them.** A literal key in source ends up in version control, logs, and screenshots. Read it at startup instead.

```python
import os

from maivn import Client

client = Client(api_key=os.environ['MAIVN_API_KEY'])
```

**Never ship keys to the frontend.** API keys are server-to-server credentials. A browser bundle is public; a key placed there is a leaked key. Keep agent invocation on your backend and expose only the results (or a streamed event feed) to clients. When you stream progress to a browser, mount the events adapter on *your own* application — `mount_events(app)` wires up `GET /maivn/events/{session_id}` on your server — so the browser talks to your route, never to the orchestrator with your key. See [Frontend Events](frontend-events.md).

**Use `private_data` for in-tool secrets — not the API key.** Your mAIvn API key authenticates the SDK; it is *not* the place to stash the third-party credentials your tools need (a payment provider key, a database password, a customer's PII). Those belong in `private_data`, which keeps the real values out of the model's prompt and injects them into your tool only at execution time.

```python
from maivn import Agent, depends_on_private_data

agent = Agent(name='billing_agent', api_key=os.environ['MAIVN_API_KEY'])
agent.private_data = {'stripe_api_key': os.environ['STRIPE_API_KEY']}

@agent.toolify(description='Charge a customer')
@depends_on_private_data(data_key='stripe_api_key', arg_name='key')
def charge(amount: int, key: str) -> dict:
    # 'key' holds the real secret at runtime; the model never sees it.
    return {'charged': amount}
```

The model plans against the *name* `stripe_api_key`, never its value, and the runtime redacts known private values from tool results before they return to the model. See the [Private Data Guide](private-data.md) for the full model.

**Scope keys to least privilege, and rotate on a schedule and on incident.** These two habits — covered in [Key scopes](#key-scopes-from-the-sdk-consumers-point-of-view) and [Rotating them](#where-keys-come-from-and-rotating-them) above — are the highest-leverage hygiene for SDK credentials. The portal's [security checklist](api-keys-webhooks.md#security-checklist) restates them alongside the webhook-side rules.

## Next Steps

- [Overview](../overview.md) — what mAIvn is and the one mental model behind it.
- [Core Concepts](../core-concepts.md) — the vocabulary (scopes, tools, responses) the guides build on.
- [Quickstart](getting-started.md) — set a key and run your first agent end to end.
- [Client](../api/client.md) — the `Client` and `ClientBuilder.from_environment()` reference.
- [API Keys and Webhooks](api-keys-webhooks.md) — create, list, and revoke keys (and webhooks) in the portal.
- [Portal Authentication](portal-authentication.md) — signing in to the portal itself: login, sessions, and password reset.
- [Errors & Exceptions](../api/errors.md) — the full SDK error catalog beyond the auth-specific cases here.
- [Billing & Usage](../api/billing.md) — the full-access (`all`) scope boundary, with `Client.get_usage()`.
- [Private Data and PII Protection](private-data.md) — keep in-tool secrets and user PII out of the model.
- [Frontend Events](frontend-events.md) — stream progress to a browser through your own route, not the orchestrator.
