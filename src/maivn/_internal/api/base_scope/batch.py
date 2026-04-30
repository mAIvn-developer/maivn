"""Batch invocation helpers for BaseScope."""

from __future__ import annotations

import asyncio
from collections.abc import Iterable
from concurrent.futures import ThreadPoolExecutor
from typing import Any

from maivn_shared import SessionResponse

# MARK: Concurrency


def resolve_max_concurrency(max_concurrency: int | None, input_count: int) -> int | None:
    if max_concurrency is not None and max_concurrency < 1:
        raise ValueError("max_concurrency must be greater than 0.")
    if input_count < 1:
        return 0
    if max_concurrency is None:
        return None
    return min(max_concurrency, input_count)


# MARK: Batch Mixin


class BaseScopeBatchMixin:
    def batch(
        self,
        inputs: Iterable[Any],
        *,
        max_concurrency: int | None = None,
        **invoke_kwargs: Any,
    ) -> list[SessionResponse]:
        """Invoke this scope for multiple inputs concurrently."""
        input_items = list(inputs)
        max_workers = resolve_max_concurrency(max_concurrency, len(input_items))
        if max_workers == 0:
            return []

        with ThreadPoolExecutor(max_workers=max_workers) as executor:
            futures = [
                executor.submit(self._invoke_batch_item, item, dict(invoke_kwargs))
                for item in input_items
            ]
            return [future.result() for future in futures]

    async def abatch(
        self,
        inputs: Iterable[Any],
        *,
        max_concurrency: int | None = None,
        **invoke_kwargs: Any,
    ) -> list[SessionResponse]:
        """Asynchronously invoke this scope for multiple inputs concurrently."""
        input_items = list(inputs)
        max_workers = resolve_max_concurrency(max_concurrency, len(input_items))
        if max_workers == 0:
            return []

        loop = asyncio.get_running_loop()
        with ThreadPoolExecutor(max_workers=max_workers) as executor:
            tasks = [
                loop.run_in_executor(
                    executor,
                    self._invoke_batch_item,
                    item,
                    dict(invoke_kwargs),
                )
                for item in input_items
            ]
            return list(await asyncio.gather(*tasks))

    def _invoke_batch_item(
        self,
        input_item: Any,
        invoke_kwargs: dict[str, Any],
    ) -> SessionResponse:
        invoke_fn = getattr(self, "invoke", None)
        if invoke_fn is None:
            raise AttributeError("Scope does not support invoke().")
        return invoke_fn(input_item, **invoke_kwargs)


__all__ = [
    "BaseScopeBatchMixin",
    "resolve_max_concurrency",
]
