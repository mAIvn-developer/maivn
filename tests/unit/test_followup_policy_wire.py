"""The follow-up policy's wire payload must distinguish both authoring modes.

Owner ruling 2026-08-21: enabling follow-up questions WITHOUT declaring an answer format
is the AI-selected mode - the model proposes the format at run time. The wire payload is
where that distinction is either preserved or destroyed.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel

from maivn._internal.compat.interrupts import FollowupQuestionsConfig

_RUN_LIMIT = 2
_THREAD_LIMIT = 7


class _Answer(BaseModel):
    """Structured answer model used as a declared response schema."""

    region: str


def _config(**kwargs: Any) -> FollowupQuestionsConfig:
    return FollowupQuestionsConfig(input_handler=lambda _question, _schema: 'US East', **kwargs)


def test_an_undeclared_answer_format_is_omitted_from_the_wire_payload() -> None:
    """Omission is the AI-selected signal; a defaulted value would erase it."""
    payload = _config().wire_payload()

    assert 'response_schema' not in payload


def test_a_declared_mapping_answer_format_still_reaches_the_wire() -> None:
    """Deterministic mode is unchanged: what the developer declared is sent."""
    payload = _config(response_schema={'type': 'boolean'}).wire_payload()

    assert payload['response_schema'] == {'type': 'boolean'}


def test_a_declared_model_answer_format_is_serialized_to_its_json_schema() -> None:
    """A pydantic model is still a declaration, not an absence."""
    payload = _config(response_schema=_Answer).wire_payload()

    assert payload['response_schema'] == _Answer.model_json_schema()


def test_declaring_free_text_explicitly_is_not_the_same_as_declaring_nothing() -> None:
    """The developer who wants free text every time can still pin it."""
    payload = _config(response_schema={'type': 'string'}).wire_payload()

    assert payload['response_schema'] == {'type': 'string'}


def test_the_limits_still_travel_when_no_format_is_declared() -> None:
    """Dropping the schema key must not drop the rest of the policy."""
    payload = _config(
        max_questions=_RUN_LIMIT,
        max_questions_per_thread=_THREAD_LIMIT,
    ).wire_payload()

    assert payload['max_questions_per_run'] == _RUN_LIMIT
    assert payload['max_questions_per_thread'] == _THREAD_LIMIT
