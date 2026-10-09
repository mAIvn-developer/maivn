"""Category reporting over physical decision receipts and caller-owned fallbacks."""

from __future__ import annotations

from typing import TYPE_CHECKING

from maivn._internal.token_usage import categorized_token_usage

if TYPE_CHECKING:
    from collections.abc import Iterable, Mapping

    from maivn._internal.decisions.models import DecisionResult
    from maivn._internal.token_usage import TokenUsageByCategory, TokenUsageCategory


def summarize_decision_usage(
    results: Iterable[DecisionResult],
    *,
    llm_usage: Mapping[str, object] | None = None,
    llm_invoked: bool = False,
) -> TokenUsageByCategory:
    """Count each batch owner once and preserve known usage on rejected answers.

    Fallbacks declare their capability; a regular LLM remains LLM usage even
    though it answers a categorical question. An explicitly local fallback has
    no token category. This does not inspect messages, tool outputs or providers,
    calculate charges, or convert tokens to LLM equivalents.
    """
    records: list[tuple[TokenUsageCategory, object, object]] = []
    for result in results:
        for attempt in (result.primary, result.fallback):
            if (
                attempt is None
                or not attempt.usage_owner
                or attempt.token_category is None
                or attempt.status in ('blocked', 'disabled')
            ):
                continue
            usage = attempt.usage
            records.append(
                (
                    attempt.token_category,
                    usage.input_tokens if usage else None,
                    usage.output_tokens if usage else None,
                )
            )
    if llm_invoked or llm_usage is not None:
        usage = llm_usage or {}
        records.append(('llm', usage.get('input_tokens'), usage.get('output_tokens')))
    return categorized_token_usage(records)
