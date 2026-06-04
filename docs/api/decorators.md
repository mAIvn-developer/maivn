# Decorators

Dependency decorators declare data flow between tools, agents, and external data sources. They enable the SDK to automatically resolve dependencies during execution. The SDK also provides arg-level policy decorators for system-tool-gated flows.

## Import

```python
from maivn import (
    compose_artifact_policy,
    depends_on_tool,
    depends_on_agent,
    depends_on_private_data,
    depends_on_interrupt,
    depends_on_await_for,
    depends_on_reevaluate,
    # Toolset class pattern
    toolify,
    toolset,
    # Permission helpers used with @toolify(permissions=...)
    PermissionFlag,
    PermissionSet,
    require_permissions,
    # Provider metadata for toolset classes
    AuthMode,
    ProviderCapability,
    ProviderMetadata,
)
```

## depends_on_tool

Declare a dependency on another tool's output.

```python
def depends_on_tool(
    tool_ref: str | BaseTool | Callable,
    arg_name: str,
) -> Callable
```

### Parameters

| Parameter  | Type                          | Description                                        |
| ---------- | ----------------------------- | -------------------------------------------------- |
| `tool_ref` | `str \| BaseTool \| Callable` | Tool reference (function, tool ID, or tool object) |
| `arg_name` | `str`                         | Function argument to receive the tool's output     |

### Example

```python
from maivn import Agent, depends_on_tool

agent = Agent(name='data_agent', api_key='...')

@agent.toolify(description='Fetch raw data')
def fetch_data(source: str) -> dict:
    return {'source': source, 'records': [...]}

@agent.toolify(description='Process fetched data')
@depends_on_tool(fetch_data, arg_name='raw_data')
def process_data(raw_data: dict) -> dict:
    # 'raw_data' is automatically populated with fetch_data's output
    return {'processed': len(raw_data['records'])}
```

### Chaining Multiple Dependencies

```python
@agent.toolify(description='Step 1')
def step_one() -> dict:
    return {'step': 1}

@agent.toolify(description='Step 2')
def step_two() -> dict:
    return {'step': 2}

@agent.toolify(description='Final step')
@depends_on_tool(step_one, arg_name='result_one')
@depends_on_tool(step_two, arg_name='result_two')
def final_step(result_one: dict, result_two: dict) -> dict:
    return {'combined': [result_one, result_two]}
```

## depends_on_agent

Declare a dependency on another agent's output.

```python
def depends_on_agent(
    agent_ref: str | Any,
    arg_name: str,
) -> Callable
```

### Parameters

| Parameter   | Type           | Description                                       |
| ----------- | -------------- | ------------------------------------------------- |
| `agent_ref` | `str \| Agent` | Agent reference (Agent object, agent ID, or name) |
| `arg_name`  | `str`          | Function argument to receive the agent's output   |

### Example

```python
from maivn import Agent, Swarm, depends_on_agent

researcher = Agent(name='researcher', api_key='...')
writer = Agent(name='writer', api_key='...')

@researcher.toolify(description='Research a topic')
def research(topic: str) -> dict:
    return {'findings': f'Research on {topic}'}

@writer.toolify(description='Write based on research')
@depends_on_agent(researcher, arg_name='research_result')
def write_article(research_result: dict) -> dict:
    return {'article': f'Article about {research_result["findings"]}'}

swarm = Swarm(name='team', agents=[researcher, writer])
```

### How It Works

When a tool depends on an agent:

1. The swarm identifies the dependency
2. The dependent agent is invoked first
3. Its output is passed to the tool's specified argument

## depends_on_private_data

Inject a private value into a tool at execution time.

```python
def depends_on_private_data(
    data_key: str,
    arg_name: str,
) -> Callable
```

### Parameters

| Parameter  | Type  | Description                                  |
| ---------- | ----- | -------------------------------------------- |
| `data_key` | `str` | Key in the agent's `private_data` dictionary |
| `arg_name` | `str` | Function argument to receive the value       |

### Example

```python
from maivn import Agent, depends_on_private_data

agent = Agent(name='api_agent', api_key='...')

@agent.toolify(description='Call external API')
@depends_on_private_data(data_key='api_secret', arg_name='secret')
def call_external_api(query: str, secret: str) -> dict:
    # 'secret' is injected at runtime, never exposed to LLM
    return {'result': f'API response for {query}'}

# Set private data
agent.private_data = {'api_secret': 'sk-xxx-secret-key'}
```

### Security Model

Private data follows strict security boundaries:

1. **Schema-only planning**: LLM sees only field names/types, never values
2. **Runtime injection**: Values injected at execution time, held within the runtime
3. **Automatic redaction**: Results containing private data are redacted
4. **Never logged**: Private data values never appear in logs

### Pydantic Class Targets

`@depends_on_private_data` works on `@agent.toolify`'d Pydantic model classes the same way it works on function tools. The `arg_name` is validated against the model's `model_fields` instead of a callable signature, and the field is automatically marked as a data dependency in the generated tool schema so the LLM never sees it as a required argument:

```python
from maivn import Agent, depends_on_private_data
from pydantic import BaseModel

agent = Agent(name='audit_agent', api_key='...')

@depends_on_private_data(data_key='customer_ssn', arg_name='ssn')
@agent.toolify(name='audit_record', description='Record an audit entry', final_tool=True)
class AuditRecord(BaseModel):
    ssn: str        # injected at execution time from private_data['customer_ssn']
    note: str       # supplied by the LLM

    def __call__(self) -> dict:
        return {'ssn_last4': self.ssn[-4:], 'note': self.note}
```

If `arg_name` does not match any model field, the decorator raises `ValueError` at import time (it used to silently no-op on classes — that is now a hard error so misconfigured dependencies fail loudly).

## compose_artifact_policy

Declare whether a specific argument may consume output from the `compose_artifact` system tool.

```python
def compose_artifact_policy(
    arg_name: str,
    *,
    mode: Literal['forbid', 'allow', 'require'] = 'allow',
    approval: Literal['none', 'explicit'] = 'none',
) -> Callable[[Callable | type[BaseModel]], Callable | type[BaseModel]]
```

> Unlike the other dependency decorators, `compose_artifact_policy` may be applied to
> either a callable or a Pydantic `BaseModel` subclass — for model tools, `arg_name`
> refers to a model field name.

### Parameters

| Parameter  | Type                               | Default   | Description                                                          |
| ---------- | ---------------------------------- | --------- | -------------------------------------------------------------------- |
| `arg_name` | `str`                              | Required  | Function or model field that the policy applies to                   |
| `mode`     | `'forbid' \| 'allow' \| 'require'` | `'allow'` | Whether the arg must not, may, or must consume `compose_artifact`    |
| `approval` | `'none' \| 'explicit'`             | `'none'`  | Whether explicit invocation approval is required for that target arg |

### Example

```python
from maivn import Agent, SystemToolsConfig, compose_artifact_policy
from maivn.messages import HumanMessage

agent = Agent(name='artifact_agent', api_key='...')

@compose_artifact_policy('query', mode='require', approval='explicit')
@agent.toolify(description='Validate a SQL query artifact')
def validate_query_artifact(query: str) -> dict:
    return {'validated': True, 'query': query}

response = agent.invoke(
    [HumanMessage(content='Compose and validate the SQL query artifact')],
    force_final_tool=True,
    system_tools_config=SystemToolsConfig(
        allowed_tools=['compose_artifact'],
        approved_compose_artifact_targets=['validate_query_artifact.query'],
    ),
)
```

### Behavior

- `forbid`: runtime rejects downstream arg values that originate from `compose_artifact`
- `allow`: `compose_artifact` may be used but is not required
- `require`: runtime rejects direct/manual arg values that do not originate from `compose_artifact`
- `approval='explicit'`: `compose_artifact` must also be approved in invocation `system_tools_config` for that arg target

This decorator is metadata-driven and can be stacked before or after `@agent.toolify(...)`.

## depends_on_interrupt

Collect user input during tool execution.

```python
def depends_on_interrupt(
    arg_name: str,
    input_handler: Callable[[str], Any],
    prompt: str = "",
    input_type: InputType | None = None,
    choices: list[str] | None = None,
) -> Callable
```

`InputType` is `Literal['text', 'choice', 'boolean', 'number', 'email', 'password']`.

### Parameters

| Parameter       | Type                   | Default  | Description                             |
| --------------- | ---------------------- | -------- | --------------------------------------- |
| `arg_name`      | `str`                  | Required | Function argument to receive user input |
| `input_handler` | `Callable[[str], Any]` | Required | Function that collects input            |
| `prompt`        | `str`                  | `""`     | Prompt displayed to the user (optional) |
| `input_type`    | `InputType \| None`    | `None`   | Override the input type. When unset, auto-detected from the parameter's type annotation (e.g., `bool` -> `'boolean'`, `int`/`float` -> `'number'`, `Literal[...]` -> `'choice'`, otherwise `'text'`) |
| `choices`       | `list[str] \| None`    | `None`   | Override choices for `'choice'` inputs. When unset, auto-detected from `Literal[...]` annotations |

### Example

```python
from maivn import Agent, depends_on_interrupt, default_terminal_interrupt

agent = Agent(name='interactive', api_key='...')

@agent.toolify(description='Greet the user')
@depends_on_interrupt(
    arg_name='user_name',
    input_handler=default_terminal_interrupt,
    prompt='Please enter your name: ',
)
def greet_user(user_name: str) -> dict:
    return {'greeting': f'Hello, {user_name}!'}
```

### Custom Input Handler

When your handler has its own prompting logic, `prompt` can be omitted:

```python
def file_picker_dialog() -> str:
    # Custom GUI dialog - no prompt needed
    return show_file_picker()

@agent.toolify()
@depends_on_interrupt(arg_name='file_path', input_handler=file_picker_dialog)
def process_file(file_path: str) -> dict:
    return {'processed': file_path}
```

With a prompt:

```python
def custom_handler(prompt: str) -> str:
    # Custom logic (GUI dialog, API call, etc.)
    return get_input_from_somewhere(prompt)

@agent.toolify()
@depends_on_interrupt(
    arg_name='choice',
    input_handler=custom_handler,
    prompt='Select an option (1-3): ',
)
def process_choice(choice: str) -> dict:
    return {'selected': choice}
```

### Interrupt Flow

1. Tool execution reaches the interrupt dependency
2. Execution pauses
3. `input_handler` is called with the prompt
4. User provides input
5. Execution resumes with the input value

## depends_on_await_for

Declare a sequencing dependency without injecting another tool's output into an argument.

```python
def depends_on_await_for(
    tool_ref: str | BaseTool | Callable,
    *,
    timing: Literal['before', 'after'] = 'after',
    instance_control: Literal['each', 'all'] = 'each',
) -> Callable
```

> `timing` and `instance_control` are keyword-only.

### Parameters

| Parameter          | Type                          | Default   | Description                                                        |
| ------------------ | ----------------------------- | --------- | ------------------------------------------------------------------ |
| `tool_ref`         | `str \| BaseTool \| Callable` | Required  | Tool reference that defines the sequencing boundary                |
| `timing`           | `'before' \| 'after'`         | `'after'` | Whether this tool should run before or after the referenced tool   |
| `instance_control` | `'each' \| 'all'`             | `'each'`  | Pair instances one-by-one or wait for all matching prior instances |

### Example

```python
from maivn import Agent, depends_on_await_for

agent = Agent(name='workflow_agent', api_key='...')

@agent.toolify(description='Fetch raw data')
def fetch_data(source: str) -> dict:
    return {'source': source}

@agent.toolify(description='Write audit log after fetch completes')
@depends_on_await_for(fetch_data)
def write_audit_entry() -> dict:
    return {'logged': True}
```

`@depends_on_await_for` is metadata-only. It affects execution order, but it does not inject a tool result into a function argument.

## depends_on_reevaluate

Declare that planning must pause at a specific boundary and insert `reevaluate`.

```python
def depends_on_reevaluate(
    tool_ref: str | BaseTool | Callable,
    *,
    timing: Literal['before', 'after'] = 'after',
    instance_control: Literal['each', 'all'] = 'each',
) -> Callable
```

> `timing` and `instance_control` are keyword-only.

### Parameters

| Parameter          | Type                          | Default   | Description                                                            |
| ------------------ | ----------------------------- | --------- | ---------------------------------------------------------------------- |
| `tool_ref`         | `str \| BaseTool \| Callable` | Required  | Tool reference that defines the reevaluate boundary                    |
| `timing`           | `'before' \| 'after'`         | `'after'` | Whether reevaluate should happen before or after the referenced tool   |
| `instance_control` | `'each' \| 'all'`             | `'each'`  | Apply reevaluate per matching instance or after all matching instances |

### Example

```python
from maivn import Agent, depends_on_reevaluate

agent = Agent(name='planning_agent', api_key='...')

@agent.toolify(description='Fetch source material')
def fetch_source() -> dict:
    return {'content': '...'}

@agent.toolify(description='Summarize after reviewing fetched content')
@depends_on_reevaluate(fetch_source, timing='after', instance_control='all')
def summarize_source() -> dict:
    return {'summary': '...'}
```

`@depends_on_reevaluate` is metadata-only on the SDK side, but the runtime enforces it: when the dependent tool is registered, the runtime ensures a `reevaluate` cycle runs at the declared boundary unless the model already planned a satisfying one, and the dependent tool is gated so it cannot run before that reevaluate completes. You are **not** required to make the model plan `reevaluate` itself for declared boundaries — the runtime guarantees the boundary either way, and a model-planned reevaluate at the same point is reconciled to avoid duplication.

When a dependency-driven reevaluate fires, an enrichment event (`phase="reevaluate_accrued"`) is emitted with `source="dependency"`, plus `trigger_tool` and `target_tool` attribution. Model-planned reevaluates emit the same event with `source="llm"`. See [Enrichment Events](../../../../docs/features/ENRICHMENT_EVENTS.md#reevaluate-accrued) for the payload shape.

A boundary that was already satisfied earlier in the same session is tracked by the runtime so future re-plans of the same chain don't cascade additional reevaluate cycles.

## Decorator Order

Decorators can be stacked in any order. The `@agent.toolify()` decorator should typically be closest to the function:

```python
@depends_on_tool(step_one, 'result_one')
@depends_on_private_data('secret', 'api_key')
@agent.toolify(description='Final processing')
def process(result_one: dict, api_key: str) -> dict:
    return {'processed': True}
```

Or with `@agent.toolify()` on top:

```python
@agent.toolify(description='Final processing')
@depends_on_tool(step_one, 'result_one')
@depends_on_private_data('secret', 'api_key')
def process(result_one: dict, api_key: str) -> dict:
    return {'processed': True}
```

Both orders work correctly.

The same dependency decorators also work before registering tools with
`Agent(..., tools=[...])` or `agent.add_tool(...)`:

```python
@depends_on_tool(step_one, 'result_one')
def process(result_one: dict) -> dict:
    """Process the first step result."""
    return {'processed': True}

agent = Agent(name='processor', api_key='...', tools=[step_one, process])
```

For swarm member agents, stack dependency decorators before `@swarm.member` on a zero-argument
agent factory, or use the builder form such as `swarm.member.depends_on_agent(...)`:

```python
@swarm.member
@depends_on_tool(load_context, 'context')
def researcher() -> Agent:
    return Agent(name='researcher', api_key='...')

writer = swarm.member.depends_on_agent(
    researcher,
    arg_name='research_notes',
)(Agent(name='writer', api_key='...'))
```

## Validation

The decorators validate that `arg_name` exists in the function signature:

```python
@agent.toolify()
@depends_on_tool(other_tool, arg_name='nonexistent')  # Raises ValueError
def my_tool(actual_arg: str) -> dict:
    return {}
```

Error message:

```
ValueError: Argument 'nonexistent' specified in dependency decorator
not found in function 'my_tool' signature: (actual_arg: str) -> dict
```

## Dependency Resolution

The SDK builds a dependency graph and resolves dependencies in order:

1. Tools with no dependencies execute first (or in parallel)
2. Dependent tools wait for their dependencies
3. The final tool executes last (if `force_final_tool=True`)

### Parallel Execution

Independent dependencies execute in parallel:

```python
@agent.toolify()
def fetch_a() -> dict: ...

@agent.toolify()
def fetch_b() -> dict: ...

@agent.toolify()
@depends_on_tool(fetch_a, 'a')
@depends_on_tool(fetch_b, 'b')
def combine(a: dict, b: dict) -> dict:
    # fetch_a and fetch_b run in parallel
    return {'combined': [a, b]}
```

## Toolset decorators

The dependency decorators above declare data flow between *individual* tools. The
toolset decorators below promote a configured class to a collection of related tools
that share an instance (a database handle, an HTTP client, an OAuth token, etc.).

### `@toolset`

Marks a class as a toolset. Two forms:

```python
from maivn import toolset


@toolset
class Plain:
    ...


@toolset(prefix='github', tags=['developer'], require_marker=True)
class Configured:
    ...
```

| Argument | Default | Effect |
| --- | --- | --- |
| `prefix` | snake_case class name | Prepended to each method-tool name. The prefix is uppercased and joined with `_` (e.g. `prefix='gmail'` + method `get_message` → `GMAIL_get_message`). The uppercase form keeps tool names compliant with the OpenAI/Anthropic tool-name regex `^[a-zA-Z0-9_-]{1,64}$` and makes the namespace boundary readable in logs. |
| `tags` | `()` | Tags applied to every method-tool produced. |
| `require_marker` | `True` | When True, only `@toolify`-marked methods are tools. Setting `False` exposes every public method — discouraged for new code. |
| `metadata` | `{}` | Free-form metadata merged into every method-tool. |

### `@toolify` (method form)

When applied inside a `@toolset` class, `@toolify` marks an individual method as a tool
and declares its metadata. The same `@toolify` symbol that decorates free functions is
used here; the registration path is selected automatically by the SDK.

```python
from maivn import PermissionFlag, PermissionSet, toolify


class Example:
    @toolify
    def simple(self) -> None:
        """Bare form. No permission tags, no destructive marker."""

    @toolify(
        name='renamed',                            # override the exposed tool name
        description='...',                         # override the docstring
        permissions=PermissionSet(PermissionFlag.WRITE),
        destructive=False,
        always_execute=False,
        final_tool=False,
        tags=['payments'],
        metadata={'audit_zone': 'high'},
        before_execute=...,
        after_execute=...,
    )
    def write_payment(self, ...): ...
```

`@toolify` coexists with the dependency decorators above (`@depends_on_tool`,
`@depends_on_agent`, `@compose_artifact_policy`, etc.). Order does not matter — each
decorator attaches its own attribute and the SDK's dependency collector reads them all
at registration time.

### Permission helpers

`PermissionFlag` is a `Flag` enum; `PermissionSet` is an immutable bitfield wrapper.
The SDK auto-derives tag names from the declared permissions (`READ` → `"read"`,
`DELETE` → `"delete"` + `"destructive"`, etc.) so filters like
`include_tags=["read"]` and `exclude_tags=["destructive"]` work without you having to
add those strings to each method's `tags=` list.

| Flag | Auto-tags added |
| --- | --- |
| `READ` | `"read"` |
| `WRITE` | `"write"` |
| `DELETE` | `"delete"`, `"destructive"` |
| `EXPORT` | `"export"` |
| `IMPORT` | `"import"`, `"destructive"` |
| `ADMIN` | `"admin"`, `"destructive"` |
| `IMPERSONATE` | `"impersonate"`, `"destructive"` |

Setting `destructive=True` on `@toolify(...)` also adds `"destructive"` regardless of
the permission flags.

Hosts can validate a caller's grant against a tool's declared set with
`require_permissions(granted, required)`, which raises `PermissionError` if any flag in
`required` is missing.

### `ProviderMetadata`

Attach a `ProviderMetadata` instance as a class attribute named `metadata` to advertise
provider identity, supported auth modes, scopes, and capabilities. The runtime treats
the attribute as descriptive only — it never inspects it for behavior — but hosts can
surface it in catalogs or audit logs.

```python
from maivn import AuthMode, ProviderCapability, ProviderMetadata, toolset


@toolset(prefix='reports')
class WeeklyReportsToolSet:
    metadata = ProviderMetadata(
        name='weekly_reports',
        display_name='Weekly KPI Reports',
        version='1.0.0',
        description='Pull weekly KPI reports from an internal warehouse.',
        auth_modes=(AuthMode.NONE,),
        capabilities=frozenset({ProviderCapability.READ}),
        tags=('analytics',),
    )
    ...
```

### Registration

```python
agent.add_toolset(instance)
agent.add_toolset(other_instance)   # multiple toolsets accumulate
```

`add_toolset` walks the class, finds every `@toolify`-marked method, and creates one
`MethodTool` per method bound to the instance. Tools are appended to `agent.tools` so
anything that inspects the agent's tool list sees them the same way it sees free
callables.

#### Filter kwargs

```python
agent.add_toolset(instance, *,
    include=None,        # list[str]: only these method names register
    exclude=None,        # list[str]: skip these method names
    include_tags=None,   # list[str]: only methods with any matching tag
    exclude_tags=None,   # list[str]: skip methods with any matching tag
    overrides=None,      # dict[str, ToolOverride]: per-method registration overrides
)
```

Filters compose: a method must pass every active filter. `include` / `exclude` match
the **unprefixed** method name (or the `name=` override on `@toolify`). Tag matching
uses the union of user-declared tags and auto-derived permission tags described above.

If filters drop every method and the toolset has `require_marker=True` (the default),
`add_toolset` raises `ValueError`.

#### Overrides

`overrides` uses the same `ToolOverride` type accepted by `agent.add_tool(...)`
and `MCPServer(tool_overrides=...)`. Use it to retarget a generic toolset for
one app without changing the provider class:

```python
from maivn import ToolOverride

agent.add_toolset(
    instance,
    overrides={
        'search': ToolOverride(
            description='Search only the current customer account.',
            default_args={'limit': 10},
            tags=['customer-support'],
        ),
    },
)
```

Override keys match the unprefixed Python method name, or the `name=` alias on
`@toolify(...)`. Unknown keys raise `ValueError`.

## See Also

- [Agent](agent.md) - `@agent.toolify()` decorator
- [Tools Guide](../guides/tools.md) - Registration styles including the toolset class pattern
- [Dependencies Guide](../guides/dependencies.md) - Detailed patterns
- [Private Data Guide](../guides/private-data.md) - Security model
