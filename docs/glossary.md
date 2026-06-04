# Glossary

A plain-language A-Z of the core mAIvn concepts, with one-sentence definitions and a link to the canonical page for each. Use it to get your bearings, or to settle the terms that are easy to confuse — session vs. thread, tool vs. toolset, agent vs. swarm, enrichment event vs. status message, final tool vs. structured output.

Definitions only. For runnable code and full detail, follow the links.

## How to read this page

Each entry answers two questions: *what is it* in everyday language, and *where do I go next* for the developer detail. Terms are grouped by topic rather than strictly alphabetized so that closely related ideas sit next to each other.

When two terms are commonly mixed up, they are defined together with a short note on the difference.

## Agent

An **Agent** is the main thing you build with mAIvn — a named unit that holds a system prompt, a set of tools, and credentials, and that you send messages to in order to get work done.

You create one with `Agent(...)` and run it with `agent.invoke(...)`. It is the primary entry point for most applications.

See [Getting Started](guides/getting-started.md) and the [Agent reference](api/agent.md).

## Swarm

A **Swarm** is a container that coordinates several Agents on one request, so a complex job can be split across specialists (for example, a researcher agent and a writer agent) instead of one agent doing everything.

The first agent in a swarm is the entry point, and agents can declare that they depend on one another's output.

> **Agent vs. Swarm:** an Agent is a single worker; a Swarm is a team of Agents working on the same request. A single Agent has no Swarm; a Swarm always contains one or more Agents.

See the [Multi-Agent Guide](guides/multi-agent.md) and the [Swarm reference](api/swarm.md).

## Scope (BaseScope)

A **Scope** is the shared foundation that both Agent and Swarm are built on — it is where the common abilities live, such as registering tools, requesting structured output, subscribing to events, and scheduling runs.

In code this is the `BaseScope` class. You rarely create one directly; you work with an Agent or a Swarm, and they inherit everything a Scope provides.

See the [BaseScope reference](api/agent.md#basescope).

## Tool / Toolset

A **Tool** is a single capability you give an agent — usually one of your own Python functions, or a Pydantic model, that the agent can choose to call to fetch data or take an action.

A **Toolset** is a convenience for registering many related tools at once: you mark up a class, and every marked method on an instance of it becomes a tool, sharing a common name prefix.

> **Tool vs. Toolset:** a Tool is one capability; a Toolset is a bundle of related tools registered together from a single object. Registering a toolset simply produces a group of individual tools.

> **Note:** Your tool code runs in your own environment. Only the tool's schema — its name, description, and parameters — is shared for orchestration; the function body is never transferred elsewhere.

See the [Tools Guide](guides/tools.md).

## Dependency / DAG execution

A **Dependency** is a declared relationship that says one tool or agent needs the output of another before it can run, so you can describe a workflow ("analyze the data that fetch produced") instead of manually sequencing calls.

Because each dependency points from a consumer to its source, the declared relationships form a **DAG** (directed acyclic graph): the runtime resolves that graph, runs independent steps where it can, and feeds each step its inputs in the right order. mAIvn supports tool dependencies, agent dependencies, private-data injection, and human-input (interrupt) dependencies, plus ordering controls that gate one step behind another without passing data.

See the [Dependencies Guide](guides/dependencies.md).

## Session / Thread

A **Session** is a single run of an agent or swarm — one `invoke()` (or `stream()`) call, from the incoming messages through to the final response.

A **Thread** is the longer-lived conversation those sessions belong to: pass the same `thread_id` across multiple calls and the agent treats them as a continuous exchange, with earlier turns available as context.

> **Session vs. Thread:** a Session is one turn (one invocation); a Thread is the ongoing conversation that strings many sessions together. One thread contains many sessions.

See [Getting Started](guides/getting-started.md) and the [Memory and Recall Guide](guides/memory-and-recall.md).

## Final tool / Structured output

**Structured output** means getting back a typed, schema-validated object (a Pydantic model) instead of free-form text — useful when another part of your program needs to consume the result reliably.

A **final tool** is one way to produce that structured output: you mark a tool as the final one, and its arguments become the shape of the response. The `.structured_output(Model)` builder is the fast path to the same goal when you just want typed output without orchestrating other tools.

> **Final tool vs. structured output:** *structured output* is the outcome (a typed result on `response.result`); a *final tool* is one mechanism for producing it within a full agent run. Both deliver a validated object rather than plain prose.

See the [Structured Output Guide](guides/structured-output.md).

## Private data / PII / Redaction / Whitelist

**Private data** is sensitive information — API keys, passwords, and similar secrets — that you attach to an agent so it can be used by your tools without ever being exposed to the language model in cleartext. The model plans against the *names and types* of these values; the real values are supplied only when a tool actually executes.

**PII** (personally identifiable information) is sensitive data found in ordinary message text, such as an email address, phone number, or government ID.

**Redaction** is the automatic process of replacing detected private values or PII with placeholders before text is sent onward, so the underlying sensitive data does not leak through.

A **whitelist** is an explicit, justified exception that marks specific values or categories as safe to pass through, suppressing redaction for those approved items only.

> **How it works, conceptually:** the model sees a schema (key names, types, labels) rather than raw secrets; real values are injected at the moment a tool runs; and outputs are scanned so known sensitive values don't slip back into the model's view. You stay in control of what is treated as private and what is explicitly allowed through.

See the [Private Data Guide](guides/private-data.md).

## Enrichment event / Status message

An **enrichment event** is a real-time, fixed-label phase indicator streamed during a turn — short signals like "Planning actions..." or "Synthesizing response..." that tell a user interface what stage the work is in between tool calls and the final answer.

A **status message** is a separate, opt-in channel of free-form, dynamic text describing what's happening, enabled with `stream(status_messages=True)`.

> **Enrichment event vs. status message:** enrichment events are a fixed set of phase labels emitted automatically; status messages are free-form dynamic lines you opt into. Enrichment events tell you *which phase*; status messages can carry *arbitrary detail*. They travel as distinct kinds of stream events.

See the [Frontend Events Guide](guides/frontend-events.md) and the [Events reference](api/events.md).

## Token usage

**Token usage** is the count of tokens consumed by the language-model calls in a run — input, output, and totals — normalized into a single shape across providers and aggregated for the whole session.

It is exposed on responses as a `TokenUsage` model and is purely informational: reading it never changes how an agent runs.

See the [Billing and Usage Guide](guides/billing-and-usage.md).

## MCP server

An **MCP server** (Model Context Protocol server) is an external tool provider you can connect to an agent, so tools hosted outside your own code — over stdio or HTTP — become available to the agent alongside your local tools.

You register one with the `MCPServer` configuration and the scope's MCP registration methods.

See the [MCP reference](api/mcp.md).

## Scheduled job (cron / every / at)

A **scheduled job** is an agent or swarm invocation set to run automatically on a cadence rather than on demand, returning a handle you can use to inspect or stop it.

Three builders cover the common cases: `cron(...)` for crontab-style schedules, `every(...)` for a fixed interval, and `at(...)` for a single run at a specific time. Each supports optional bounded jitter, retry/backoff, and overlap policies.

See the [Scheduled Invocation Guide](guides/scheduled-invocation.md) and the [Scheduling reference](api/scheduling.md).

## Interrupt

An **interrupt** is a pause-for-human-input step in the middle of a run: a tool can declare that it needs a value from a person, and execution waits for that input before continuing.

You declare it as a dependency so the runtime knows where to pause and how to resume once the input arrives.

See the [Dependencies Guide](guides/dependencies.md).

## Next steps

- [Getting Started](guides/getting-started.md) — build and run your first agent
- [Tools Guide](guides/tools.md) — function tools, Pydantic tools, and toolsets
- [Dependencies Guide](guides/dependencies.md) — chain tools and agents into a workflow
- [Structured Output Guide](guides/structured-output.md) — guaranteed typed results
- [Multi-Agent Guide](guides/multi-agent.md) — coordinate agents with a Swarm
- [Private Data Guide](guides/private-data.md) — secrets, PII, redaction, and whitelists
- [Frontend Events Guide](guides/frontend-events.md) — enrichment events vs. status messages
- [Billing and Usage Guide](guides/billing-and-usage.md) — token usage and plans
- [Scheduled Invocation Guide](guides/scheduled-invocation.md) — `cron` / `every` / `at`
- [API Reference Index](api/README.md) — the full public surface
