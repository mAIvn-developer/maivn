"""Immutable decision contracts, containing no arbitrary message objects."""

from __future__ import annotations

from collections.abc import Mapping
from enum import Enum
from typing import TYPE_CHECKING, Annotated, Literal, cast

from maivn_contracts.decisions import DecisionImage
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from maivn._internal.decisions.usage import summarize_decision_usage
from maivn._internal.token_usage import TokenUsageByCategory, TokenUsageCategory

if TYPE_CHECKING:
    from typing_extensions import Self

Text = Annotated[str, Field(min_length=1, max_length=16384)]
PrivateText = Annotated[str, Field(min_length=1, max_length=4096)]
Key = Annotated[str, Field(min_length=1, max_length=128, pattern=r'\S')]
Probability = Annotated[float, Field(ge=0, le=1, allow_inf_nan=False)]
Count = Annotated[int, Field(ge=0)]


class _Frozen(BaseModel):
    model_config = ConfigDict(
        frozen=True,
        strict=True,
        extra='forbid',
        hide_input_in_errors=True,
        revalidate_instances='always',
    )


class DecisionChoice(_Frozen):
    """One declared categorical answer."""

    key: Key
    description: str = Field(min_length=1, max_length=2048, repr=False)


class DecisionRequest(_Frozen):
    """Complete immutable content submitted for local authorization."""

    name: Key
    state: Text = Field(repr=False)
    images: tuple[DecisionImage, ...] = Field(
        default=(), max_length=4, repr=False, exclude_if=lambda images: not images
    )
    instructions: str = Field(min_length=1, max_length=4096, repr=False)
    choices: tuple[DecisionChoice, ...] = Field(min_length=2, max_length=32, repr=False)
    privacy: Literal['public', 'private', 'unknown'] = 'unknown'
    # Values marked private inside otherwise public text (key -> value, the invoke
    # convention). The platform replaces them with ``{_{key}_}`` placeholders before the
    # decision model sees anything; a placeholder verdict is resolved locally.
    private_data: tuple[tuple[Key, PrivateText], ...] = Field(default=(), max_length=64, repr=False)

    @field_validator('private_data', mode='before')
    @classmethod
    def _private_pairs(cls, value: object) -> object:
        """Accept a mapping; store sorted pairs so the request stays immutable."""
        if isinstance(value, Mapping):
            pairs = cast('Mapping[object, object]', value).items()
            return tuple(sorted(((str(key), item) for key, item in pairs), key=lambda p: p[0]))
        return value

    @property
    def private_values(self) -> dict[str, str]:
        """Return the marked private values as a mapping."""
        return dict(self.private_data)

    @model_validator(mode='after')
    def unique_choices(self) -> Self:
        """Reject ambiguous categories and private-value declarations."""
        if len({choice.key for choice in self.choices}) != len(self.choices):
            message = 'Decision choice keys must be unique'
            raise ValueError(message)
        if len({key for key, _value in self.private_data}) != len(self.private_data):
            message = 'Decision private data keys must be unique'
            raise ValueError(message)
        return self


class DecisionConfig(_Frozen):
    """Bounded routing policy; the platform chooses the decision model."""

    min_confidence: Probability = 0.8
    timeout_seconds: float = Field(default=5.0, gt=0, allow_inf_nan=False)
    enabled: bool = True
    max_batch_questions: int = Field(default=32, ge=1, le=128)


class DecisionUsage(_Frozen):
    """Platform-reported decision tokens; missing counts remain unknown."""

    input_tokens: Count | None = None
    output_tokens: Count | None = None


class DecisionAnswer(_Frozen):
    """A categorical answer with truthful provider metadata."""

    value: Key
    provider: Key
    model: Key | None = None
    usage: DecisionUsage | None = None
    # A categorical answer does not make a regular LLM a decision model.
    # Explicit None declares a local baseline with no model invocation.
    token_category: TokenUsageCategory | None = Field(default='llm')

    @model_validator(mode='after')
    def local_answer_has_no_model_usage(self) -> Self:
        """Reject counters on an explicitly local baseline."""
        if self.token_category is None and self.usage is not None:
            message = 'Local answers cannot report model token usage'
            raise ValueError(message)
        return self


class DecisionPrediction(DecisionAnswer):
    """A provider answer with a provider-reported confidence field."""

    confidence: Probability
    token_category: TokenUsageCategory | None = Field(default='decision')

    @model_validator(mode='after')
    def prediction_requires_decision_category(self) -> Self:
        """Keep primary decision provider metadata in the declared capability."""
        if self.token_category != 'decision':  # noqa: S105 - Capability label, not a secret.
            message = 'Decision predictions require the decision token category'
            raise ValueError(message)
        return self


class DecisionStatus(str, Enum):
    """Safe, content-free attempt outcomes."""

    ACCEPTED = 'accepted'
    BLOCKED = 'blocked'
    DISABLED = 'disabled'
    TIMEOUT = 'timeout'
    UNAVAILABLE = 'unavailable'
    INVALID = 'invalid'
    LOW_CONFIDENCE = 'low_confidence'


class DecisionAttempt(_Frozen):
    """Safe diagnostics for an attempted route."""

    status: DecisionStatus
    latency_seconds: float = Field(ge=0, allow_inf_nan=False)
    model: Key | None = None
    provider: Key | None = None
    confidence: Probability | None = None
    usage: DecisionUsage | None = None
    # For native batches only the first result owns the platform call's receipt.
    # Every member exposes the scope and size; followers have usage=None.
    usage_scope: Literal['individual', 'batch'] = 'individual'
    usage_batch_size: int | None = Field(default=None, ge=1)
    token_category: TokenUsageCategory | None = Field(default='decision')
    usage_owner: bool = True


class DecisionResult(_Frozen):
    """Final category and diagnostic history."""

    value: Key
    source: Literal['primary', 'fallback']
    primary: DecisionAttempt
    fallback: DecisionAttempt | None = None

    @property
    def token_usage_by_category(self) -> TokenUsageByCategory:
        """Return separately categorized primary and fallback receipts."""
        return summarize_decision_usage([self])
