# Basics

The smallest end-to-end examples — enough to see how `Agent`, tools, and
final output fit together.

## Your first agent

```python
import os
from maivn import Agent
from maivn.messages import HumanMessage

agent = Agent(
    name='Hello Agent',
    system_prompt='You are a concise assistant. Reply in one sentence.',
    api_key=os.environ['MAIVN_API_KEY'],
)

response = agent.invoke([HumanMessage(content='What is 7 * 9?')])
print(response)
```

`agent.invoke(...)` is synchronous and returns the full response. Use
`agent.stream(...)` for token-by-token streaming, or `await agent.ainvoke(...)`
inside an event loop.

## A function tool

Register a plain Python function as a tool with `@agent.toolify`:

```python
from maivn import Agent

agent = Agent(name='Hardware Diagnostics', system_prompt='...', api_key='...')

@agent.toolify(
    name='hardware_scanner',
    description='Scan a laptop by serial number and report installed hardware.',
)
def hardware_scanner(serial_number: str, include_peripherals: bool = True) -> dict:
    return {
        'serial_number': serial_number,
        'hardware': {
            'cpu': 'Intel Core Ultra 7 155H',
            'ram_gb': 32,
            'gpu': 'NVIDIA RTX 4070',
        },
    }
```

Type hints become the tool's JSON schema; the docstring becomes the description
visible to the LLM. The agent decides when to call it based on the user's
message and the tool's description.

## A Pydantic tool

Pydantic models work the same way and are particularly useful for the
**final tool** — the structured answer you want at the end of execution.

```python
from pydantic import BaseModel, Field

@agent.toolify(name='laptop_health_summary', final_tool=True)
class LaptopHealthSummary(BaseModel):
    """Compile a holistic health report for a laptop."""
    serial_number: str
    overall_status: str = Field(..., description='ok / warning / critical')
    issues_found: list[str]
    recommended_actions: list[str]
```

`final_tool=True` tells the runtime that *this* tool's output is the final
answer. With `force_final_tool=True` at invocation time, the agent will be
required to terminate by calling it.

## Chaining tools together

Tools can depend on the output of other tools — the runtime executes them in
order, passing the upstream result in as a named argument.

There are two equivalent forms. The decorator form:

```python
from maivn import depends_on_tool

@depends_on_tool(hardware_scanner, arg_name='system_profile')
@agent.toolify(name='firmware_checker')
def firmware_checker(system_profile: dict) -> dict:
    return {
        'serial_number': system_profile['serial_number'],
        'updates_available': [
            {'component': 'BIOS', 'current': 'v1.03', 'latest': 'v1.05'},
        ],
    }
```

And the fluent form (handy when you have multiple dependencies and want to
read them top-to-bottom):

```python
@(
    agent.toolify(name='laptop_health_summary', final_tool=True)
    .depends_on_tool(hardware_scanner, 'hardware_overview')
    .depends_on_tool(firmware_checker, 'firmware_status')
)
class LaptopHealthSummary(BaseModel):
    hardware_overview: dict
    firmware_status: dict
    recommended_actions: list[str]
```

When `LaptopHealthSummary` is constructed, the runtime has already executed
`hardware_scanner` and `firmware_checker` and will pass their results in as
`hardware_overview` and `firmware_status`.

## Private data

`agent.private_data` is a dict that holds values the LLM should not see in
plain text — credentials, customer identifiers, internal IDs. Tools opt in
to receiving a value with `@depends_on_private_data`:

```python
from maivn import depends_on_private_data

@depends_on_private_data(arg_name='serial_number', data_key='serial_number')
@agent.toolify(name='hardware_scanner')
def hardware_scanner(serial_number: str, include_peripherals: bool = True) -> dict:
    return {'serial_number': serial_number, 'hardware': {...}}

agent.private_data = {'serial_number': 'SN-LPTP-8831'}
agent.invoke([HumanMessage(content='Inspect the laptop and report.')])
```

The LLM sees a schema that says "this tool needs a serial number" — but the
actual value is injected at execution time by the runtime, not by the
model.

See [Private Data](./private-data.md) for redaction patterns,
placeholder replacement, and tool-result PII protection.

## Forcing structured output

If you only care about the final structured answer, use
`agent.structured_output(model=...)` to bypass the orchestration step entirely:

```python
response = agent.structured_output(model=LaptopHealthSummary).invoke([
    HumanMessage(content='Inspect laptop SN-LPTP-8831.'),
])

print(response.result)   # a LaptopHealthSummary instance
print(response.responses)  # the assistant text trace
```

Direct schema extraction is faster for one-shot calls than full
orchestration; use it when you don't need multi-step reasoning.

## Complex types

The SDK handles arbitrarily nested Pydantic models, including unions and
recursive structures. A condensed sketch of a robot-specification model:

```python
from typing import Literal
from pydantic import BaseModel, Field

class Motor(BaseModel):
    manufacturer: str
    max_power_w: float
    max_torque_nm: float

class RobotHand(BaseModel):
    motors: dict[str, Motor] = Field(default_factory=dict)

class RobotArm(BaseModel):
    joint_type: Literal['revolute', 'prismatic']
    motor: Motor
    end_effector: RobotHand | None = None

class Robot(BaseModel):
    name: str
    arms: list[RobotArm]
```

The agent populates the model exactly as the schema describes — no need to
flatten it into primitives.

## Deep dependency chains

For larger workflows, dependencies stack. The runtime resolves the DAG and
runs independent branches in parallel:

```text
            ┌─► load_engine_specs ─┐
hardware ──┤                      ├─► powertrain_design ─► final_spec
            └─► load_fuel_specs ──┘
```

Each layer in the DAG runs concurrently with its siblings, so a wide chain
finishes in roughly `O(depth)` LLM calls rather than `O(width × depth)`.

## Async

Every terminal has an async sibling:

```python
import asyncio

async def main():
    response = await agent.ainvoke([HumanMessage(content='hi')])
    async for chunk in agent.astream([HumanMessage(content='hi')]):
        print(chunk, end='', flush=True)

asyncio.run(main())
```

## What's next

- **[Agents & Tools](./agents-and-tools.md)** — three ways to register tools,
  cross-agent dependencies, before/after hooks.
- **[Swarms](./swarms.md)** — multi-agent collaboration.
- **[Structured Output guide](../guides/structured-output.md)** — deeper
  treatment of the schema/forced-output paths.
- **[Tools guide](../guides/tools.md)** — the full decorator reference.
