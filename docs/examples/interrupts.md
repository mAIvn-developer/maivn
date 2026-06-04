# Interrupts (Human-in-the-Loop)

These examples pause a tool to collect input from the user mid-run — a name,
a confirmation, a preference — then resume with the value.

Declare the dependency with `@depends_on_interrupt`: the runtime pauses
execution, collects the input via your input handler, and resumes.
`@depends_on_interrupt` takes `arg_name` and an `input_handler` (the callable
that actually collects the value); `prompt` is optional. The SDK ships
`default_terminal_interrupt` for stdin-based collection.

## Terminal interrupt — the simplest case

```python
from maivn import Agent, default_terminal_interrupt, depends_on_interrupt

agent = Agent(
    name='Interactive Profile Agent',
    system_prompt='Collect user information interactively and build a profile.',
    api_key='...',
)

@depends_on_interrupt(
    arg_name='user_name',
    prompt='Please enter your name: ',
    input_handler=default_terminal_interrupt,
)
@agent.toolify(name='greet_user')
def greet_user(user_name: str, greeting_style: str = 'casual') -> dict:
    greetings = {
        'formal': f'Good day, {user_name}.',
        'casual': f'Hey {user_name}!',
        'enthusiastic': f'Hello {user_name}!!!',
    }
    return {'greeting': greetings[greeting_style], 'user_name': user_name}
```

The runtime calls `default_terminal_interrupt(prompt)`, prints the prompt
to stdout, reads a line from stdin, and passes it in as `user_name`.

## Chaining interrupts with tool dependencies

A common pattern: collect several inputs in order, then combine them in a
final tool. Use tool dependencies so each step has a clear handoff:

```python
from maivn import default_terminal_interrupt, depends_on_interrupt, depends_on_tool

@agent.toolify(name='greet_user')
@depends_on_interrupt(arg_name='user_name', input_handler=default_terminal_interrupt, prompt='Your name: ')
def greet_user(user_name: str) -> dict:
    return {'user_name': user_name, 'greeting': f'Hey {user_name}!'}

@agent.toolify(name='personalize_profile')
@depends_on_interrupt(arg_name='favorite_color', input_handler=default_terminal_interrupt, prompt='Favorite color: ')
def personalize_profile(favorite_color: str) -> dict:
    return {'profile': {'favorite_color': favorite_color}}

@agent.toolify(name='confirm_action')
@depends_on_interrupt(arg_name='confirmation_input', input_handler=default_terminal_interrupt, prompt='Proceed? (yes/no): ')
def confirm_action(confirmation_input: str, action_name: str) -> dict:
    confirmed = confirmation_input.lower().strip() in ('yes', 'y')
    return {'action_name': action_name, 'confirmed': confirmed}

@agent.toolify(name='create_summary', final_tool=True)
@depends_on_tool(tool_ref='greet_user', arg_name='greeting_result')
@depends_on_tool(tool_ref='personalize_profile', arg_name='profile_result')
@depends_on_tool(tool_ref='confirm_action', arg_name='confirmation_result')
def create_summary(
    greeting_result: dict,
    profile_result: dict,
    confirmation_result: dict,
) -> dict:
    return {
        'user_name': greeting_result['user_name'],
        'favorite_color': profile_result['profile']['favorite_color'],
        'action_confirmed': confirmation_result['confirmed'],
    }
```

The system prompt should guide the agent to call them in the right order
and to handle the confirmation loop ("if the user says no, restart"):

```python
agent.system_prompt = (
    'Tools that collect user input (greet_user, personalize_profile, '
    'confirm_action) must execute in order so prompts appear sequentially. '
    'After confirm_action: if the user responds "no", repeat the sequence '
    'from greet_user. Once confirmed, call create_summary with all values.'
)
```

## Custom input handlers

`default_terminal_interrupt` reads from stdin. In a web app, you'll want a
custom handler that pushes the prompt to the UI and waits for a response. An
input handler receives a single positional argument — the prompt string — and
returns the collected value, so wire any per-session state in via a closure:

```python
def make_web_input_handler(session_id: str):
    """Build a handler bound to a specific session via closure."""

    def web_input_handler(prompt: str) -> str:
        send_to_browser({'type': 'prompt', 'session_id': session_id, 'text': prompt})
        return wait_for_user_reply(session_id)

    return web_input_handler

@depends_on_interrupt(
    arg_name='confirmation',
    input_handler=make_web_input_handler(session_id='sess-123'),
    prompt='Approve the destructive action?',
)
@agent.toolify(name='request_approval')
def request_approval(confirmation: str) -> dict:
    return {'approved': confirmation.lower() in ('yes', 'y', 'approve')}
```

The handler blocks the worker thread until it returns, so any waiting (e.g.
polling for the user's reply) happens inside the handler.

## Combining interrupts with structured output

Interrupts can supply values to a Pydantic final tool — useful for "form-fill"
style flows where the user fills in missing fields:

```python
from pydantic import BaseModel

@agent.toolify(name='submit_order', final_tool=True)
@depends_on_interrupt(arg_name='shipping_address', input_handler=default_terminal_interrupt, prompt='Shipping address: ')
@depends_on_interrupt(arg_name='delivery_instructions', input_handler=default_terminal_interrupt, prompt='Delivery instructions (optional): ')
class OrderSubmission(BaseModel):
    item_id: str
    quantity: int
    shipping_address: str
    delivery_instructions: str = ''
```

`item_id` and `quantity` come from the agent's reasoning over the
conversation; `shipping_address` and `delivery_instructions` come from the
user, collected at execution time.

## Stoppable interrupts

If you need to cancel an in-flight interrupt (e.g. user navigates away),
have your input handler raise an exception. The runtime treats it as a
tool failure — the after-hook fires with `error` populated and the agent
sees the failure in its next turn:

```python
class InterruptCancelled(Exception):
    pass

def make_cancellable_handler(session_id: str):
    def cancellable_handler(prompt: str) -> str:
        try:
            return wait_for_user_reply_with_timeout(session_id, seconds=120)
        except TimeoutError:
            raise InterruptCancelled('user did not respond')

    return cancellable_handler
```

## What's next

- **[Interrupts guide](../guides/interrupts.md)** — the conceptual reference:
  how interrupts pause and resume a turn, detecting an interrupted turn from
  `SessionResponse.status` or the event stream, typed prompts, cancellation, and
  keeping a sensitive answer redacted across the pause.
- **[Agents & Tools](./agents-and-tools.md)** — combining interrupts with
  `before_execute` / `after_execute` hooks for fine-grained progress
  signaling.
- **[Private Data](./private-data.md)** — when the user-supplied value is
  sensitive (use `RedactedMessage` or `known_pii_values`).
- **[mAIvn Studio guide](../guides/maivn-studio.md)** — the Studio UI
  handles interrupt prompts inline in the chat panel.
