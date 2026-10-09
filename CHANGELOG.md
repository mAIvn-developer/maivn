# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

## [2.2.0] - 2026-10-09

### Added
- Expanded the SDK README decision reference with request/result contracts, routing and fallback errors, ordered/native batches, invocation stages and streaming, typed arguments, managed function/tool dependencies, candidate matching, categorized usage and privacy guidance.
- `InvokeResponse.privacy_guidance` exposes advisory over-redaction suggestions from the final event, including the delivered value. Guidance never changes redaction or unhides model input. It is omitted from the response representation to avoid incidental diagnostic output, but remains present in model/JSON serialization; do not log or persist it.
- `maivn.decisions`: categorical decisions with a caller-owned fallback (`DecisionClient`, fluent `decide(...)` builders, decision arguments, dependencies and candidate matching). Every primary decision goes through the mAIvn platform (`POST /v1/decisions`) with your normal mAIvn API key and `MAIVN_BASE_URL`. The platform picks the decision model from its signed catalog, shields explicitly marked private values in each request, and bills the call to your project. Results carry the served model and the platform's token receipt. There is no decision-model key and no model name to configure. `DecisionClient(fallback=..., config=..., authorize=..., client=None)` approves each request locally before anything is sent; only requests declared `privacy='public'` are sent. Values you mark private with `DecisionRequest(private_data={key: value})` are shielded into placeholders by the platform before the decision model sees them, and a placeholder verdict is resolved back locally.

### Fixed
- Declared private values inside cyclic event payloads cannot escape through history, replay, SSE or packet observers. Plain cyclic payloads remain unchanged.

## [2.1.0] - 2026-10-03

### Added
- `Client.astream` and `Agent.astream` accept `cancel_on_close=False`. Opting in cancels the accepted root invocation when the stream closes before a root final or error, including an interrupt awaiting input. Cancellation cleanup waits at most two seconds; failures preserve the original stream failure. The default retains disconnect and replay behavior.

### Fixed
- `EventBridge` no longer sends the raw value of a `PrivateData` to clients. SSE frames, `get_history()`, replays and `on_packet` observers now receive `{"__private__": true, "label": ..., "pii_type": ...}` in its place, for both audiences and at any depth, including inside dataclasses and pydantic models. Your own objects are left unchanged.

## [2.0.0] - 2026-09-28

The 2.x line targets mAIvn Platform v2. It is not a drop-in upgrade from 0.4.x; the client, configuration and CLI changed with the platform.

### Changed
- Runtime contracts come from the maivn-contracts package. maivn-shared is no longer used.
- Encrypted local storage for private data uses the private-data-vault extension (abi3 wheels for Python 3.10 and later).
- Python 3.10 is the minimum for the SDK. maivn-tools needs 3.12.
- Migration notes: see MIGRATION.md in this repository.

### Removed
- Runtime dependencies on docstring-parser, prompt-toolkit, python-dotenv and tzlocal.
- rich is now the optional extra maivn[rich].

## 0.4.3 and earlier

The 0.x history lives on the `v0.4.3` tag of this repository.
