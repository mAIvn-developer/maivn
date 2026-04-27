# RUNBOOK - mAIvn SDK

This runbook explains how to build, test, and validate the Python SDK after changes, plus how to roll them back if needed.

## Prerequisites

- Python 3.11+
- [uv](https://github.com/astral-sh/uv) for dependency management (preferred)

All commands assume the monorepo root (adjust paths if running on another OS).

## Setup

```bash
cd libraries/maivn
uv sync  # installs runtime + dev dependencies using pyproject + uv.lock
```

If you prefer plain virtualenv:

```bash
python -m venv .venv
source .venv/bin/activate  # or .venv\Scripts\activate on Windows
pip install -e .[dev]
```

## Test & Equivalence Verification

1. **Unit tests**

   ```bash
   cd libraries/maivn
   uv run pytest
   ```

   The suite exercises the orchestrator dispatcher, interrupt handling, and dependency wiring.

2. **SDK import & Agent smoke test**

   ```bash
   uv run python -c "from maivn import Agent, Client; print('Import OK')"
   ```

   Confirms the public surface (`Agent`, `Client`, message re-exports) still imports cleanly.

3. **Type checking and linting**

   ```bash
   uv run pyright src/maivn
   uv run ruff check src/maivn
   ```

## Rollback Instructions

- **Unstaged changes**: `git restore libraries/maivn`
- **Committed changes**: `git revert <commit_sha>`
- **Entire repo reset** (destructive): `git reset --hard origin/master`

Always re-run `uv run pytest` after rollback to confirm the SDK behavior is restored.

## Execution Context Reference

The orchestrator and dependency layers share a single `ExecutionContext` dataclass (`maivn._internal.core.entities.execution_context.ExecutionContext`). Key fields:

- `scope`: the active `Agent`/`Swarm` instance
- `tool_results`: mutable map of tool_id -> raw execution result (used by `depends_on_tool`)
- `messages`: most recent message sequence passed into the agent
- `timeout`: agent-specific timeout override (seconds)
- `metadata`: optional dictionary for out-of-band data (e.g., private data injections, available helper tools)
