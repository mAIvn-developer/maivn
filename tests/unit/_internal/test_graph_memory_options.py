"""Graph memory configuration reaches the closed v2 invocation policy."""

from __future__ import annotations

from maivn import MemoryConfig, MemoryGraphExtractionConfig, MemoryRetrievalConfig
from maivn._internal.scope.options import (
    _memory_policy_payload,  # pyright: ignore[reportPrivateUsage]
)


def test_sdk_preserves_explicit_graph_controls() -> None:
    """Graph writes and recall can be configured separately with existing memory options."""
    config = MemoryConfig(
        persistence_mode='vector_plus_graph',
        retrieval=MemoryRetrievalConfig(graph_enabled=False, graph_injection_max_count=5),
        graph_extraction=MemoryGraphExtractionConfig(enabled=True, max_count=7),
    )
    payload = _memory_policy_payload(config)
    assert payload['graph_extraction'] == {'enabled': True, 'max_count': 7}
    assert payload['retrieval']['graph_enabled'] is False
    assert payload['retrieval']['max_graph_assertions'] == 5  # noqa: PLR2004 - authored count


def test_sdk_omits_unconfigured_graph_fields_from_existing_policy_payloads() -> None:
    """Existing deployed definitions keep their exact optional-field representation."""
    payload = _memory_policy_payload(MemoryConfig(level='focus'))
    assert 'graph_extraction' not in payload
    assert 'graph_enabled' not in payload['retrieval']
    assert 'max_graph_assertions' not in payload['retrieval']
