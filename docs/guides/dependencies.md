# Dependencies Guide

Dependencies declare data flow between tools and agents. This guide covers all dependency patterns.

## Overview

The maivn SDK supports four types of dependencies:

| Type         | Decorator                  | Description                  |
| ------------ | -------------------------- | ---------------------------- |
| Tool         | `@depends_on_tool`         | Output from another tool     |
| Agent        | `@depends_on_agent`        | Output from another agent    |
| Private Data | `@depends_on_private_data` | Server-side secret injection |
| Interrupt    | `@depends_on_interrupt`    | User input collection        |

It also supports two metadata-only execution controls:

| Control    | Decorator                | Description                                            |
| ---------- | ------------------------ | ------------------------------------------------------ |
| Await-for  | `@depends_on_await_for`  | Enforce execution order without injecting data         |
| Reevaluate | `@depends_on_reevaluate` | Force a reevaluate boundary before continuing planning |

## Tool Dependencies

Chain tools by injecting one tool's output into another.

### Basic Pattern

```python
from maivn import Agent, depends_on_tool

agent = Agent(name='data_agent', api_key='...')

@agent.toolify(description='Fetch raw data from source')
def fetch_data(source: str) -> dict:
    return {'source': source, 'records': [1, 2, 3]}

@agent.toolify(description='Process fetched data')
@depends_on_tool(fetch_data, arg_name='raw_data')
def process_data(raw_data: dict) -> dict:
    return {'processed': len(raw_data['records'])}
```

When `process_data` is called, `fetch_data` runs first and its output is passed to `raw_data`.

Dependency decorators are independent of the registration style. The same dependency graph can
be registered through `Agent(..., tools=[...])` or `agent.add_tool(...)`:

```python
@depends_on_tool(fetch_data, arg_name='raw_data')
def process_data(raw_data: dict) -> dict:
    """Process fetched data."""
    return {'processed': len(raw_data['records'])}

agent = Agent(name='data_agent', api_key='...', tools=[fetch_data, process_data])
```

### Multiple Tool Dependencies

```python
@agent.toolify()
def fetch_users() -> dict:
    return {'users': [...]}

@agent.toolify()
def fetch_orders() -> dict:
    return {'orders': [...]}

@agent.toolify()
@depends_on_tool(fetch_users, arg_name='users')
@depends_on_tool(fetch_orders, arg_name='orders')
def generate_report(users: dict, orders: dict) -> dict:
    return {'report': 'Combined data from users and orders'}
```

Independent dependencies (`fetch_users` and `fetch_orders`) execute in parallel.

### DAG Patterns

Build complex dependency graphs (Directed Acyclic Graphs):

```python
# Level 1: No dependencies
@agent.toolify()
def step_a() -> dict:
    return {'step': 'A'}

@agent.toolify()
def step_b() -> dict:
    return {'step': 'B'}

# Level 2: Depends on Level 1
@agent.toolify()
@depends_on_tool(step_a, 'a_result')
def step_c(a_result: dict) -> dict:
    return {'step': 'C', 'from': a_result}

@agent.toolify()
@depends_on_tool(step_b, 'b_result')
def step_d(b_result: dict) -> dict:
    return {'step': 'D', 'from': b_result}

# Level 3: Depends on Level 2
@agent.toolify(final_tool=True)
@depends_on_tool(step_c, 'c_result')
@depends_on_tool(step_d, 'd_result')
class FinalReport(BaseModel):
    combined: str
```

Execution order:

1. `step_a` and `step_b` run in parallel
2. `step_c` and `step_d` run when their dependencies complete
3. `FinalReport` runs last

## Execution-Control Dependencies

Execution-control decorators are metadata-only. They shape sequencing and reevaluation, but they do not create argument injection dependencies.

### Sequencing Without Data Flow

Use `@depends_on_await_for` when one tool must wait for another even though it does not consume the other tool's return value.

```python
from maivn import depends_on_await_for

@agent.toolify(description='Fetch latest records')
def fetch_records() -> dict:
    return {'records': [...]}

@agent.toolify(description='Write compliance audit entry after fetch')
@depends_on_await_for(fetch_records, timing='after', instance_control='all')
def write_audit_log() -> dict:
    return {'logged': True}
```

Use this for ordering, confirmation flows, audit trails, and any side-effecting tool that must wait even when no data is passed.

### Declaring Reevaluation Boundaries

Use `@depends_on_reevaluate` when planning must stop, inspect completed results, and resume in a later batch.

```python
from maivn import depends_on_reevaluate

@agent.toolify(description='Fetch document text')
def fetch_document() -> dict:
    return {'text': '...'}

@agent.toolify(description='Create final summary after reviewing fetched content')
@depends_on_reevaluate(fetch_document, timing='after', instance_control='all')
def summarize_document() -> dict:
    return {'summary': '...'}
```

This causes planning to insert `reevaluate` at the declared boundary so later work can use accumulated context instead of placeholder arguments.

## Agent Dependencies

In multi-agent systems, tools can depend on other agents' outputs.

### Basic Pattern

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
    return {'article': f'Based on: {research_result}'}

swarm = Swarm(name='team', agents=[researcher, writer])
```

### Agent Dependency Resolution

When a tool depends on an agent:

1. The dependent agent is invoked
2. Its full output (from its tools) is collected
3. That output is passed to the dependent tool

### Swarm Member Dependencies

Use `swarm.member` when the dependency belongs to the swarm's generated agent invocation tool,
rather than to a tool defined inside an agent.

```python
from maivn import Agent, Swarm, depends_on_tool

swarm = Swarm(name='planning_team')

@swarm.toolify(description='Load launch context')
def load_context() -> dict:
    return {'segment': 'regional clinics'}

@swarm.member
@depends_on_tool(load_context, arg_name='context')
def researcher() -> Agent:
    return Agent(name='researcher', api_key='...')

editor = swarm.member.depends_on_agent(
    researcher,
    arg_name='research_notes',
)(Agent(name='editor', api_key='...', use_as_final_output=True))
```

Member dependencies are exposed as arguments on the generated agent invocation tool. Use this for
swarm-level context, agent-to-agent handoffs, execution controls, and interrupt inputs that should
be resolved before a member agent is invoked.

## Private Data Dependencies

Inject server-side secrets without exposing them to the LLM.

### Basic Pattern

```python
from maivn import depends_on_private_data

@agent.toolify(description='Call external API')
@depends_on_private_data(data_key='api_key', arg_name='secret')
def call_api(query: str, secret: str) -> dict:
    # 'secret' is injected at runtime
    return {'result': f'API call with {query}'}

agent.private_data = {'api_key': 'sk-xxx-secret'}
```

### Multiple Secrets

```python
@agent.toolify()
@depends_on_private_data(data_key='db_password', arg_name='db_pass')
@depends_on_private_data(data_key='api_key', arg_name='api_key')
def connect_and_fetch(query: str, db_pass: str, api_key: str) -> dict:
    return {'data': 'fetched'}

agent.private_data = {
    'db_password': 'db-secret',
    'api_key': 'api-secret',
}
```

### Security Model

Private data follows strict security rules:

1. **Schema-only planning**: LLM sees only key names, never values
2. **Server-side injection**: Values injected at execution time
3. **Automatic redaction**: Results are scanned and redacted
4. **Never logged**: Values never appear in logs

See [Private Data Guide](private-data.md) for full details.

## Interrupt Dependencies

Collect user input during tool execution.

### Basic Pattern

```python
from maivn import depends_on_interrupt, default_terminal_interrupt

@agent.toolify(description='Greet user by name')
@depends_on_interrupt(
    arg_name='name',
    input_handler=default_terminal_interrupt,
    prompt='Please enter your name: ',
)
def greet(name: str) -> dict:
    return {'greeting': f'Hello, {name}!'}
```

### Custom Input Handler

When your handler has its own prompting logic, `prompt` can be omitted:

```python
def file_picker() -> str:
    # GUI dialog with its own UI - no prompt needed
    return show_file_dialog()

@agent.toolify()
@depends_on_interrupt(arg_name='file_path', input_handler=file_picker)
def process_file(file_path: str) -> dict:
    return {'processed': file_path}
```

With a prompt passed to the handler:

```python
def gui_input_handler(prompt: str) -> str:
    # Show a GUI dialog with the prompt
    return show_dialog(prompt)

@agent.toolify()
@depends_on_interrupt(
    arg_name='choice',
    input_handler=gui_input_handler,
    prompt='Select option (1-3): ',
)
def process_choice(choice: str) -> dict:
    return {'selected': choice}
```

### Interrupt Flow

1. Tool execution reaches the interrupt
2. Execution pauses
3. `input_handler` is called with the prompt
4. User provides input
5. Execution resumes with the value

## Combining Dependencies

Mix dependency types as needed:

```python
@agent.toolify(description='Process with all inputs')
@depends_on_tool(fetch_data, arg_name='data')
@depends_on_private_data(data_key='api_key', arg_name='key')
@depends_on_interrupt(
    arg_name='confirm',
    input_handler=default_terminal_interrupt,
    prompt='Proceed? (y/n): ',
)
def full_process(data: dict, key: str, confirm: str) -> dict:
    if confirm.lower() != 'y':
        return {'status': 'cancelled'}
    return {'status': 'processed', 'records': len(data['records'])}
```

## Decorator Order

Decorators can be stacked in any order:

```python
# Order 1: toolify first
@agent.toolify()
@depends_on_tool(other, 'result')
def my_tool(result: dict) -> dict: ...

# Order 2: toolify last
@depends_on_tool(other, 'result')
@agent.toolify()
def my_tool(result: dict) -> dict: ...
```

Both work correctly. The SDK handles registration regardless of order.

## Validation

### Argument Validation

The decorator validates that `arg_name` exists in the function signature:

```python
@agent.toolify()
@depends_on_tool(other, arg_name='wrong_name')  # ValueError!
def my_tool(correct_name: dict) -> dict: ...
```

Error:

```
ValueError: Argument 'wrong_name' specified in dependency decorator
not found in function 'my_tool' signature
```

### Circular Dependencies

Circular dependencies are detected and prevented:

```python
@agent.toolify()
@depends_on_tool(tool_b, 'b')
def tool_a(b: dict) -> dict: ...

@agent.toolify()
@depends_on_tool(tool_a, 'a')  # Creates cycle!
def tool_b(a: dict) -> dict: ...
```

## Execution Behavior

### Parallel Execution

Independent tools run in parallel:

```python
@agent.toolify()
def tool_a() -> dict: ...  # Takes 2 seconds

@agent.toolify()
def tool_b() -> dict: ...  # Takes 3 seconds

@agent.toolify()
@depends_on_tool(tool_a, 'a')
@depends_on_tool(tool_b, 'b')
def final(a: dict, b: dict) -> dict: ...

# Total time: ~3 seconds (not 5)
# tool_a and tool_b run in parallel
```

### Sequential When Required

Dependencies enforce ordering:

```python
@agent.toolify()
def step_1() -> dict: ...  # Takes 2 seconds

@agent.toolify()
@depends_on_tool(step_1, 'result')
def step_2(result: dict) -> dict: ...  # Takes 2 seconds

# Total time: ~4 seconds
# step_2 waits for step_1
```

## Best Practices

### 1. Use Clear Argument Names

```python
# Good
@depends_on_tool(fetch_users, arg_name='user_data')

# Less clear
@depends_on_tool(fetch_users, arg_name='x')
```

### 2. Document Data Flow

Add comments for complex dependency graphs:

```python
# Data flow:
# fetch_metrics -> calculate_stats
#              \-> generate_charts
# Both feed into -> final_report

@agent.toolify()
@depends_on_tool(calculate_stats, 'stats')
@depends_on_tool(generate_charts, 'charts')
def final_report(stats: dict, charts: dict) -> dict: ...
```

## See Also

- [Decorators Reference](../api/decorators.md) - API details
- [Private Data Guide](private-data.md) - Security model
- [Multi-Agent Guide](multi-agent.md) - Agent dependencies
