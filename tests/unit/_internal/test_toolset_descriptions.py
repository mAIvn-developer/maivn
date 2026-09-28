"""Toolset registration must retain developer-authored execution constraints."""

from inspect import getdoc

import pytest

from maivn import Agent, toolify, toolset


@pytest.mark.parametrize('override', [None, 'Explicit public description.'])
def test_compiled_toolset_preserves_full_docstring_unless_overridden(override: str | None) -> None:
    """Registration retains constraints while an explicit description stays authoritative."""

    @toolset(prefix='layout')
    class Layout:
        @toolify(description=override)
        def place(self, address: str) -> str:
            """Place a rectangle.

            Ranges must not overlap. Reuse the existing identifier to replace one.
            Blank cells still occupy space; place formulas in separate ranges.
            """
            return address

    agent = Agent(name='tool-description-contract', api_key='test-key')
    agent.add_toolset(Layout())
    [compiled] = agent.compile_tools()
    assert compiled.description == (override or getdoc(Layout.place))
