"""Client cache helpers for Agent construction."""

from __future__ import annotations

from typing import TYPE_CHECKING
from weakref import WeakValueDictionary

if TYPE_CHECKING:
    from ..client import Client


# MARK: Cache

_CLIENT_CACHE: WeakValueDictionary[tuple[str, str, str, float | int | None], Client] = (
    WeakValueDictionary()
)


# MARK: Client Resolution


def get_or_create_client(api_key: str) -> Client:
    """Get a cached Client for the active SDK configuration or create one."""
    from maivn._internal.utils.configuration import get_configuration

    from ..client import Client

    config = get_configuration()
    cache_key = (
        api_key,
        config.server.base_url,
        config.server.mock_base_url,
        config.server.timeout_seconds,
    )

    cached = _CLIENT_CACHE.get(cache_key)
    if cached is not None:
        return cached

    client = Client(api_key=api_key)
    _CLIENT_CACHE[cache_key] = client
    return client


__all__ = ["get_or_create_client"]
