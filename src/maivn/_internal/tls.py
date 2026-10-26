"""One TLS context per trust setting, shared by short-lived outbound HTTP clients."""

from __future__ import annotations

from typing import TYPE_CHECKING

import httpx

if TYPE_CHECKING:
    import ssl


# Keyed by the setting itself, so tls_context() and tls_context(trust_env=True)
# share one context.
_CONTEXTS: dict[bool, ssl.SSLContext] = {}


def tls_context(*, trust_env: bool = True) -> ssl.SSLContext:
    """Return the process's TLS context for this trust setting, built once.

    An ``httpx`` client built without one loads the certificate bundle again,
    about 400 ms of event-loop CPU on Windows, every time it is constructed.
    """
    context = _CONTEXTS.get(trust_env)
    if context is None:
        context = _CONTEXTS.setdefault(trust_env, httpx.create_ssl_context(trust_env=trust_env))
    return context
