"""Explicit typed argument mappings for bounded categorical decisions."""

from __future__ import annotations

import keyword
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from types import MappingProxyType
from typing import TYPE_CHECKING, cast

from maivn._internal.api.async_stream import run_blocking
from maivn._internal.decisions.models import DecisionRequest, DecisionResult, DecisionStatus
from maivn._internal.decisions.usage import summarize_decision_usage

if TYPE_CHECKING:
    from maivn._internal.decisions.runtime import DecisionClient
    from maivn._internal.token_usage import TokenUsageByCategory


def _typed_values(values: Mapping[str, bool | str]) -> Mapping[str, bool | str]:
    if not isinstance(cast('object', values), Mapping):
        message = 'Decision argument values must be a mapping'
        raise TypeError(message)
    detached = dict(values)
    if any(
        type(key) is not str or type(value) not in (bool, str) for key, value in detached.items()
    ):
        message = 'Decision argument values require string keys and boolean or string values'
        raise ValueError(message)
    return MappingProxyType(detached)


@dataclass(frozen=True, slots=True)
class DecisionArgument:
    """An explicit field name, approved question, and exact typed category mapping."""

    name: str
    question: DecisionRequest = field(repr=False)
    values: Mapping[str, bool | str] = field(repr=False)

    def __post_init__(self) -> None:
        """Validate and detach configuration without running inference."""
        if (
            type(self.name) is not str
            or not self.name.isidentifier()
            or keyword.iskeyword(self.name)
        ):
            message = 'Decision argument names must be valid Python identifiers'
            raise ValueError(message)
        if not isinstance(cast('object', self.question), DecisionRequest):
            message = 'Decision argument questions must be DecisionRequest records'
            raise TypeError(message)
        question = DecisionRequest.model_validate(self.question)
        values = _typed_values(self.values)
        if set(values) != {choice.key for choice in question.choices}:
            message = 'Decision argument values must map exactly all question choices'
            raise ValueError(message)
        object.__setattr__(self, 'question', question)
        object.__setattr__(self, 'values', values)


@dataclass(frozen=True, slots=True)
class DecisionArgumentPreparation:
    """Resolved primary values and diagnostic history, including unresolved fields."""

    values: Mapping[str, bool | str] = field(repr=False)
    decisions: Mapping[str, DecisionResult] = field(repr=False)

    @property
    def token_usage_by_category(self) -> TokenUsageByCategory:
        """Report owned argument decisions and their declared fallbacks."""
        return summarize_decision_usage(self.decisions.values())

    def __post_init__(self) -> None:
        """Detach all mappings and revalidate immutable nested result records."""
        object.__setattr__(self, 'values', _typed_values(self.values))
        object.__setattr__(
            self,
            'decisions',
            MappingProxyType(
                {
                    name: DecisionResult.model_validate(result)
                    for name, result in self.decisions.items()
                }
            ),
        )


class DecisionArgumentPreparer:
    """Prepare declared boolean or string values through a caller-configured client."""

    def __init__(self, client: DecisionClient) -> None:
        """Retain explicit routing policy without performing inference."""
        self._client = client

    def prepare(
        self,
        arguments: Sequence[DecisionArgument],
        *,
        max_concurrency: int = 4,
        native_batch: bool = False,
    ) -> DecisionArgumentPreparation:
        """Run preparation synchronously outside an async event loop."""
        return run_blocking(
            lambda: self.aprepare(
                arguments, max_concurrency=max_concurrency, native_batch=native_batch
            )
        )

    async def aprepare(
        self,
        arguments: Sequence[DecisionArgument],
        *,
        max_concurrency: int = 4,
        native_batch: bool = False,
    ) -> DecisionArgumentPreparation:
        """Validate all declarations before one bounded, ordered, draining client batch."""
        if type(max_concurrency) is not int or max_concurrency < 1:
            message = 'Decision max_concurrency must be a positive integer'
            raise ValueError(message)
        if not isinstance(cast('object', arguments), Sequence):
            message = 'Decision arguments must be a sequence of DecisionArgument records'
            raise TypeError(message)
        detached: list[DecisionArgument] = []
        names: set[str] = set()
        for argument in arguments:
            if not isinstance(cast('object', argument), DecisionArgument):
                message = 'Decision arguments must be DecisionArgument records'
                raise TypeError(message)
            validated = DecisionArgument(argument.name, argument.question, argument.values)
            if validated.name in names:
                message = 'Decision argument names must be unique'
                raise ValueError(message)
            names.add(validated.name)
            detached.append(validated)
        results = await self._client.abatch(
            [argument.question for argument in detached],
            max_concurrency=max_concurrency,
            native_batch=native_batch,
        )
        decisions = dict(zip((argument.name for argument in detached), results, strict=True))
        values = {
            argument.name: argument.values[result.value]
            for argument, result in zip(detached, results, strict=True)
            if result.source == 'primary' and result.primary.status == DecisionStatus.ACCEPTED
        }
        return DecisionArgumentPreparation(values, decisions)
