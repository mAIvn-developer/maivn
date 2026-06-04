# mAIvn SDK Overview

mAIvn lets you build AI agents that reliably use your own code as tools, coordinate as teams, and keep secrets out of the model.

This is the front door to the SDK. It explains what mAIvn is in plain language, the one mental model that makes everything else click, how to install it, and where to go next.

## What mAIvn Is

mAIvn is a Python SDK for building **agents** — programs that can reason about a request and then take action by calling tools.

Three ideas carry the whole SDK:

- **Agents.** An `Agent` is the thing you talk to. You give it a name, a system prompt, and a set of tools, and it decides which tools to call to fulfill a request.
- **Tools are your own functions.** A tool is just a Python function (or a Pydantic model) that you mark with `@agent.toolify()`. The agent can call it, but the code is *yours* — your business logic, your API calls, your database access.
- **Teams of agents.** When one agent isn't enough, a `Swarm` coordinates several specialized agents so they can hand work back and forth.

If you can write a Python function, you can give an agent a new capability.

## Why It Matters

The defining property of mAIvn is **where your code runs**.

When you register a tool, the agent does not receive your code. The hosted orchestrator only ever sees the tool's *schema* — its name, description, and parameter types. The function body stays on your machine and runs in your environment.

> **Note:** Your tool code executes locally in your environment — it is never transferred to or executed on mAIvn servers. Only the tool schema (name, description, parameters) is sent to the server for orchestration.

That single design choice gives you:

- **Private business logic.** Your implementation never leaves your environment.
- **Local data access.** Tools can read your database, hit internal services, or touch the filesystem, and the results stay under your control.
- **Secrets out of the model.** Sensitive values — API keys, passwords, PII — can be declared as *private data* and injected into your tools only at execution time. The model plans against the *names* of those values, never the values themselves, and sensitive data that appears in tool results is redacted before it ever returns to the model. See the [Private Data Guide](guides/private-data.md).

You get the planning ability of a hosted orchestrator without surrendering your code or your secrets to it.

## How It Works at a Glance

The mental model is short: **you describe tools and rules; the hosted orchestrator plans; your code runs locally; a typed response comes back.**

Concretely, every invocation flows through four conceptual stages:

1. **You author scopes.** A *scope* is an `Agent` or a `Swarm`. You declare its tools, the rules between them (dependencies, private data, structured output), and any configuration.
2. **The hosted orchestrator plans.** When you call `invoke()`, the SDK sends your message and the *schemas* of your tools to the orchestrator, which decides what to do and in what order.
3. **Your tools execute locally.** When the plan calls a tool, the SDK runs your function in your own process and sends only the result back. Private values are injected at this moment and redacted from results.
4. **A typed response comes back.** You receive a `SessionResponse`. Read `response.response` for the final assistant text, or `response.result` for structured output.

You stay in control of the code and the data; the orchestrator handles the reasoning about *which* tool to call and *when*.

## Install

```bash
pip install maivn
```

Or with uv:

```bash
uv add maivn
```

mAIvn requires Python 3.10+. It depends on the public `maivn-shared` package and installs it automatically.

## Your First Agent

Five lines is enough to define an agent, give it a tool, and run it:

```python
import os

from maivn import Agent
from maivn.messages import HumanMessage

agent = Agent(name='assistant', api_key=os.environ['MAIVN_API_KEY'])

@agent.toolify(description='Get current weather for a city')
def get_weather(city: str) -> dict:
    return {'city': city, 'temp': 72, 'condition': 'sunny'}

response = agent.invoke([HumanMessage(content='What is the weather in Austin?')])
print(response.response)
```

The `Agent` constructor needs either an `api_key` or a preconfigured `Client`; it does not read environment variables on its own, so read the key explicitly as shown.

For a guided, step-by-step build of this same agent — including structured output and live event streaming — start with the [Getting Started guide](guides/getting-started.md).

## Where to Go Next

Pick the path that matches what you're trying to do:

- **[Getting Started](guides/getting-started.md)** — Build your first agent step by step, add a tool, and inspect execution events.
- **Core Concepts** — Learn the building blocks in depth:
  - [Tools](guides/tools.md) — Define tools as functions or Pydantic models.
  - [Dependencies](guides/dependencies.md) — Chain tools and inject data between them.
  - [Structured Output](guides/structured-output.md) — Guaranteed, typed responses.
  - [Multi-Agent](guides/multi-agent.md) — Coordinate specialized agents with a `Swarm`.
  - [Private Data](guides/private-data.md) — Keep secrets and PII out of the model.
- **Guides** — Task-focused how-tos for [system tools](guides/system-tools.md), [memory and recall](guides/memory-and-recall.md), [scheduled invocation](guides/scheduled-invocation.md), [billing and usage](guides/billing-and-usage.md), and [frontend events](guides/frontend-events.md).
- **[SDK Reference](api/README.md)** — Per-class API documentation for [`Agent`](api/agent.md), [`Swarm`](api/swarm.md), [decorators](api/decorators.md), [configuration](api/configuration.md), and more.
- **[Examples](examples/README.md)** — A working tour of the SDK organized by what you'd want to build.
