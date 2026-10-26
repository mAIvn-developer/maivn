"""The shared TLS context is built once per trust setting and still verifies certificates."""

from __future__ import annotations

import ssl

from maivn._internal.tls import tls_context


def test_one_context_per_trust_setting_that_verifies_certificates() -> None:
    """Reuse one verifying context per trust setting instead of one per client."""
    context = tls_context()
    assert tls_context() is context
    assert tls_context(trust_env=False) is tls_context(trust_env=False)
    assert tls_context(trust_env=False) is not context
    for shared in (context, tls_context(trust_env=False)):
        assert shared.verify_mode == ssl.CERT_REQUIRED
        assert shared.check_hostname is True
