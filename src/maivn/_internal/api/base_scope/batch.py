"""Batch invocation helpers for BaseScope."""

# pyright: strict
from __future__ import annotations

import asyncio
from collections.abc import Iterable
from concurrent.futures import ThreadPoolExecutor
from typing import Protocol, cast

from maivn_shared import SessionResponse

# MARK: Types


class _BatchInvocationScope(Protocol):
    def invoke(self, input_item: object, **invoke_kwargs: object) -> SessionResponse: ...


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
        inputs: Iterable[object],
        *,
        max_concurrency: int | None = None,
        **invoke_kwargs: object,
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
        inputs: Iterable[object],
        *,
        max_concurrency: int | None = None,
        **invoke_kwargs: object,
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
        input_item: object,
        invoke_kwargs: dict[str, object],
    ) -> SessionResponse:
        scope = cast(_BatchInvocationScope, cast(object, self))
        return scope.invoke(input_item, **invoke_kwargs)


__all__ = [
    "BaseScopeBatchMixin",
    "resolve_max_concurrency",
]
