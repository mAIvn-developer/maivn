# maivn

Python SDK for mAIvn agents, swarms, tools, structured output, conversations,
triggers, resources, and artifacts.

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
