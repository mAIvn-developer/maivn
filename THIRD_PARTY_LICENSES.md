# Third-Party Open Source License Report

**Package**: maivn
**Distribution**: Public PyPI SDK
**Report Date**: 2026-06-06
**Report Version**: 2.1

---

## Executive Summary

This report is generated from the locked dependency graph (`uv export --locked`) and installed package metadata.
First-party `maivn-*` packages are excluded from the third-party inventory.

**Compliance Result**: PASS

| Scope | Packages | Permissive | Weak Copyleft | Strong Copyleft | Non-OSI | Unknown |
|-------|----------|------------|----------------|------------------|---------|---------|
| Runtime | 40 | 38 | 2 | 0 | 0 | 0 |
| Dev-only | 15 | 15 | 0 | 0 | 0 | 0 |

---

## Notable Findings

- Runtime blockers detected: none
- Runtime weak-copyleft packages: certifi, orjson
- Dev-only non-OSI packages: none

## Runtime Dependencies

| Package | Version | Effective License | Category | Notes |
|---------|---------|-------------------|----------|-------|
| annotated-types | 0.7.0 | MIT License | Permissive |   |
| anyio | 4.13.0 | MIT | Permissive |   |
| certifi | 2026.2.25 | Mozilla Public License 2.0 (MPL 2.0) | Weak Copyleft |   |
| charset-normalizer | 3.4.6 | MIT | Permissive |   |
| croniter | 6.2.2 | MIT | Permissive |   |
| docstring-parser | 0.17.0 | MIT License | Permissive |   |
| h11 | 0.16.0 | MIT License | Permissive |   |
| httpcore | 1.0.9 | BSD-3-Clause | Permissive |   |
| httpx | 0.28.1 | BSD License | Permissive |   |
| idna | 3.18 | BSD-3-Clause | Permissive |   |
| jsonpatch | 1.33 | BSD License | Permissive |   |
| jsonpointer | 3.1.0 | BSD License | Permissive |   |
| langchain-core | 1.4.0 | MIT License | Permissive |   |
| langchain-protocol | 0.0.15 | MIT License | Permissive |   |
| langsmith | 0.8.8 | MIT | Permissive |   |
| markdown-it-py | 4.0.0 | MIT License | Permissive |   |
| mdurl | 0.1.2 | MIT License | Permissive |   |
| orjson | 3.11.9 | MPL-2.0 AND (Apache-2.0 OR MIT) | Weak Copyleft |   |
| packaging | 26.0 | Apache-2.0 OR BSD-2-Clause | Permissive |   |
| prompt-toolkit | 3.0.52 | BSD License | Permissive |   |
| pydantic | 2.13.3 | MIT | Permissive |   |
| pydantic-core | 2.46.3 | MIT | Permissive |   |
| pygments | 2.20.0 | BSD-2-Clause | Permissive |   |
| python-dateutil | 2.9.0.post0 | BSD License OR Apache Software License | Permissive |   |
| python-dotenv | 1.2.2 | BSD-3-Clause | Permissive |   |
| pyyaml | 6.0.3 | MIT License | Permissive |   |
| requests | 2.34.2 | Apache Software License | Permissive |   |
| requests-toolbelt | 1.0.0 | Apache Software License | Permissive |   |
| rich | 14.3.4 | MIT License | Permissive |   |
| six | 1.17.0 | MIT License | Permissive |   |
| tenacity | 9.1.4 | Apache Software License | Permissive |   |
| typing-extensions | 4.15.0 | PSF-2.0 | Permissive |   |
| typing-inspection | 0.4.2 | MIT | Permissive |   |
| tzlocal | 5.3.1 | MIT License | Permissive |   |
| urllib3 | 2.7.0 | MIT | Permissive |   |
| uuid-utils | 0.14.1 | BSD-3-Clause | Permissive |   |
| wcwidth | 0.6.0 | MIT | Permissive |   |
| websockets | 15.0.1 | BSD License | Permissive |   |
| xxhash | 3.6.0 | BSD License | Permissive |   |
| zstandard | 0.25.0 | BSD-3-Clause | Permissive |   |

---

## Development-Only Dependencies

These packages are not part of the production runtime image.

| Package | Version | Effective License | Category | Notes |
|---------|---------|-------------------|----------|-------|
| annotated-doc | 0.0.4 | MIT | Permissive |   |
| basedpyright | 1.39.5 | MIT License | Permissive |   |
| click | 8.3.1 | BSD-3-Clause | Permissive |   |
| fastapi | 0.136.3 | MIT | Permissive |   |
| iniconfig | 2.3.0 | MIT | Permissive |   |
| nodeenv | 1.10.0 | BSD License | Permissive |   |
| nodejs-wheel-binaries | 24.15.0 | MIT License | Permissive |   |
| pluggy | 1.6.0 | MIT License | Permissive |   |
| pyright | 1.1.409 | MIT | Permissive |   |
| pytest | 9.0.3 | MIT | Permissive |   |
| pytest-asyncio | 1.3.0 | Apache-2.0 | Permissive |   |
| ruff | 0.15.12 | MIT | Permissive |   |
| sse-starlette | 2.1.3 | BSD License | Permissive |   |
| starlette | 1.0.1 | BSD-3-Clause | Permissive |   |
| uvicorn | 0.46.0 | BSD-3-Clause | Permissive |   |

---

## Compliance Checklist

- [x] No blocked runtime licenses detected (strong copyleft, non-OSI, or unknown).
- [x] Weak-copyleft runtime dependencies are used unmodified.
- [x] Dev-only non-OSI packages remain excluded from production artifacts.
- [x] Committed license report is expected to stay in sync with `uv.lock`.

---

## Certification

This report was generated from `uv export --locked` output and installed package metadata as of the report date above.
