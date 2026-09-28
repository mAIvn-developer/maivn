"""Final-event normalization preserves model-tool results beside prose."""

from maivn.events._models import NormalizedStreamState
from maivn.events._normalize.context import NormalizationOptions
from maivn.events._normalize.lifecycle_events import handle_final_event
from maivn.events._normalize.system_events import handle_system_tool_complete_event


def test_final_event_uses_last_model_tool_result_when_prose_is_separate() -> None:
    """A prose final event keeps the preceding model-tool value as result."""
    state = NormalizedStreamState()
    state.last_model_tool_result = {'status': 'ready'}

    payloads = handle_final_event(
        {'response': 'The result is ready.'},
        state,
        NormalizationOptions(),
    )

    assert payloads[-1]['response'] == 'The result is ready.'
    assert payloads[-1]['result'] == {'status': 'ready'}


def test_system_tool_completion_retains_the_latest_structured_value() -> None:
    """Studio can retain a completed final function tool beside later prose."""
    state = NormalizedStreamState()

    handle_system_tool_complete_event(
        {'tool_name': 'final_result', 'result': {'status': 'ready'}},
        state,
        NormalizationOptions(),
    )

    assert state.last_tool_result == {'status': 'ready'}
