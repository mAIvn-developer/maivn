# Examples

A working tour of the SDK, organized by what you'd actually want to build.
Each page contains runnable snippets, comments on what's load-bearing, and
links back to the relevant guide for deeper coverage.

If you're brand new, start with **[Basics](./basics.md)** — it's the shortest
path to a working agent, a tool, and a structured final answer.

## Index

| Page | What you'll learn |
| --- | --- |
| [Basics](./basics.md) | A first agent. Function and Pydantic tools. Tool chaining. Private data injection. Structured final output. |
| [Agents & Tools](./agents-and-tools.md) | The three ways to register tools (`@agent.toolify`, `agent.add_tool(...)`, `Agent(..., tools=[...])`). Tool execution hooks. Cross-agent dependencies. |
| [Swarms](./swarms.md) | Multi-agent collaboration with `Swarm`. Designated final-output agents. Member-style registration. Per-agent `final_tool` ownership. |
| [MCP Integration](./mcp.md) | Registering MCP servers over stdio and HTTP. Auto-setup for third-party servers. |
| [Memory](./memory.md) | Memory configuration, retrieval policies, skill/insight extraction, attaching resources. |
| [Private Data](./private-data.md) | `@depends_on_private_data`, placeholder replacement, `RedactedMessage`. |
| [Batch & Scheduling](./batch-and-scheduling.md) | `agent.batch(...)` / `agent.cron(...)` / `swarm.every(...)`. Retry, jitter, overlap policies. |
| [Interrupts](./interrupts.md) | Human-in-the-loop input collection with `@depends_on_interrupt`. |
| [Real-World Projects](./projects.md) | Larger end-to-end examples — automobile spec, data harmonization, HTML email builder, financial planner with MCP. |

## How to read the examples

The snippets are condensed — boilerplate (logging setup, `.env` loading,
argparse) is omitted so you can see the SDK surface clearly. To run them
locally, all you need is:

```python
import os
from maivn import Agent
from maivn.messages import HumanMessage

agent = Agent(
    name='My Agent',
    system_prompt='...',
    api_key=os.environ['MAIVN_API_KEY'],
)

agent.invoke([HumanMessage(content='hello')])
```

Most examples also work inside [mAIvn Studio](../guides/maivn-studio.md) — if
you see `APP_PROMPTS = [...]` in the source repo, that's the Studio prompt
picker metadata.

## What's missing here

These examples cover the SDK surface. For the **platform** (auth, billing,
projects, webhooks, IAM, analytics) — see the
[Developer Portal guides](../guides/portal-authentication.md). For
**Studio-specific** authoring and debugging, see
[mAIvn Studio guide](../guides/maivn-studio.md).
