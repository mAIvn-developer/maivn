# Interrupts (Human-in-the-Loop)

When a tool needs information that only the user can supply at runtime —
a name, a confirmation, a preference — declare it with
`@depends_on_interrupt`. The runtime pauses execution, collects the input
via your input handler, and resumes with the value.

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
from maivn import depends_on_interrupt, depends_on_tool

@agent.toolify(name='greet_user')
@depends_on_interrupt(arg_name='user_name', prompt='Your name: ')
def greet_user(user_name: str) -> dict:
    return {'user_name': user_name, 'greeting': f'Hey {user_name}!'}

@agent.toolify(name='personalize_profile')
@depends_on_interrupt(arg_name='favorite_color', prompt='Favorite color: ')
def personalize_profile(favorite_color: str) -> dict:
    return {'profile': {'favorite_color': favorite_color}}

@agent.toolify(name='confirm_action')
@depends_on_interrupt(arg_name='confirmation_input', prompt='Proceed? (yes/no): ')
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
custom handler that pushes the prompt to the UI and waits for a response:

```python
import asyncio

async def web_input_handler(prompt: str, *, session_id: str) -> str:
    """Push the prompt over a websocket and await the user's reply."""
    await send_to_browser({'type': 'prompt', 'session_id': session_id, 'text': prompt})
    return await wait_for_user_reply(session_id)

@depends_on_interrupt(
    arg_name='confirmation',
    prompt='Approve the destructive action?',
    input_handler=web_input_handler,
)
@agent.toolify(name='request_approval')
def request_approval(confirmation: str) -> dict:
    return {'approved': confirmation.lower() in ('yes', 'y', 'approve')}
```

The handler can be sync or async. Sync handlers block the worker thread
until they return; async handlers integrate cleanly with web frameworks.

## Combining interrupts with structured output

Interrupts can supply values to a Pydantic final tool — useful for "form-fill"
style flows where the user fills in missing fields:

```python
from pydantic import BaseModel

@agent.toolify(name='submit_order', final_tool=True)
@depends_on_interrupt(arg_name='shipping_address', prompt='Shipping address: ')
@depends_on_interrupt(arg_name='delivery_instructions', prompt='Delivery instructions (optional): ')
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

async def cancellable_handler(prompt: str, *, session_id: str) -> str:
    try:
        return await wait_for_user_reply_with_timeout(session_id, seconds=120)
    except TimeoutError:
        raise InterruptCancelled('user did not respond')
```

## What's next

- **[Agents & Tools](./agents-and-tools.md)** — combining interrupts with
  `before_execute` / `after_execute` hooks for fine-grained progress
  signaling.
- **[Private Data](./private-data.md)** — when the user-supplied value is
  sensitive (use `RedactedMessage` or `known_pii_values`).
- **[mAIvn Studio guide](../guides/maivn-studio.md)** — the Studio UI
  handles interrupt prompts inline in the chat panel.
