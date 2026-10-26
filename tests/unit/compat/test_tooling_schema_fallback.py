"""A tool whose annotation pydantic cannot resolve still registers (U05-D5).

``Decimal`` is imported only for type checkers, so under postponed annotations
the parameter's annotation is an unresolvable string at runtime. Registration
falls back to the name-only schema for that parameter, as it does for an
annotation pydantic rejects with ``TypeError``.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from maivn import Agent

if TYPE_CHECKING:
    from decimal import Decimal


def test_type_checking_only_annotation_falls_back_to_a_name_schema() -> None:
    """The unresolvable parameter gets the fallback schema; the others keep theirs."""
    agent = Agent(name='fallback', api_key='test-key', base_url='http://testserver')

    def refund(order_id: str, amount: Decimal) -> str:
        """Refund an order."""
        return f'{order_id}:{amount}'

    _ = agent.toolify(name='refund')(refund)
    (compiled,) = agent.compile_tools()

    assert compiled.input_schema == {
        'type': 'object',
        'properties': {'order_id': {'type': 'string'}, 'amount': {'description': 'amount'}},
        'additionalProperties': False,
        'required': ['order_id', 'amount'],
    }
