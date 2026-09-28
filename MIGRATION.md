# Migrating from maivn 0.4.x to 2.0

maivn 2.0 is the SDK for mAIvn Platform v2. The 0.4.x line targeted the previous platform, which is retired, so this is a new major version rather than an in-place upgrade. This page covers the packaging facts; the platform documentation at https://developer.maivn.io covers the API.

## Install

```bash
pip install "maivn==2.0.0"
pip install "maivn[fastapi,otel,rich]==2.0.0"   # optional extras
pip install "maivn-tools==2.0.0"                # optional connectors, Python 3.12+
```

Python 3.10 or later is required for `maivn`; `maivn-tools` requires 3.12 or later.

## What changed in the package

| | 0.4.3 | 2.0.0 |
| --- | --- | --- |
| Contracts | `maivn-shared` | `maivn-contracts` (new package, 0.1.0) |
| Private data storage | not included | `private-data-vault` (new package, 0.1.0, Rust extension with abi3 wheels) |
| HTTP client and models | not declared | `httpx`, `orjson`, `pydantic` 2.13.5 or later, `pydantic-settings` |
| Terminal output | `rich` required | `rich` optional, via `maivn[rich]` |
| Removed dependencies | | `docstring-parser`, `prompt-toolkit`, `python-dotenv`, `tzlocal` |
| Console script | `maivn` | `maivn` (unchanged name) |

`maivn-shared`, `maivn-studio` and the 0.4.x releases stay on PyPI for the retired platform; they are not part of 2.0.

## Where to look next

- Changelog: `CHANGELOG.md` in this repository.
- The 0.4.3 source is preserved on the `v0.4.3` tag of this repository.
