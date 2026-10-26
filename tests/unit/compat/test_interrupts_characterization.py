"""Characterization tests for follow-up and interrupt answer handling."""

from __future__ import annotations

import builtins
import math

import pytest
from pydantic import BaseModel, ValidationError

from maivn import FollowupQuestion, FollowupQuestionsConfig, default_terminal_followup
from maivn._internal.compat.interrupts import typed_interrupt_answer


def _question(**overrides: object) -> FollowupQuestion:
    payload: dict[str, object] = {
        'checkpoint_id': 'cp-1',
        'thread_id': 'th-1',
        'session_id': 'se-1',
        'question': 'Choose a region.',
        'options': [
            {'label': 'US East', 'description': 'Lowest latency.'},
            {'label': 'EU West', 'description': 'EU residency.'},
        ],
        **overrides,
    }
    return FollowupQuestion.model_validate(payload)


def _answer(
    monkeypatch: pytest.MonkeyPatch,
    raw: str,
    question: FollowupQuestion,
    schema: dict[str, object],
) -> tuple[object, list[str]]:
    prompts: list[str] = []

    def fake_input(prompt: str) -> str:
        prompts.append(prompt)
        return raw

    monkeypatch.setattr(builtins, 'input', fake_input)
    return default_terminal_followup(question, schema), prompts


def _handler(_question: FollowupQuestion, _schema: dict[str, object]) -> object:
    return None


class _Answer(BaseModel):
    region: str


@pytest.mark.parametrize(
    ('field', 'message'),
    [
        ('max_questions', 'follow-up question limits must be positive'),
        ('max_questions_per_thread', 'follow-up question limits must be positive'),
        ('timeout', 'follow-up timeout must be positive'),
    ],
)
def test_followup_config_rejects_non_positive_limits(field: str, message: str) -> None:
    """Question limits and timeouts must be at least one."""
    with pytest.raises(ValueError, match=r'^') as excinfo:
        _ = FollowupQuestionsConfig(
            input_handler=_handler,
            max_questions=0 if field == 'max_questions' else 3,
            max_questions_per_thread=0 if field == 'max_questions_per_thread' else 10,
            timeout=0 if field == 'timeout' else None,
        )
    assert str(excinfo.value) == message


def test_followup_config_wire_payload_omits_undeclared_schema_and_timeout() -> None:
    """Defaults send only the two limits; the handler never crosses the wire."""
    config = FollowupQuestionsConfig(input_handler=_handler)
    assert config.wire_payload() == {'max_questions_per_run': 3, 'max_questions_per_thread': 10}
    minimal = FollowupQuestionsConfig(
        input_handler=_handler,
        max_questions=1,
        max_questions_per_thread=1,
        timeout=1,
    )
    assert minimal.wire_payload() == {
        'max_questions_per_run': 1,
        'max_questions_per_thread': 1,
        'timeout_seconds': 1,
    }


def test_followup_config_wire_payload_serializes_declared_schemas() -> None:
    """Mapping schemas are copied; model classes become their JSON schema."""
    schema = {'type': 'string', 'enum': ['a', 'b']}
    mapped = FollowupQuestionsConfig(input_handler=_handler, response_schema=schema, timeout=30)
    payload = mapped.wire_payload()
    assert payload == {
        'max_questions_per_run': 3,
        'max_questions_per_thread': 10,
        'response_schema': {'type': 'string', 'enum': ['a', 'b']},
        'timeout_seconds': 30,
    }
    assert payload['response_schema'] is not schema
    modelled = FollowupQuestionsConfig(input_handler=_handler, response_schema=_Answer)
    assert modelled.wire_payload()['response_schema'] == _Answer.model_json_schema()


@pytest.mark.parametrize(
    ('value', 'expected'),
    [
        (True, True),
        (False, False),
        (' YES ', True),
        ('y', True),
        ('1', True),
        ('True', True),
        ('no', False),
        ('N', False),
        ('0', False),
        ('false', False),
    ],
)
def test_boolean_answers_accept_yes_no_vocabulary(value: object, *, expected: bool) -> None:
    """Booleans pass through and yes/no/true/false/1/0 strings are normalized."""
    assert typed_interrupt_answer(value, 'boolean') is expected


@pytest.mark.parametrize('value', ['maybe', '', 1, None])
def test_boolean_answers_refuse_anything_else(value: object) -> None:
    """Other strings and non-string values are refused."""
    with pytest.raises(ValueError, match=r'^boolean answer must be yes/no or true/false$'):
        _ = typed_interrupt_answer(value, 'boolean')


@pytest.mark.parametrize(
    ('value', 'expected'),
    [('42', 42), ('-1.5', -1.5), (7, 7), (2.5, 2.5), ('1e3', 1000.0)],
)
def test_numeric_answers_parse_json_numbers(value: object, expected: float) -> None:
    """Numbers and JSON number strings are returned as numbers."""
    result = typed_interrupt_answer(value, 'number')
    assert result == expected
    assert type(result) is type(expected)


@pytest.mark.parametrize(
    'value',
    ['abc', 'true', '"5"', '1e400', 'NaN', True, math.inf, math.nan, None, [1]],
)
def test_numeric_answers_refuse_non_finite_and_non_numbers(value: object) -> None:
    """Booleans, non-finite floats and non-numbers are refused."""
    with pytest.raises(ValueError, match=r'^numeric answer must be a finite number$'):
        _ = typed_interrupt_answer(value, 'number')


def test_other_input_types_pass_values_through() -> None:
    """Text and choice answers are returned unchanged."""
    marker = object()
    assert typed_interrupt_answer(marker, 'text') is marker
    assert typed_interrupt_answer(' raw ', 'choice') == ' raw '


def test_terminal_followup_renders_header_question_explanation_and_escape(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The prompt lists header, question, explanation, numbered options and Other."""
    question = _question(header='Region', explanation='Pick where data lives.')
    answer, prompts = _answer(monkeypatch, ' 1 ', question, {'type': 'string'})
    assert answer == 'US East'
    assert prompts == [
        'Region\nChoose a region.\nPick where data lives.\n'
        '1. US East — Lowest latency.\n2. EU West — EU residency.\n'
        'Other — enter a free-text answer\n> ',
    ]


def test_terminal_followup_without_options_has_no_escape_line(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A free-text question is just the question and returns the stripped text."""
    answer, prompts = _answer(monkeypatch, '  hello  ', _question(options=[]), {'type': 'string'})
    assert answer == 'hello'
    assert prompts == ['Choose a region.\n> ']


@pytest.mark.parametrize('raw', ['0', '3', '-1'])
def test_terminal_followup_out_of_range_numbers_stay_free_text(
    monkeypatch: pytest.MonkeyPatch,
    raw: str,
) -> None:
    """Numbers outside the option range are submitted as typed."""
    answer, _prompts = _answer(monkeypatch, raw, _question(), {'type': 'string'})
    assert answer == raw


def test_terminal_followup_multi_select_without_schema_type_splits_commas(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An untyped multi-select answer is split on commas and blanks are dropped."""
    question = _question(multi_select=True)
    answer, _prompts = _answer(monkeypatch, '2, ,custom,1', question, {})
    assert answer == ['EU West', 'custom', 'US East']


def test_terminal_followup_object_answers_must_be_json_objects(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Object schemas parse JSON and refuse non-object JSON."""
    with pytest.raises(ValueError, match=r'^structured follow-up answer must be a JSON object$'):
        _ = _answer(monkeypatch, '[1, 2]', _question(), {'type': 'object'})


def test_terminal_followup_boolean_refusal(monkeypatch: pytest.MonkeyPatch) -> None:
    """A boolean schema refuses an unrecognized word."""
    with pytest.raises(ValueError, match=r'^boolean answer must be yes/no or true/false$'):
        _ = _answer(monkeypatch, 'perhaps', _question(), {'type': 'boolean'})


def test_followup_question_limits() -> None:
    """Questions allow at most three options and are immutable."""
    option = {'label': 'A', 'description': 'B'}
    with pytest.raises(ValidationError) as excinfo:
        _ = _question(options=[option] * 4)
    assert [(e['loc'], e['msg']) for e in excinfo.value.errors()] == [
        (('options',), 'Tuple should have at most 3 items after validation, not 4'),
    ]
    question = _question()
    with pytest.raises(ValidationError, match='frozen'):
        question.question = 'x'
