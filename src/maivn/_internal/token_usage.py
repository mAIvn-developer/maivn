"""Public token-count model used by SDK reporting adapters.

Cost and provider accounting are intentionally outside the public SDK contract.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Annotated, Literal, TypeAlias

from pydantic import BaseModel, ConfigDict, Field, computed_field

if TYPE_CHECKING:
    from collections.abc import Iterable, Mapping

# MARK: Client Model


class TokenUsage(BaseModel):
    """Token usage metrics - public SDK model.

    This model contains only token counts for SDK consumers. Cost information
    is intentionally excluded from the public API.

    Attributes:
        total_tokens: Total number of tokens used.
        input_tokens: Number of input tokens used.
        output_tokens: Number of output tokens used.
        cache_read_tokens: Number of tokens read from cache.
        cache_creation_tokens: Number of tokens written to cache.
        reasoning_tokens: Number of reasoning/thinking tokens used.

    """

    total_tokens: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    cache_read_tokens: int = 0
    cache_creation_tokens: int = 0
    reasoning_tokens: int = 0


# These categories describe capabilities, never a provider brand or token value.
TokenUsageCategory: TypeAlias = Literal['llm', 'decision']
_CategoryCount = Annotated[int, Field(ge=0)]


class TokenCategoryUsage(BaseModel):
    """Counts for one capability; absent receipts remain unknown.

    The known subtotal is useful even when a timeout prevents a complete total.
    Total tokens use input plus output, like the legacy SDK telemetry surface;
    they are not currency, quota units, or interchangeable across categories.
    """

    model_config = ConfigDict(frozen=True, strict=True, extra='forbid')
    input_tokens: _CategoryCount | None = 0
    output_tokens: _CategoryCount | None = 0
    known_input_tokens: _CategoryCount = 0
    known_output_tokens: _CategoryCount = 0
    unknown_input_calls: _CategoryCount = 0
    unknown_output_calls: _CategoryCount = 0

    @computed_field
    @property
    def total_tokens(self) -> int | None:
        """Return a total only when both counters are complete."""
        if self.input_tokens is None or self.output_tokens is None:
            return None
        return self.input_tokens + self.output_tokens


class TokenUsageByCategory(BaseModel):
    """Separate LLM and decision usage without inventing an equivalent total."""

    model_config = ConfigDict(frozen=True, strict=True, extra='forbid')
    llm: TokenCategoryUsage = Field(default_factory=TokenCategoryUsage)
    decision: TokenCategoryUsage = Field(default_factory=TokenCategoryUsage)


def categorized_token_usage(
    records: Iterable[tuple[TokenUsageCategory, object, object]],
) -> TokenUsageByCategory:
    """Sum owned counters while preserving unknowns independently per category."""
    buckets = {'llm': [0, 0, 0, 0], 'decision': [0, 0, 0, 0]}
    for category, input_tokens, output_tokens in records:
        bucket = buckets[category]
        for index, value in enumerate((input_tokens, output_tokens)):
            if type(value) is int and value >= 0:
                bucket[index] += value
            else:
                bucket[index + 2] += 1
    totals: dict[str, TokenCategoryUsage] = {}
    for category, (known_input, known_output, unknown_input, unknown_output) in buckets.items():
        totals[category] = TokenCategoryUsage(
            input_tokens=None if unknown_input else known_input,
            output_tokens=None if unknown_output else known_output,
            known_input_tokens=known_input,
            known_output_tokens=known_output,
            unknown_input_calls=unknown_input,
            unknown_output_calls=unknown_output,
        )
    return TokenUsageByCategory(llm=totals['llm'], decision=totals['decision'])


def model_token_usage(usage: Mapping[str, object]) -> TokenUsageByCategory:
    """Categorize a reported invocation; missing usage is not a free invocation."""
    return categorized_token_usage([('llm', usage.get('input_tokens'), usage.get('output_tokens'))])
