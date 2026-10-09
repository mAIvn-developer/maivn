"""Immutable context and outcomes for one decision-bound invocation."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Literal, TypeAlias

from pydantic import Field

from maivn._internal.decisions.models import DecisionRequest, DecisionResult
from maivn._internal.decisions.usage import summarize_decision_usage
from maivn._internal.models import InvokeResponse, StreamEvent

if TYPE_CHECKING:
    from maivn._internal.token_usage import TokenUsageByCategory


@dataclass(frozen=True, slots=True)
class DecisionContext:
    """Local stage snapshot; it never grants permission to export its contents."""

    input: object = field(repr=False)
    output: InvokeResponse | None = field(repr=False)
    decisions: Mapping[str, Mapping[str, DecisionResult]]

    @property
    def original_input(self) -> object:
        """Return the original input rather than explicitly mapped messages."""
        return self.input


DecisionQuestion: TypeAlias = DecisionRequest | Callable[[DecisionContext], DecisionRequest]


@dataclass(frozen=True, slots=True)
class DecisionInvocationResult:
    """Keep the invocation response and decision accounting separate."""

    status: Literal['completed', 'skipped']
    response: InvokeResponse | None = field(repr=False)
    decisions: Mapping[str, Mapping[str, DecisionResult]]
    skipped_by: str | None = None

    @property
    def token_usage_by_category(self) -> TokenUsageByCategory:
        """Keep invocation LLM usage separate from all before/after decisions."""
        return summarize_decision_usage(
            (result for stage in self.decisions.values() for result in stage.values()),
            llm_usage=self.response.usage if self.response is not None else None,
            llm_invoked=self.status == 'completed',
        )


class DecisionStreamEvent(StreamEvent):
    """Content-free lifecycle event with an excluded local final outcome."""

    outcome: DecisionInvocationResult | None = Field(default=None, exclude=True, repr=False)
