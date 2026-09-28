"""System-tool deltas adapt to cumulative reporters without mixing concurrent calls."""

import asyncio

from maivn.events import (
    AppEvent,
    NormalizedEventForwardingState,
    build_system_tool_chunk_payload,
    forward_normalized_event,
)
from maivn.events._forwarding.state import clear_tool_state


def test_system_tool_reporter_receives_separate_cumulative_snapshots() -> None:
    """Repeated text is content, with accumulated state owned and cleared per call."""
    received: list[tuple[str, str | None]] = []

    class Reporter:
        def report_system_tool_progress(
            self, event_id: str, text: str | None = None, **_kwargs: object
        ) -> None:
            received.append((event_id, text))

    async def scenario() -> None:
        state = NormalizedEventForwardingState()
        reporter = Reporter()
        for tool_id, text in [('a', '('), ('b', ' '), ('a', '('), ('b', ' '), ('a', '1))')]:
            await forward_normalized_event(
                AppEvent.model_validate(
                    build_system_tool_chunk_payload(tool_id=tool_id, text=text)
                ),
                reporter=reporter,
                state=state,
            )
        clear_tool_state(state, 'a')
        assert state.system_tool_text_by_id == {'b': '  '}

    asyncio.run(scenario())
    assert received == [('a', '('), ('b', ' '), ('a', '(('), ('b', '  '), ('a', '((1))')]
