# Testing & Local Development

Building an agent is mostly building ordinary Python — your tools are your functions,
your schemas are your Pydantic models, your wiring is your code. That means you can
test most of an agent the same way you test anything else: fast, offline, and without
ever calling a live model.

This guide shows you how. You will learn to **see exactly what your agent would send**
to the orchestrator without running it, to **unit-test your tool functions directly**
because they are plain callables, to **mock the agent's response** so your application
code can be tested in isolation, and to **iterate quickly** while you build. None of
this requires a network round trip, and most of it requires no API key at all.

If you are brand new, read [Getting Started](getting-started.md) first to build a
working agent, then come back here to lock it down with tests.

## What This Is

There are two very different things to test when you build with mAIvn, and keeping them
separate is the whole trick:

1. **Your code** — the tool functions you wrote, the Pydantic models you defined, and
   the application logic that consumes an agent's answer. This is the part you own, and
   it is the part most worth testing. It is ordinary Python and tests like ordinary
   Python — no model, no network, no mocking required.
2. **The agent's reasoning** — *which* tool the model decides to call and *when*. That
   decision happens in the hosted orchestrator at invocation time. You do not unit-test
   the model's judgment; instead you (a) verify the *request* you would send is shaped
   correctly, and (b) mock the *response* so your downstream code can be tested without
   a live call.

> **The mental model.** mAIvn sends the *schemas* of your tools to the orchestrator; it
> never sends your code. Your tool bodies run locally. So the seam between "your code"
> and "the model" is exactly the seam between "what I test directly" and "what I inspect
> or mock." See the [Overview](../overview.md) for the full picture of where code runs.

Everything below is built on the public `maivn` and `maivn_shared` surfaces — the same
`Agent`, decorators, and models you use in production.

## Inspect What the Agent Will Send Without a Live Run

Before you spend a single token, you can ask an agent to **compile** the request it
*would* send — messages, tool schemas, and configuration — and assert on it. This is
the fastest feedback loop in the SDK: it is pure local computation, needs no successful
network call, and answers questions like *"is my tool actually registered?"*, *"is its
description what I think it is?"*, and *"did `targeted_tools` narrow the set correctly?"*

### `compile_state()` — the request, not the run

`agent.compile_state(messages, ...)` runs the same compilation step `invoke()` does, then
stops and hands you a `SessionRequest` instead of executing it. The `SessionRequest` is a
public `maivn_shared` model, so you can read it directly or `model_dump()` it.

```python
from maivn import Agent
from maivn.messages import HumanMessage

agent = Agent(name='helper', api_key='test-key')

@agent.toolify(description='Get current weather for a city')
def get_weather(city: str) -> dict:
    return {'city': city, 'temp': 72}

request = agent.compile_state([HumanMessage(content='Weather in Austin?')])

# `request` is a maivn_shared.SessionRequest — inspect it offline.
assert any(tool.name == 'get_weather' for tool in request.tools)
```

Each entry in `request.tools` is a `ToolSpec` carrying the **schema the orchestrator
sees** — and nothing more. The useful fields for tests are:

| Field | What it tells you |
| --- | --- |
| `name` | The tool name the model plans against. |
| `description` | The text the model reads to decide *when* to call the tool. |
| `args_schema` | The JSON schema derived from your type hints — argument names, types, constraints. |
| `tags` | Tags you declared (or that were auto-derived from permissions). |
| `always_execute` / `final_tool` | The execution-role flags you set. |

This is a natural place to assert that a tool's description and parameter schema are
what you intend, since those are precisely what steer the model's tool selection:

```python
def test_weather_tool_schema() -> None:
    agent = Agent(name='helper', api_key='test-key')

    @agent.toolify(description='Get current weather for a city')
    def get_weather(city: str) -> dict:
        return {'city': city, 'temp': 72}

    request = agent.compile_state([HumanMessage(content='hi')])
    spec = next(t for t in request.tools if t.name == 'get_weather')

    assert spec.description == 'Get current weather for a city'
    assert 'city' in spec.args_schema['properties']
```

> **`compile_state()` is local and side-effect-free.** It compiles tools and assembles
> the request; it does not call the orchestrator and does not run your tool bodies. An
> `api_key` value still has to be present on the `Agent` (any non-empty string works for
> compilation), because the key is part of the agent's configuration — but it is never
> *used* by `compile_state()`, so a dummy value is fine in tests.

### Narrowing with `targeted_tools`

`compile_state()` accepts the same `targeted_tools` argument as `invoke()`, so you can
verify that targeting (and its automatic dependency inclusion) resolves to the set you
expect — again, without running anything:

```python
request = agent.compile_state(
    [HumanMessage(content='...')],
    targeted_tools=['generate_report'],
)
compiled_names = {tool.name for tool in request.tools}
# `generate_report` plus any tools it depends on are present.
```

### `list_tools()` and `get_tool()` — registry introspection

If you only need to confirm *what is registered* — not the full compiled request —
`agent.list_tools()` returns the registered `BaseTool` objects and `agent.get_tool(tool_id)`
fetches one by its `tool_id`. These read the in-memory registry directly:

```python
agent = Agent(name='helper', api_key='test-key')

@agent.toolify(description='Add two numbers')
def add(a: int, b: int) -> dict:
    return {'sum': a + b}

tools = agent.list_tools()
assert {t.name for t in tools} == {'add'}
```

A registered `BaseTool` exposes `name`, `description`, `tool_id`, `tags`,
`dependencies`, `always_execute`, and `final_tool` — enough to assert that a toolset
registered the methods you expected, that a dependency was wired, or that exactly one
tool is marked `final_tool=True`.

> **Catch configuration mistakes early.** `agent.validate_tool_configuration()` runs the
> same per-scope checks the runtime enforces (for example, *at most one* `final_tool` per
> scope) and raises if something is wrong. Calling it in a test turns a would-be runtime
> error into a fast, local assertion. Misconfigured dependencies already fail at
> *import time*: the `@depends_on_*` decorators validate that each `arg_name` exists in
> the target's signature, so binding a dependency to a parameter that does not exist
> raises `ValueError` the moment your test module imports, not mid-invocation.

## Unit-Test Your Tool Functions Directly

Here is the part that surprises people: **a tool is just your function.** `@toolify`
registers the callable with the agent, but it does not hide it or wrap it away — the
function is still the function you wrote, and you can call it directly in a test with no
agent involved at all.

```python
from maivn import Agent

agent = Agent(name='math', api_key='test-key')

@agent.toolify(description='Add two numbers')
def add(a: int, b: int) -> dict:
    return {'sum': a + b}

def test_add_directly() -> None:
    # No agent, no model, no network — just the function.
    assert add(2, 3) == {'sum': 5}
```

This is where the bulk of your test coverage should live. The orchestrator decides
*whether* to call `add`; your test proves that *when* it is called, it returns the right
answer. Those are separate concerns, and the second one is plain Python.

The same holds for **toolset methods** — decorate the class with `@toolset`, then test
the instance like any object:

```python
from maivn import toolify, toolset

@toolset(prefix='math')
class MathTools:
    def __init__(self, precision: int):
        self._precision = precision

    @toolify(description='Divide two numbers')
    def divide(self, a: float, b: float) -> dict:
        return {'result': round(a / b, self._precision)}

def test_divide_method() -> None:
    tools = MathTools(precision=2)
    assert tools.divide(1, 3) == {'result': 0.33}
```

And for **Pydantic-model tools**, the model is just a Pydantic model — construct and
validate it directly, including the validation the agent's output would have to pass:

```python
from pydantic import BaseModel, Field, ValidationError as PydanticValidationError
import pytest

class WeatherReport(BaseModel):
    city: str
    temperature: int = Field(..., ge=-100, le=150)

def test_report_validation() -> None:
    WeatherReport(city='Austin', temperature=72)  # valid
    with pytest.raises(PydanticValidationError):
        WeatherReport(city='Austin', temperature=999)  # out of range
```

A few practical notes:

- **Async tools** test the same way — `await` the function inside an async test (e.g.
  with `pytest.mark.asyncio` or `anyio`).
- **Private data** is injected at execution time and is never visible to the model, but
  in a direct unit test you simply pass the argument yourself — the function does not
  know or care that the value would have been injected. See the
  [Private Data Guide](private-data.md) for how injection works at runtime.
- **Dependencies** (`@depends_on_tool`, `@depends_on_agent`, …) only affect *scheduling*
  inside an invocation. They do not change your function's signature, so a direct call
  works exactly as written; you supply the dependency's value as the argument.

## Mocking Agent Responses in Tests

Once your tools are covered, the remaining question is: *does my application behave
correctly given an agent's answer?* You do not need a live model to test that — you need
a predictable response. The cleanest approach is to patch the agent's invocation method
so it returns a `SessionResponse` you construct.

`SessionResponse` is a public `maivn_shared` model. The fields most relevant to tests are
`responses` (the list of assistant texts emitted during the session), `result`
(structured output, when present), `session_id`, and `token_usage`. The convenience
property `response` is read-only and returns the *last* entry of `responses` — so you
construct a fake with `responses=[...]` and read it back as `.response`:

```python
from unittest.mock import patch

from maivn import Agent
from maivn.messages import HumanMessage
from maivn_shared import SessionResponse


def summarize_ticket(agent: Agent, ticket: str) -> str:
    """The application code under test."""
    response = agent.invoke([HumanMessage(content=ticket)])
    return response.response.strip()


def test_summarize_ticket() -> None:
    agent = Agent(name='support', api_key='test-key')
    fake = SessionResponse(
        session_id='test-session',
        responses=['  Customer wants a refund.  '],
    )

    with patch.object(agent, 'invoke', return_value=fake) as mock_invoke:
        result = summarize_ticket(agent, 'I want my money back')

    assert result == 'Customer wants a refund.'
    mock_invoke.assert_called_once()
```

Patching `invoke` keeps the seam exactly where it belongs: your tool functions and your
application logic are exercised for real, and only the model's *decision* — the one part
that is non-deterministic and remote — is stubbed.

For structured-output flows, set `result` to a model instance (or its dict form) so your
post-processing code can read it back:

```python
from pydantic import BaseModel

class Sentiment(BaseModel):
    label: str
    confidence: float

fake = SessionResponse(
    session_id='test-session',
    responses=['Analyzed.'],
    result=Sentiment(label='positive', confidence=0.92),
)
```

### Mocking a stream

`agent.stream(...)` yields `RawSSEEvent` objects — the public transport-level event
type. To test code that consumes a stream, patch `stream` to return an iterator of the
events you care about:

```python
from maivn import RawSSEEvent

def test_stream_consumer() -> None:
    agent = Agent(name='support', api_key='test-key')
    events = iter([
        RawSSEEvent(name='enrichment', payload={'phase': 'planning'}),
        RawSSEEvent(name='final', payload={'response': 'Done.'}),
    ])

    with patch.object(agent, 'stream', return_value=events):
        names = [event.name for event in agent.stream([])]

    assert names == ['enrichment', 'final']
```

If your application normalizes events before handing them to a frontend, feed your mock
events through `normalize_stream(...)` in the test exactly as you do in production — see
[Frontend Events](frontend-events.md) for the normalized `AppEvent` contract and the
`build_*_payload()` helpers you can use to assemble contract-shaped events by hand.

> **Prefer the public seam.** Patch the public methods you call — `invoke`, `ainvoke`,
> `stream`, `astream` — rather than reaching into internal attributes. Those method
> signatures are the supported surface and are stable across releases; internals are not.

## Tips for Fast Local Iteration

A few habits keep the build-test loop tight:

- **Keep tool bodies pure where you can.** A tool that takes its inputs as arguments and
  returns a value (rather than reading globals or hidden state) is trivial to unit-test
  and trivial for the agent to use correctly. Push side effects to the edges.
- **Assert on the compiled request, not on model behavior.** Use `compile_state()` to
  lock down tool names, descriptions, and parameter schemas. These are deterministic and
  are exactly what steer the model — so they are worth a test even though the model's
  choice is not.
- **Let the decorators fail loudly.** Because `@depends_on_*` validates argument names at
  import time, a simple `import` of your agent module in a test already catches a class of
  wiring bugs. Registration adds more guards — `add_toolset` rejects duplicate tool names
  with a `ValueError` — and `agent.validate_tool_configuration()` covers the per-scope
  rules. Calling all three in a test keeps misconfiguration loud and local.
- **Use a dummy API key in tests.** Construction and `compile_state()` only require a key
  to be *present*, not valid — any non-empty string works for offline tests. Keep real
  keys in environment variables (`MAIVN_API_KEY`) and out of your test code; read them
  explicitly when you do want a live call.
- **Mark live tests as slow and skip them by default.** Tests that actually hit the
  orchestrator are integration tests — gate them behind a marker or an env-var check so
  your fast suite stays fast.

  ```python
  import os
  import pytest

  @pytest.mark.skipif(not os.environ.get('MAIVN_API_KEY'), reason='needs live key')
  def test_real_invocation() -> None:
      agent = Agent(name='helper', api_key=os.environ['MAIVN_API_KEY'])
      ...
  ```

- **Catch SDK errors with `MaivnError`.** Every framework-raised error subclasses
  `maivn_shared.MaivnError`, so a single `except MaivnError` is the catch-all when you
  want a test (or your app) to handle any SDK failure uniformly. `MaivnError` carries
  structured fields — `message`, `error_code`, `context` — and a `.to_dict()` you can log
  or assert against. Construction and usage mistakes (a missing `api_key`, mutually
  exclusive `invoke` options like `force_final_tool` together with `targeted_tools`,
  registering a non-`@toolset` class) surface as `ValueError`/`TypeError` at call time, so
  you can assert on those directly with `pytest.raises`.

  ```python
  import pytest
  from maivn import Agent
  from maivn.messages import HumanMessage

  def test_mutually_exclusive_options() -> None:
      agent = Agent(name='helper', api_key='test-key')
      with pytest.raises(ValueError):
          agent.invoke(
              [HumanMessage(content='hi')],
              force_final_tool=True,
              targeted_tools=['add'],
          )
  ```

## Next Steps

- [Overview](../overview.md) — where your code runs vs. where the model plans, the seam
  this guide tests around.
- [Core Concepts](../core-concepts.md) — the `Agent`, `Tool`, `Session`, and
  `SessionResponse` vocabulary used throughout.
- [Tools and Dependencies](tools.md) — how `@toolify`, toolsets, and `@depends_on_*`
  register and wire the functions you unit-test here.
- [Structured Output](structured-output.md) — `final_tool` and `structured_output()`,
  and the `result` field you mock in tests.
- [Frontend Events](frontend-events.md) — `RawSSEEvent`, `normalize_stream()`, and the
  `AppEvent` contract for testing stream consumers.
- [Agent API](../api/agent.md) — exact signatures for `compile_state()`, `list_tools()`,
  `invoke()`, `stream()`, and `validate_tool_configuration()`.
- [Best Practices](../best-practices.md) — recommended patterns for tool design, error
  returns, and the broader habits the tests here protect.
- [Troubleshooting](../troubleshooting.md) — the common construction and configuration
  errors, with the fixes you can assert against in tests.
