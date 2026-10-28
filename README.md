# maivn

Python SDK for mAIvn agents, swarms, tools, structured output, conversations,
triggers, resources, artifacts, and categorical decisions.

## Install

```bash
pip install maivn
```

Set `MAIVN_API_KEY` in the environment or pass `api_key=` to `Agent`, `Swarm`,
or `Client`. `MAIVN_BASE_URL` overrides the SDK's default API endpoint.

```python
from maivn import Agent

assistant = Agent(name='Assistant')
response = assistant.invoke('Summarize the next action in one sentence.')
print(response.response)
```

## Documentation

The public documentation lives in its own repository, **maivn-docs**, checked out
as `docs/` in the platform superproject. It is not part of this package and is
not shipped in the source distribution, so a documentation fix never requires a
release of this SDK.

Runnable public examples live in the **maivn-examples** repository. They cover
typed tools, structured output, conversations, swarms, privacy, human input,
triggers, and generated files.

## Encrypted local storage

The SDK includes the native vault package. On a supported desktop with an
available OS credential store, the SDK saves caller-supplied private thread
values automatically. It creates the vault when those values are first used,
not during installation. Keep the same account, API base address and thread
ID to resume them after restarting your application. Rotating an API key for
the same user, organization and project keeps access to the same vault.

### Servers and containers

Configure two values in your hosting provider:

- `MAIVN_VAULT_DIRECTORY`: an absolute path on durable storage.
- `MAIVN_VAULT_KEY`: a base64-encoded, randomly generated 32-byte key,
  injected from your secret manager.

Create the key once and retain it across deployments. Do not generate a new
key at startup or substitute your mAIvn API key. Ordinary `Client()` construction
then uses these settings. Your provider handles secret delivery. The SDK does
not create cloud resources or configure a secret manager for you.

If your application already retrieves secrets, pass a callable that returns
the 32 key bytes using
`Client(local_vault=LocalVaultConfig(directory=path, key_provider=load_key))`.
Import both `Client` and `LocalVaultConfig` from `maivn`. The SDK calls the
provider when it opens the vault.

Serverless temporary storage loses data between instances. Use a persistent
filesystem with working file locks and atomic replacement. An object-store
URL is not a vault directory. Qualify shared filesystems before running
multiple writers against them.

### Recovery and deletion

Back up encrypted files and retain their matching key. Replacing a missing
key cannot recover existing data. Desktop credential entries are tied to the
vault's location, so copying the files to another path or machine alone does
not restore access. Server deployments must preserve both their durable
storage and secret-manager key.

If secure storage cannot open, the SDK raises `VaultSetupError` when private
storage is needed. It does not switch to plaintext or temporary storage.

Call `client.forget_private_data(thread_id)` to delete saved caller values
for one thread. Async applications can await `client.aforget_private_data(thread_id)`.
These methods do not delete server-held data or uploaded documents.
Explicit `Client(private_data_store=None)` disables local persistence.
You can also supply your own `PrivateDataStore` implementation.
Set `Client(restore_private_values=False)` to receive placeholder-only output instead of resolved caller values.

This automatic store holds caller-declared placeholder values. Private file
uploads and document retrieval retain their separate API and authorization
requirements.


## Shielded markers in tool arguments

Pass shielded markers through exactly as you receive them. They can be
resolved inside SDK-local tool arguments, including prose in a declared string
field, when their values are available in the current private-data custody.
The platform fills the keys it allocated; the caller's SDK process fills the
keys it declared itself. This also applies to final tools. A final-tool declaration
does not grant access to additional private data.

Markers are not a guarantee that a value is still available. Unknown or stale
server keys fail with `private_placeholder_unresolvable`. Reacquire the source
through an authorized tool or report that the data is unavailable. Do not
invent a replacement or repeat the unchanged call. Hosted and external tools
cannot receive vault-held plaintext through this SDK-local resolution path.
Response text may retain a marker for display when its value is unavailable;
that does not make the marker executable tool data.

## Decisions

`maivn.decisions` answers categorical questions through the mAIvn platform and
falls back to your own baseline when a decision is unavailable, refused or not
confident enough. The SDK never calls a decision model directly: requests go to
`POST /v1/decisions` with the same API key and `MAIVN_BASE_URL` as every other
call. The platform chooses the decision model, shields explicitly marked private values
and bills the call to your project.

```python
from maivn.decisions import (
    DecisionAnswer, DecisionChoice, DecisionClient, DecisionConfig, DecisionRequest,
)

question = DecisionRequest(
    name='urgent',
    state='Public bulletin: planned API maintenance on Saturday.',
    instructions='Does this bulletin need immediate action?',
    privacy='public',
    choices=(
        DecisionChoice(key='yes', description='Immediate action'),
        DecisionChoice(key='no', description='No immediate action'),
    ),
)


async def baseline(request: DecisionRequest) -> DecisionAnswer:
    return DecisionAnswer(value='yes', provider='local', token_category=None)


decisions = DecisionClient(
    fallback=baseline,
    config=DecisionConfig(min_confidence=0.8),
    authorize=lambda request: request == question,
)
result = decisions.decide(question)
print(result.value, result.source, result.primary.status)
```

Only requests declared `privacy='public'` and approved by `authorize` are sent.
Mark private values inside otherwise public text with
`DecisionRequest(..., private_data={'customer_name': 'Priya Raman'})`: the
platform replaces them with placeholders before the decision model sees
anything. A placeholder verdict is resolved locally and must still match a declared
choice. Raw marked values reach the platform shield, never the decision model.
Plain unmarked text receives no automatic redaction. Your fallback receives the
original request and must enforce your application's privacy policy.
Pass `client=Client(...)` to reuse an existing client's settings.

## Decision token usage

Decision workflows report two separate categories: `decision` for platform
decisions and `llm` for ordinary model invocations, including model fallbacks.
Use the additive `token_usage_by_category` property on a decision result,
builder outcome, argument preparation, matching result, or decorated call.
For a list of decisions, use:

```python
from maivn.decisions import summarize_decision_usage

usage = summarize_decision_usage([result])
print(usage.decision.input_tokens)
print(usage.llm.input_tokens)
```

Native batch followers do not count the shared receipt again. Missing counts
remain `None`; `known_input_tokens` and `known_output_tokens` retain the observed
subtotal. A skipped agent invocation contributes zero invocation tokens while
its decision receipts remain visible. Declared local fallbacks use
`DecisionAnswer(..., token_category=None)`; a decision-model backup declares
`token_category='decision'`. Ordinary model fallbacks default to `llm`.

These counts are not interchangeable billing units. LLM invocation counts
mirror the existing public SDK input/output surface; provider totals and cache
counts remain available through `provider_usage`. Price each capability with
its applicable provider tariff and retain costs separately. The existing
`token_usage` property keeps its original behavior.


## Decision reference and workflows

All symbols below are imported from `maivn.decisions`. These workflows are opt-in;
creating a request, builder or decorator performs no inference. Synchronous
execution methods run outside an active event loop; use their async counterparts
inside async applications. The examples below reuse `question`, `baseline` and
`decisions` from the Decisions example. They make platform calls when executed
unless a feature is explicitly disabled.

### Requests, routing and results

| Public type | Contract |
| --- | --- |
| `DecisionChoice` | `key` (1–128 characters, containing a non-whitespace character), `description` (1–2,048 characters). |
| `DecisionRequest` | `name` uses the choice-key bounds; `state` is 1–16,384 characters; `instructions` is 1–4,096 characters; `choices` is a tuple of 2–32 choices with unique keys. `privacy` is `public`, `private` or `unknown` (default). |
| `DecisionConfig` | `min_confidence=0.8` (0–1), `timeout_seconds=5.0` (positive finite), `enabled=True`, `max_batch_questions=32` (1–128). Timeout bounds the primary attempt, not your fallback. |
| `DecisionUsage` | Optional nonnegative `input_tokens` and `output_tokens`; missing values mean unknown. |
| `DecisionAnswer` | Fallback `value`, `provider`, optional `model` and `usage`; `token_category='llm'` by default. Use `None` for local code with no model call, which cannot report model usage. |
| `DecisionPrediction` | An answer with confidence (0–1); requires `token_category='decision'`. Returned primary metadata identifies the model actually served, rather than configuring a model. |
| `DecisionStatus` | `accepted`, `blocked`, `disabled`, `timeout`, `unavailable`, `invalid`, `low_confidence`. |
| `DecisionAttempt` | Status, latency in seconds, optional provider/model/confidence/usage, `token_category`, `usage_scope` (`individual` or `batch`), optional `usage_batch_size`, and `usage_owner`. |
| `DecisionResult` | Final `value`, `source` (`primary` or `fallback`), `primary` attempt and optional `fallback` attempt; `token_usage_by_category` keeps both receipts. |

Request and answer models are strict, reject extra fields and are immutable.
`private_data` accepts a mapping or tuple of key/value pairs: up to 64 unique
keys, each following the choice-key bounds, with string values of 1–4,096
characters. Mappings become sorted immutable pairs; `request.private_values`
returns a detached dictionary.

`DecisionClient(fallback=..., config=..., authorize=..., client=None)` requires
an async fallback returning `DecisionAnswer` and a synchronous authorization
callback. Only an exact `True` approves a complete request. Authorization errors,
private/unknown privacy, or a disabled client prevent primary dispatch. Approval
is a local export permission, not a substitute for declaring private values.
No vendor key or decision-model identifier is needed. An existing `Client` can
supply the authenticated platform transport.

Use `decide(request)` / `await adecide(request)` for one question. Confidence below
the threshold, refusal, malformed or out-of-choice answers, timeout and platform
failure use the fallback at most once per question. Its returned value must be a
declared choice. Invalid fallback answers raise `ValueError`; fallback exceptions
propagate. Cancellation propagates rather than starting another route. Request
validation errors also propagate. Your fallback is not subject to the primary
attempt's timeout.

`with_fallback(callback)` creates a client sharing the route, authorization and
configuration with a different fallback. The original client is unchanged.

### Ordered and native batches

```python
batch_requests = (question, question.model_copy(update={'name': 'urgent_again'}))
batch_client = DecisionClient(
    fallback=baseline, config=DecisionConfig(),
    authorize=lambda request: request in batch_requests,
)
results = batch_client.batch(batch_requests, max_concurrency=2, native_batch=True)
print([result.source for result in results])
```

`batch` / `abatch` return results in input order. `max_concurrency=4` bounds
outstanding work by default and must be a positive integer. `native_batch=False`
runs scalar decisions; `True` combines questions with identical `state` and
`private_data`, splitting at `max_batch_questions`. `native_batch_question_limit`
reports that limit (one when the primary is disabled). Approve every full request
in your authorization policy. If any member of a shared packet is not approved, that packet falls back without dispatch. Private or
unknown requests fall back individually. On caller cancellation or failure,
outstanding batch tasks are cancelled and drained.

A native packet has one token receipt. The first member owns it; followers expose
`usage_scope='batch'`, the batch size and `usage_owner=False`, without duplicating
usage. Aggregate the whole result collection to retain the owned receipt.

### Before and after an invocation

`Agent.decide` and `Swarm.decide` return a `DecisionInvocationBuilder`. The same
operation composes with their structured-output, event and follow-up builders.
Stage names and question labels start with a letter and contain letters, digits,
underscores, dots or hyphens, up to 128 characters; stage names must be unique.

```python
from maivn import Agent

plan = (
    Agent(name='Bulletin assistant')
    .decide(
        'triage', client=decisions, questions={'urgent': question},
        require={'urgent': 'yes'},
    )
    .decide('review', questions={'urgent': question}, at='after')
)
outcome = plan.invoke('Summarize the public maintenance bulletin.')
print(outcome.status, outcome.skipped_by)
if outcome.response is not None:
    print(outcome.response.response)
```

Each `.decide(name, client=..., questions=..., at='before', require=None,
max_concurrency=4, native_batch=False)` stage has 1–32 questions. Subsequent
stages may omit `client` to reuse the previous stage's client. `DecisionQuestion`
values are requests or synchronous factories taking `DecisionContext`. Each
factory must explicitly construct its own request. Context contains the original `input`
(also `original_input`), `output` (absent before invocation), and prior nested
`decisions[stage][question]`. Context does not authorize exporting input or output.

A `require` gate skips invocation only when an accepted **primary** answer differs
from the required choice. Any fallback preserves normal invocation, even when
its value differs. After stages annotate the completed response and cannot have
`require`. `.map_input(mapper)` explicitly changes invocation input after before
stages; without it, decisions do not change messages.

`invoke` / `ainvoke` return `DecisionInvocationResult`: `status` is `completed` or
`skipped`, `response` is an `InvokeResponse` or `None`, `decisions` is the nested
result mapping, and `skipped_by` identifies a blocking stage. `batch` / `abatch`
accept an iterable of inputs and `max_concurrency=4`, preserve order and share a
bound across decisions and invocations. Invocation keyword arguments pass through
to the bound scope.

`.structured_output(Model)`, `.events(include=None, exclude=None, on_event=None,
auto_verbose=True)` and `.allow_followup_questions(**kwargs)` retain the decision
stages and configure the existing invocation wrappers.

`stream` / `astream` forward ordinary invocation events and add
`decision.stage.started`, `decision.stage.completed` and `decision.plan.completed`.
The final `DecisionStreamEvent.outcome` holds the typed result locally and is
excluded from serialized event data; read it before discarding the event.

```python
from maivn.decisions import DecisionStreamEvent

for event in plan.stream('Summarize the public maintenance bulletin.'):
    if isinstance(event, DecisionStreamEvent) and event.outcome is not None:
        print(event.outcome.status)
```

### Typed argument preparation

```python
from maivn.decisions import DecisionArgument, DecisionArgumentPreparer

prepared = DecisionArgumentPreparer(decisions).prepare([
    DecisionArgument(
        name='notify', question=question, values={'yes': True, 'no': False},
    ),
])
# Preserve the existing value when the optional decision falls back.
notify = prepared.values.get('notify', True)
print(notify, prepared.decisions['notify'].source)
```

`DecisionArgument(name, question, values)` maps **every** choice exactly once to a
boolean or string; its name must be a valid Python identifier, excluding keywords.
`DecisionArgumentPreparer.prepare` / `aprepare` accept unique named arguments,
`max_concurrency=4` and `native_batch=False`. `DecisionArgumentPreparation.values`
contains only accepted primary mappings; fallbacks leave the field absent so your
existing argument preparation can continue. `.decisions` retains every result.
It does not register a tool or automatically inject arguments into one.

### Function and local tool dependencies

```python
from maivn.decisions import (
    DecisionResults, DecisionStep, decision_flow, depends_on_decision,
)

@depends_on_decision(
    client=decisions,
    decisions=[DecisionStep(
        'triage', questions={'urgent': question}, require={'urgent': 'yes'},
    )],
    arg_name='decision_results',
)
def notify_bulletin(message: str, decision_results: DecisionResults) -> str:
    return message

call = decision_flow(notify_bulletin).invoke({'message': 'Maintenance bulletin'})
print(call.status, call.value)
```

`depends_on_decision(*, client, decisions, arg_name=None, max_concurrency=4,
state=None, native_batch=False)` declares metadata. Direct Python calls keep their
original behavior; use `decision_flow(function)` (a `DecisionFunction`) or register
the decorated function as an SDK-local tool to execute its dependencies.
A tool's injected decision parameter is hidden from its public input schema.

`DecisionStep(name, *, questions, after=(), require=None, client=None,
max_concurrency=4)` declares a stage. `after` names a predecessor or tuple of
predecessors within the same decorator group. Unknown predecessors and cycles
are rejected; ready steps can run concurrently. A step may override its client.
Factories receive a local snapshot of bound function arguments, including
defaults, through `DecisionContext.input`. The optional synchronous `state` mapper
narrows this snapshot once per factory stage; its result must support deep copying.
A factory explicitly chooses which content goes into its exported request before
authorization. Static requests need no input snapshot. `arg_name` injects nested
results into a parameter annotated exactly as `DecisionResults`. The caller must
not supply that hidden parameter.

Managed `invoke` / `ainvoke` accept a mapping of function arguments; `batch` /
`abatch` accept iterables of mappings with `max_concurrency=4`. `DecisionCallResult`
contains `status` (`completed` or `skipped`), the native return `value` (or `None`
when skipped), nested `decisions`, `skipped_by` and category token usage. Gates
apply the same accepted-primary rule as invocation builders. Fallbacks preserve
function execution; errors from the function propagate. `DecisionResults` is the
mapping type `stage -> question -> DecisionResult`.

### Optional candidate matching

Matching operates on an already authorized, retrieved candidate set. It neither
retrieves records nor grants access to them. It is disabled by default, with no
primary call while disabled.

```python
from maivn.decisions import (
    DecisionCandidateMatcher, DecisionMatchCandidate, DecisionMatchConfig,
    DecisionMatchRequest, candidate_match_questions,
)

match_request = DecisionMatchRequest(
    query='When is maintenance?',
    candidates=(DecisionMatchCandidate(key='bulletin', text=question.state),),
    baseline_keys=('bulletin',),
    privacy='public',
)
# Review the complete generated requests before enabling platform matching.
approved = candidate_match_questions(match_request)
match_client = DecisionClient(
    fallback=baseline, config=DecisionConfig(),
    authorize=lambda request: request in approved,
)
matcher = DecisionCandidateMatcher(match_client, DecisionMatchConfig())
match_result = matcher.match(match_request)
print(match_result.selected_keys, match_result.reason)  # ('bulletin',), disabled
```

`DecisionMatchCandidate` has a unique local key (1–512 characters) and excerpt
(1–1,200 characters). `DecisionMatchRequest` has `query` (1–16,384 characters), a
tuple of up to 16 candidates, unique `baseline_keys` from those candidates,
`privacy='unknown'`, and `max_results=5` (1–16).
`DecisionMatchConfig` defaults to `enabled=False`, `max_candidates=5` (1–16),
`max_state_characters=8192` (1–16,384 serialized characters), and
`timeout_seconds=1.0` (positive finite).

`candidate_match_questions(request)` constructs public-only questions for a local
authorization allowlist; it raises `ValueError` for private/unknown input. Source
keys and baseline keys stay local; the model sees synthetic candidate labels and
bounded text. There is no `private_data` option on matching requests: use only
approved public text, or retain the baseline.

`match` / `amatch`, and ordered `batch` / `abatch(max_concurrency=4)`, return
`DecisionMatchResult(selected_keys, source, reason, decisions, latency_seconds)`.
Source is `primary` or `baseline`; reasons are `accepted`, `blocked`, `disabled`,
`empty`, `oversized`, `rejected`, `timeout`, `unavailable` or `no_match`.
Only when **every** candidate question has an accepted primary `yes` or `no`
answer, and at least one matches, can selection change. Matches preserve candidate
order and are capped at `max_results`. Any unknown, refusal, malformed answer,
timeout, privacy failure or no-match result preserves the exact `baseline_keys`,
without truncating or reordering them. Matching uses its own local abstaining
fallback rather than spending the decision client's caller fallback.

### Usage types and privacy guidance

`TokenUsageCategory` is `decision` or `llm`; `TokenUsageByCategory` holds one
`TokenCategoryUsage` per category. Each category has `input_tokens`,
`output_tokens`, `total_tokens`, `known_input_tokens`, `known_output_tokens`,
`unknown_input_calls` and `unknown_output_calls`. Unknown totals remain `None`;
known subtotals remain available. `summarize_decision_usage(results)` accepts a
collection of decision results. All workflow outcomes expose
`token_usage_by_category`; aggregating native receipts counts each owner once.
These counters alone do not prove cost savings or production qualification.

Ordinary `InvokeResponse.privacy_guidance` contains advisory over-redaction
suggestions when delivered by the platform. Guidance includes the actual delivered
value; it does not change redaction or reveal model input. `repr=False` keeps it
out of the response representation, but `model_dump()` and JSON serialization
still include it. Display it only in a trusted developer interface; do not log or
persist the guidance or serialize whole responses into diagnostics. Normalized
SDK event builders do not guarantee a guidance field. Decision results have no
`privacy_guidance` field.

## Bounded hosted jobs (deployment opt-in)

Hosted jobs start immediately and continue after your HTTP client disconnects.
The feature is disabled by default. An enabled deployment initially permits at most
64 aggregate model/tool steps per job and two active jobs per project. The server
also limits duration to its configured task deadline, never more than 30 minutes.
Every start must provide all three positive bounds; ordinary invocations are unchanged.

```python
from maivn import Client, HostedJobOptions, RunOptions

client = Client()
job = client.jobs.start(
    ['Summarize the supplied research notes.'],
    job=HostedJobOptions(
        idempotency_key='research-summary-2026-10-22',
        max_steps=20,
        max_duration_seconds=120,
    ),
    options=RunOptions(max_expense_usd='0.50'),
)
status = client.jobs.get(job.session_id)
# client.jobs.cancel(job.session_id) commits a stop for new work.
```

Async callers use `astart`, `aget`, and `acancel`. Repeating the same idempotency key
and semantic request returns the original job; a changed request conflicts. Generated
message IDs and timestamps do not change request identity. The expense ceiling uses
the same exact decimal and provider-expense accounting as a capped ordinary run;
the lower server ceiling also applies. Nested models, compaction, and helpers share
the same root, step limit, expense ceiling, and fixed database deadline. Background
memory enrichment is skipped.

Jobs support model calls and free system tools. To use registered worker/webhook
tools, provide `registered_agent={'agent_id': 'registered-agent-id', 'version': 1}`
in `HostedJobOptions`; the server resolves that immutable registered revision and
uses its pinned instructions, model, output schema, agents, skills, memory and
authorized tool surface. The caller supplies messages, private data, thread context
and the explicit job bounds. Nondefault caller model or policy options alongside
`registered_agent` are rejected with HTTP 422; default SDK wire values are accepted. SDK-local Python tools, human-input
continuations, paid hosted search, sandbox, and hosted artifact producers are
unsupported. Connector/provider fees charged outside mAIvn are outside
`max_expense_usd`.

`GET /v1/jobs/{session_id}` (the SDK's `get`) returns durable status, the terminal
reason, step count, retained progress cursor, and any retained shielded final
message/result. Retained session events are available through the existing
`/v1/sessions/{session_id}/events` replay endpoint. Reads require the original
active owner key and project access. The ordinary process-local `/result` endpoint
is not the durable job result API. Private values remain process-only; status and
replayed events keep placeholders rather than restoring private values.

Cancellation prevents new work, but cannot undo an external effect already sent.
Late receipts still settle costs. Server/replica loss ends the job as interrupted
or execution-outcome-unknown and preserves unknown expense reservations. Jobs never
resume or replay automatically after a crash. Deployments end running jobs unless
an independently approved drain is used. Retention rules still apply to progress
and output.

The deployment must keep `MAIVN_INTERNAL_PLANE_HMAC_SECRET` configured and stable
across replicas while retained start requests need to be replayed. Job idempotency
uses a domain-separated keyed digest, including private input, and stores no raw
request. Records may outlive the 30-minute execution limit under retention.
Rotating the server secret makes old start retries conflict; reading or cancelling
an already known job session is unaffected. A new idempotency key starts new work.


When no thread is supplied, the SDK derives a credential-scoped thread identifier
from the idempotency key using a domain-separated keyed digest. The credential is
never sent as the thread identifier. Keep the same credential and thread context
for start retries; rotating the credential changes this default and may conflict
with an existing project-wide idempotency key. Supply the original thread explicitly
when retrying with the original authorized owner key. A new owner key cannot
take over that key's existing job or project-wide idempotency key.
An explicit per-call or configured client thread always takes precedence.
`client_timezone` and `sdk_deployment_timezone` also participate in request identity;
retrying from a different host/timezone can return HTTP 409. Preserve these options when
replaying the same start request.

## Customer memory isolation (behavior change)

Automatic memory and run attachments are private to the exact `user_id` within the
authenticated project. IDs are opaque and case-sensitive, at most 256 characters,
without control characters or surrounding whitespace. The default `sdk-user` and
blank subjects disable private recall, extraction and attachment persistence;
generation, current prompt attachments and explicitly shared authored knowledge
remain available. Legacy ownerless records remain hidden from runtime recall.

Set `RunOptions(user_id=...)` from your trusted backend's authenticated customer
session. The platform authenticates the project key; it does **not** independently
authenticate that end user. Do not expose a project key and trust a browser-supplied
ID as customer authorization. Key rotation does not change customer ownership.

A trusted backend that acts for a single customer can set that customer once with
`Client(default_user_id=...)` or `MAIVN_DEFAULT_USER_ID`. It fills `user_id` only for
runs whose `RunOptions` did not set one; an explicit per-call `user_id` always wins.
It does not change the `.for_user(user_id)` facades below, which always need an
explicit ID.

Memory, skill and resource clients default to project developer management. Narrow
all operations with independent facades such as
`client.memory.for_user(user_id).insights.list()`,
`client.skills.for_user(user_id)` and `client.resources.for_user(user_id)`.
These send the developer-asserted `X-Maivn-User-Id` header (percent-encoded UTF-8,
decoded once by the server, preserving opaque IDs exactly). A supplied invalid, blank
or default ID is rejected rather than falling back to project management.
Subject-scoped management sees only that customer's private records. Responses
include `scope.owner_user_id` and `scope.shared`.

Promotion and binding preserve private ownership. To deliberately share knowledge,
a project developer must create a new authored record with `scope.shared=True`
and their own content through the existing create endpoint. Never automatically
copy conversation-derived private content into a shared record.

Legacy sessions or threads containing recalled memory or compaction without a
partition version cannot be safely continued. The server returns
`thread_memory_legacy_requires_new_session`; create a new thread explicitly with
your trusted customer `user_id`. Existing unbound legacy threads cannot be adopted
by a customer subject (`thread_user_mismatch`). The SDK surfaces these error codes
without silently replaying, discarding history or switching threads.

`Agent.add_skill` and `Swarm.add_skill` deliberately author shared knowledge for
that agent or swarm. They reject existing private or automatically extracted
skills instead of silently converting them into shared content.

New memory, skill, skill-set and resource creates require either
`scope.shared=True` for deliberately authored shared content or a customer-scoped
`for_user(user_id)` facade. Unclassified creates return HTTP 422 with
`memory_classification_required`. Existing ownerless authored skills, skill sets,
uploads and promoted insights are also hidden from runtime recall until recreated
with an explicit classification; the rollout change includes all these record types.
Thread list summaries remain available for legacy threads and expose the read-only
`requires_new_session` flag; history and continuation still refuse unsafe legacy state.

Use `Client.preview_redaction()` (or `apreview_redaction()`) to review the shielded
prompt and attachments before a run. See the [private-data guide](../../docs/guides/private-data.md#preview-redaction).
