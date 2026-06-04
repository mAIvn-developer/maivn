# Interrupts & Human-in-the-Loop

Some turns can't finish on their own. An agent drafting a refund, booking travel,
or completing a form eventually hits a point where it needs something only a
person can give: an approval, a missing detail, a yes-or-no. An **interrupt** is
how mAIvn pauses a turn at exactly that point, hands control back to your
application to collect the answer, and then resumes the same turn with the value
filled in.

This guide covers the full loop: declaring the interrupt on a tool, detecting an
interrupted turn from the response, collecting the human input and resuming, and
keeping any sensitive answer redacted across the pause.

## What this is, and why it matters

Think of an interrupt as a tool argument the agent can't supply on its own.
Most arguments come from the model's reasoning over the conversation. An
interrupt argument comes from a human, at the moment the tool is about to run.

That single idea unlocks the patterns people usually mean by "human-in-the-loop":

- **Approval gates** — pause before a destructive or costly action and wait for a
  "yes" before proceeding.
- **Form-fill** — let the agent gather what it can, then ask the user for the
  fields it can't infer (a shipping address, a date, a preference).
- **Disambiguation** — when the request is underspecified, ask one precise
  question instead of guessing.

The point is that the turn doesn't fail and doesn't restart from scratch. It
pauses, collects the missing value, and continues — so the agent keeps all the
context it had built up to that moment.

## Declaring an interrupt with `@depends_on_interrupt`

You declare an interrupt the same way you declare any other dependency: decorate
the tool and bind the human-supplied value to one of its arguments. `@depends_on_interrupt`
takes the `arg_name` to fill, an `input_handler` that knows how to collect the
value, and an optional `prompt` to show the user.

```python
from maivn import Agent, default_terminal_interrupt, depends_on_interrupt

agent = Agent(name='profile_agent', api_key='...')


@agent.toolify(description='Greet the user by name')
@depends_on_interrupt(
    arg_name='name',
    input_handler=default_terminal_interrupt,
    prompt='Please enter your name: ',
)
def greet(name: str) -> dict:
    return {'greeting': f'Hello, {name}!'}
```

When the agent decides to call `greet`, the runtime stops just before execution,
calls your `input_handler` with the `prompt`, takes whatever the handler returns,
and passes it in as `name`. `default_terminal_interrupt` is the built-in handler
for local scripts and CLIs — it prints the prompt to stdout and reads one line
from stdin.

> **Decoration-time validation.** `arg_name` must match a real parameter on the
> decorated function (or a field on a Pydantic final tool). If it doesn't, the
> decorator raises `ValueError` at import time — a programming error you fix by
> correcting the name, not something you catch at runtime.

### Custom input handlers

`default_terminal_interrupt` is only the simplest case. An `input_handler` is any
callable that takes the `prompt` string and returns the user's value, so in a real
application you swap in a handler that talks to your UI. If your handler does its
own prompting, you can omit `prompt`:

```python
def pick_file(prompt: str) -> str:
    # Opens your app's file dialog; it has its own UI, so the prompt is unused.
    return show_file_dialog()


@agent.toolify(description='Process the chosen file')
@depends_on_interrupt(arg_name='file_path', input_handler=pick_file)
def process_file(file_path: str) -> dict:
    return {'processed': file_path}
```

A handler that declares an `input_type` and/or `choices` parameter also receives
the interrupt's input type and choices alongside the prompt, so a single handler
can render the right control (a yes/no toggle, a picker, a text box) for whatever
tool triggered it:

```python
def routed_handler(prompt: str, input_type: str = 'text', choices: list[str] | None = None) -> str:
    if input_type == 'choice' and choices:
        return show_choice_dialog(prompt, choices)
    if input_type == 'boolean':
        return show_yes_no(prompt)
    return show_text_input(prompt)


@agent.toolify(description='Request approval before a destructive action')
@depends_on_interrupt(
    arg_name='confirmation',
    input_handler=routed_handler,
    prompt='Approve the destructive action?',
)
def request_approval(confirmation: str) -> dict:
    return {'approved': confirmation.strip().lower() in ('yes', 'y', 'approve')}
```

To reach session-specific state (a websocket, a per-session future) from inside a
handler that only receives the prompt, close over it. A small factory that returns
the handler keeps the prompt-only call signature the runtime expects while giving
the body access to whatever it needs:

```python
def make_handler(session_id: str):
    def handler(prompt: str) -> str:
        push_prompt_to_frontend(session_id, prompt)
        return wait_for_user_reply(session_id)

    return handler
```

### Typed prompts

`@depends_on_interrupt` reads the decorated argument's type annotation to infer
how the input should be collected. A `bool` argument becomes a yes/no prompt, an
`int` or `float` becomes a number prompt, and a `Literal[...]` becomes a choice
prompt over those values. You can also set `input_type` and `choices` explicitly
when you want to override the inferred shape. This metadata travels with the
interrupt so a rich UI can render the right control instead of a plain text box.

## Detecting an interrupted turn

There are two ways to learn that a turn paused for input, and which one you use
depends on how you collect the answer.

**If your `input_handler` collects the value in-process** (a terminal prompt, a
GUI dialog, a blocking wait on a reply), the pause and resume happen inside the
single `invoke` call. The handler returns, the tool runs, and you get back a normal
completed `SessionResponse`. You don't have to detect anything — the loop closed
itself.

**If your application collects the value out-of-band** — the turn returns to your
backend, your frontend asks the user, and a later request supplies the answer —
then the turn comes back in an interrupted state. Read `SessionResponse.status`:

```python
response = agent.invoke([HumanMessage(content='Submit my order.')])

if response.status == 'interrupted':
    # The turn paused waiting on human input. Surface the prompt to the user
    # and resume once you have their answer.
    ...
else:
    print(response.response)
```

`status` is a small fixed vocabulary — `"pending"`, `"running"`,
`"waiting_for_tools"`, `"completed"`, `"failed"`, and `"interrupted"`. Only
`"interrupted"` means "I need a human before I can continue."

`SessionResponse` is a public model you can import from the shared package:

```python
from maivn_shared import SessionResponse
```

### Seeing the interrupt on the event stream

If you stream a turn instead of awaiting a single response, the pause shows up as
an event on the same stream as everything else. After normalizing the stream into
the public `AppEvent` contract, an interrupt event populates the `interrupt`
descriptor:

```python
from maivn.events import normalize_stream
from maivn.messages import HumanMessage

for event in normalize_stream(agent.stream([HumanMessage(content='Submit my order.')])):
    if event.interrupt is not None:
        i = event.interrupt
        # Render i.prompt to the user. i.arg_name / i.tool_name say what is being
        # asked for; i.input_type and i.choices say how to render the control;
        # i.number / i.total position it within a multi-step sequence.
        show_prompt(i.prompt, choices=i.choices)
```

The `InterruptDescriptor` carries the `prompt`, the `arg_name` and `tool_name`
being filled, the `input_type` and `choices` for rendering, and `number` / `total`
when several interrupts fire in one turn. This is the path to use when a frontend —
a browser, a mobile app, or [mAIvn Studio](maivn-studio.md) — renders the prompt
inline rather than blocking a Python thread. See [Frontend Events](frontend-events.md)
for the full event surface and the `EventBridge` that forwards events to a browser.

## Collecting human input and resuming the session

The simplest way to collect and resume is to put the waiting *inside* your
`input_handler`. The handler is called with the prompt at the moment the tool is
about to run; it blocks until your application produces the user's value, then
returns it, and the tool runs with that value filled in. As above, reach any
session-specific state — a per-request reply queue, a websocket — through a
closure, since the handler only receives the prompt:

```python
import queue

# A place to park each session's pending reply until the frontend posts it back.
_pending: dict[str, queue.Queue[str]] = {}


def make_reply_handler(session_id: str):
    """Build a prompt-only handler that blocks until the frontend answers."""

    def handler(prompt: str) -> str:
        replies: queue.Queue[str] = _pending.setdefault(session_id, queue.Queue())
        push_prompt_to_frontend(session_id, prompt)
        return replies.get()  # blocks until resume_session() puts the answer

    return handler


def resume_session(session_id: str, answer: str) -> None:
    """Called by your endpoint when the user submits their answer."""
    _pending.setdefault(session_id, queue.Queue()).put(answer)
```

Bind the handler to the tool and run the turn. The tool's execution pauses at the
interrupt, your UI shows the prompt, and the moment `resume_session` delivers the
answer, the handler returns and the turn continues to completion:

```python
session_id = 'order-4821'


@agent.toolify(description='Confirm before charging the card')
@depends_on_interrupt(
    arg_name='confirmation',
    input_handler=make_reply_handler(session_id),
    prompt='Confirm the charge? (yes/no)',
)
def confirm_charge(confirmation: str, amount: float) -> dict:
    if confirmation.strip().lower() not in ('yes', 'y'):
        return {'charged': False, 'reason': 'declined by user'}
    return {'charged': True, 'amount': amount}


response = agent.invoke([HumanMessage(content='Pay invoice 4821.')])
print(response.response)  # produced after the human confirmed
```

> The handler is invoked with the `prompt` (and, if it declares them, `input_type`
> and `choices`). It does not receive any other arguments from the runtime, so keep
> the signature to those parameters and pull anything else in through a closure.

### Ordering multiple interrupts

When several tools each collect input, the agent decides the order it calls them.
To make prompts appear in a deterministic sequence — and to gate a final step on
all of them — chain the tools with `@depends_on_tool` and guide the order in the
system prompt. See the [Dependencies guide](dependencies.md) for the full pattern;
the short version is that a final tool depending on each input-collecting tool
forces them to resolve first. The [Interrupts examples](../examples/interrupts.md)
walk through a complete runnable ordered-collection flow.

### Cancelling an interrupt

A pending interrupt isn't open-ended. If the user navigates away, a timeout
elapses, or the prompt otherwise can't be answered, raise an exception from inside
your `input_handler`. The runtime treats that the same as any other tool failure:
the value is never filled in, the tool's after-hook fires with the error
populated, and the agent sees the failure on its next step (so it can apologize,
retry, or take a different path) rather than hanging on a prompt no one will
answer. Define a clear exception type and convert your handler's own
failure modes — a timeout, a closed connection — into it. See the
[Interrupts examples](../examples/interrupts.md) for a runnable timeout-driven
cancellation handler.

## How `RedactedMessage` keeps secrets safe across the pause

Some of what a human types in response to a prompt is sensitive: an account
number, an email, a one-time code. An interrupt collects that value at execution
time, but the conversation it joins may be replayed to the model on later steps —
so the raw value needs to stay out of the model's view.

`RedactedMessage` is the public message type that handles this. Instead of sending
the user's text as a plain `HumanMessage`, wrap it so the runtime detects and
masks sensitive spans before the content ever reaches the model:

```python
from maivn.messages import RedactedMessage

response = agent.invoke([
    RedactedMessage(content='My account number is 1234-5678-9012'),
])
```

What happens across the pause/resume boundary:

1. The runtime scans the message content for PII and replaces detected values with
   placeholders before anything reaches the model.
2. The real values are held inside the session's private data — within the
   runtime, never surfaced to the model.
3. When a tool genuinely needs the real value, it's injected at execution time,
   the same way [private data](private-data.md) is injected — so the value is used
   without ever being planned against or echoed back.

When you already know which values in a reply are sensitive, declare them up front
with `known_pii_values`. You can mix raw strings and `PrivateData` descriptors so
the detected spans get stable keys and labels:

```python
from maivn.messages import PrivateData, RedactedMessage

reply = RedactedMessage(
    content=user_answer,
    known_pii_values=[
        PrivateData(value=user_answer, name='otp_code', label='One-time code'),
    ],
)
response = agent.invoke([reply])
```

The net effect: a human can hand the agent a secret mid-turn, the turn completes
using it, and the secret never appears in the model context, in logs, or — when
you forward events through a `frontend_safe` bridge — on the wire to a browser.
The redaction model behind all of this is covered in full in the
[Private Data guide](private-data.md).

## A worked end-to-end example

This example ties the loop together: an order-submission turn where the model
supplies what it can reason about and the user fills in the rest, with the
sensitive field redacted across the pause. It uses a Pydantic final tool so the
turn returns a validated, typed result.

```python
from pydantic import BaseModel

from maivn import Agent, depends_on_interrupt
from maivn.messages import HumanMessage, PrivateData, RedactedMessage

agent = Agent(
    name='checkout_agent',
    system_prompt='Help the user place an order. Ask for any details you cannot infer.',
    api_key='...',
)


# The frontend posts the answer back here; the handler blocks until it does.
def ask_user(prompt: str) -> str:
    push_prompt_to_frontend(prompt)
    return wait_for_user_reply()


@agent.toolify(name='submit_order', final_tool=True)
@depends_on_interrupt(
    arg_name='shipping_address',
    input_handler=ask_user,
    prompt='What is the shipping address?',
)
@depends_on_interrupt(
    arg_name='card_last_four',
    input_handler=ask_user,
    prompt='Last four digits of the card to charge?',
)
class OrderSubmission(BaseModel):
    item_id: str          # inferred by the model from the conversation
    quantity: int         # inferred by the model from the conversation
    shipping_address: str  # collected from the user at execution time
    card_last_four: str    # collected from the user, and treated as sensitive


def run_checkout() -> None:
    response = agent.invoke([HumanMessage(content='Order 2 of item SKU-42.')])

    # If your handler resolves in-process (as above), the turn completes here.
    if response.status == 'interrupted':
        # Out-of-band collection path: surface the prompt and resume later.
        return

    order: OrderSubmission = response.result  # validated final-tool object
    print('Placed order:', order.item_id, 'x', order.quantity)
```

If the user volunteers a sensitive value in plain conversation rather than through
a prompt, wrap that turn's message so it is redacted before it reaches the model:

```python
agent.invoke([
    RedactedMessage(
        content='Charge the card ending 4242, ship to 500 Main St.',
        known_pii_values=[
            PrivateData(value='4242', name='card_last_four', label='Card last four'),
        ],
    ),
])
```

`item_id` and `quantity` come from the model's reasoning; `shipping_address` and
`card_last_four` come from the human at execution time; and the card detail never
enters the model context. That is the whole human-in-the-loop pattern in one
turn — collect, redact, resume.

## Next steps

- [Interrupts examples](../examples/interrupts.md) — runnable walkthroughs:
  terminal prompts, ordered multi-input collection, custom and async handlers,
  and timeout-driven cancellation.
- [Dependencies](dependencies.md) — interrupts are one of four dependency types;
  see how to chain them and order multi-step input collection.
- [Private Data](private-data.md) — the redaction model behind `RedactedMessage`,
  `known_pii_values`, and `frontend_safe` event bridges.
- [Sessions, Invocation, and Streaming](sessions-and-streaming.md) — `invoke` /
  `stream`, the `SessionResponse` shape, and the `status` field.
- [Frontend Events](frontend-events.md) — forward interrupt prompts to a browser
  via `normalize_stream` and `EventBridge` (including `mount_events` for FastAPI).
- [Structured Output](structured-output.md) — the `final_tool` and
  `structured_output` patterns used in the worked example.
- [Decorators Reference](../api/decorators.md) — the full `@depends_on_interrupt`
  signature and the other dependency decorators.
- [Core Concepts](../core-concepts.md) and the [Overview](../overview.md) — where
  interrupts sit in the wider mAIvn model.
