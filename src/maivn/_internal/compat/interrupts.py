"""V1-compatible interrupt helpers."""

from __future__ import annotations

import json
import math
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Any, Final, TypeAlias, cast

from maivn_contracts.runtime.choice_options import choice_values, resolve_choice_options
from pydantic import BaseModel, ConfigDict, Field, StrictBool, StrictStr

JsonObject: TypeAlias = dict[str, Any]


class _AgentGeneratedPrompt(str):
    """Identity-bearing string marker for model-authored interrupt prompts."""

    __slots__ = ()


AgentGenerated: Final = _AgentGeneratedPrompt('__maivn_agent_generated__')


class FollowupOption(BaseModel):
    """One selectable answer and its human-facing consequence."""

    model_config = ConfigDict(extra='forbid', frozen=True)

    label: StrictStr = Field(..., min_length=1, max_length=80)
    description: StrictStr = Field(..., min_length=1, max_length=240)
    value: StrictStr | None = None


class FollowupQuestion(BaseModel):
    """Typed question delivered to an SDK follow-up input handler."""

    model_config = ConfigDict(extra='forbid', frozen=True)

    checkpoint_id: StrictStr = Field(..., min_length=1)
    thread_id: StrictStr = Field(..., min_length=1)
    session_id: StrictStr = Field(..., min_length=1)
    header: StrictStr | None = Field(default=None, min_length=1, max_length=64)
    # Match the runtime request_human_input authoring limit before application
    # handlers shape the question for display.
    question: StrictStr = Field(..., min_length=1, max_length=2048)
    explanation: StrictStr | None = Field(default=None, min_length=1, max_length=1024)
    options: tuple[FollowupOption, ...] = Field(default=(), max_length=3)
    multi_select: StrictBool = False
    required: StrictBool = True


FollowupInputHandler: TypeAlias = Callable[[FollowupQuestion, JsonObject], object]


@dataclass(frozen=True, slots=True)
class FollowupQuestionsConfig:
    """SDK-local handler plus the serializable invocation policy.

    A handler raising ``EOFError`` leaves the checkpoint unanswered while the
    stream waits for server timeout, cancellation, or an external response.
    Configure ``timeout`` to bound that wait. Returning ``None`` submits a JSON
    null answer and is not a signal to skip answering.
    """

    input_handler: FollowupInputHandler
    max_questions: int = 3
    max_questions_per_thread: int = 10
    response_schema: JsonObject | type[BaseModel] | None = None
    timeout: int | None = None

    def __post_init__(self) -> None:
        """Reject invalid limits before any invocation reaches the wire."""
        if self.max_questions < 1 or self.max_questions_per_thread < 1:
            message = 'follow-up question limits must be positive'
            raise ValueError(message)
        if self.timeout is not None and self.timeout < 1:
            message = 'follow-up timeout must be positive'
            raise ValueError(message)

    def wire_payload(self) -> JsonObject:
        """Return configuration only; the local callback never crosses the wire.

        An undeclared `response_schema` is OMITTED rather than defaulted to
        `{'type': 'string'}`. Omission is what tells the runtime this run is in
        AI-selected mode - enable follow-ups, let the model propose the answer format -
        and a default here would make "declared free text" and "declared nothing"
        indistinguishable on the wire (owner ruling 2026-08-21).
        """
        payload: JsonObject = {
            'max_questions_per_run': self.max_questions,
            'max_questions_per_thread': self.max_questions_per_thread,
        }
        schema = self.response_schema
        if isinstance(schema, type):
            payload['response_schema'] = schema.model_json_schema()
        elif isinstance(schema, Mapping):
            payload['response_schema'] = dict(schema)
        if self.timeout is not None:
            payload['timeout_seconds'] = self.timeout
        return payload


def is_agent_generated_prompt(prompt: str) -> bool:
    """Return whether a prompt is the exported singleton, never merely equal to it."""
    return prompt is AgentGenerated


def default_terminal_interrupt(prompt: str) -> str:
    """Collect interrupt input from the current terminal."""
    terminal_prompt = f'{prompt} ' if prompt and not prompt[-1:].isspace() else prompt
    return input(terminal_prompt).removeprefix('\ufeff')


def typed_interrupt_answer(value: object, input_type: str) -> object:
    """Normalize declared scalar answers without changing free-text or choice values."""
    if input_type == 'boolean':
        if isinstance(value, bool):
            return value
        if isinstance(value, str):
            normalized = value.strip().casefold()
            if normalized in {'true', 'yes', 'y', '1'}:
                return True
            if normalized in {'false', 'no', 'n', '0'}:
                return False
        message = 'boolean answer must be yes/no or true/false'
        raise ValueError(message)
    if input_type == 'number':
        return _numeric_interrupt_answer(value)
    return value


def _numeric_interrupt_answer(value: object) -> int | float:
    """Parse numeric terminal input while rejecting booleans and non-finite values."""
    message = 'numeric answer must be a finite number'
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except ValueError:
            raise ValueError(message) from None
    if (
        isinstance(value, (int, float))
        and not isinstance(value, bool)
        and (not isinstance(value, float) or math.isfinite(value))
    ):
        return value
    raise ValueError(message)


def default_terminal_followup(
    question: FollowupQuestion,
    response_schema: JsonObject,
) -> object:
    """Render a typed follow-up with its options and a free-text escape."""
    options = resolve_choice_options(
        response_schema,
        [option.model_dump() for option in question.options],
    )
    lines: list[str] = []
    if question.header:
        lines.append(question.header)
    lines.append(question.question)
    if question.explanation:
        lines.append(question.explanation)
    lines.extend(
        f'{index}. {option["label"]}'
        + (f' — {option["description"]}' if option['description'] else '')
        for index, option in enumerate(options, start=1)
    )
    if options and choice_values(response_schema) is None:
        lines.append('Other — enter a free-text answer')
    suffix = '\n'.join(lines)
    raw = input(f'{suffix}\n> ')
    return _typed_terminal_answer(raw, question=question, response_schema=response_schema)


def _typed_terminal_answer(
    raw: str,
    *,
    question: FollowupQuestion,
    response_schema: JsonObject,
) -> object:
    """Convert terminal text according to the declared response schema."""
    raw = raw.removeprefix('\ufeff')
    schema_type = response_schema.get('type')
    if schema_type == 'boolean':
        return typed_interrupt_answer(raw, 'boolean')
    if schema_type == 'object':
        value = json.loads(raw)
        if not isinstance(value, dict):
            message = 'structured follow-up answer must be a JSON object'
            raise ValueError(message)
        return cast('JsonObject', value)
    options = resolve_choice_options(
        response_schema,
        [option.model_dump() for option in question.options],
    )
    if schema_type == 'array' or (schema_type is None and question.multi_select):
        selections = [item.strip() for item in raw.split(',') if item.strip()]
        return [_terminal_option_value(item, options) for item in selections]
    return _terminal_option_value(raw.strip(), options)


def _terminal_option_value(raw: str, options: tuple[dict[str, str], ...]) -> str:
    """Resolve a one-based option number, otherwise preserve the free-text escape."""
    try:
        index = int(raw)
    except ValueError:
        return raw
    if 1 <= index <= len(options):
        return options[index - 1]['value']
    return raw


__all__ = [
    'AgentGenerated',
    'FollowupInputHandler',
    'FollowupOption',
    'FollowupQuestion',
    'FollowupQuestionsConfig',
    'default_terminal_followup',
    'default_terminal_interrupt',
    'is_agent_generated_prompt',
]
