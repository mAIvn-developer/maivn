"""The shared TLS context is built once per trust setting and still verifies certificates."""

from __future__ import annotations

import asyncio
import ssl

import httpx

from maivn._internal.tls import tls_context
from maivn._internal.transport.leases import LoopLocalTransport


def test_one_context_per_trust_setting_that_verifies_certificates() -> None:
    """Reuse one verifying context per trust setting instead of one per client."""
    context = tls_context()
    assert tls_context() is context
    assert tls_context(trust_env=False) is tls_context(trust_env=False)
    assert tls_context(trust_env=False) is not context
    for shared in (context, tls_context(trust_env=False)):
        assert shared.verify_mode == ssl.CERT_REQUIRED
        assert shared.check_hostname is True


def test_default_loop_pools_share_verifying_tls_context() -> None:
    """Keep pools loop-local while sharing the cached certificate context."""
    transport = LoopLocalTransport()

    async def pool_for_loop() -> httpx.AsyncHTTPTransport:
        pool = transport.for_current_loop()
        assert isinstance(pool, httpx.AsyncHTTPTransport)
        assert transport.for_current_loop() is pool
        await pool.aclose()
        return pool

    first = asyncio.run(pool_for_loop())
    second = asyncio.run(pool_for_loop())

    assert first is not second
    # Inspect the actual HTTPX pool contexts, not just constructor arguments.
    assert (
        first._pool._ssl_context  # pyright: ignore[reportPrivateUsage] # noqa: SLF001
        is tls_context()
    )
    assert (
        second._pool._ssl_context  # pyright: ignore[reportPrivateUsage] # noqa: SLF001
        is tls_context()
    )
    assert tls_context().verify_mode == ssl.CERT_REQUIRED
    assert tls_context().check_hostname is True
