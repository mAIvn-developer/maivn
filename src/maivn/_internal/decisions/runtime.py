"""Bounded decision routing with a mandatory caller-owned baseline."""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING

from pydantic import ValidationError

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable, Sequence
from time import perf_counter

from maivn._internal.api.async_stream import run_blocking
from maivn._internal.decisions.models import (
    DecisionAnswer,
    DecisionAttempt,
    DecisionConfig,
    DecisionPrediction,
    DecisionRequest,
    DecisionResult,
    DecisionStatus,
)
from maivn._internal.decisions.platform import (
    BatchDecisionBackend,
    DecisionBackend,
    PlatformDecisions,
    ProviderError,
)

if TYPE_CHECKING:
    from maivn._internal.client import Client


class DecisionClient:
    """Route public decisions through the mAIvn platform with a caller-owned baseline.

    ``authorize`` approves each complete request locally before anything leaves the
    process. ``client`` is an ordinary :class:`maivn.Client`; without one, a default
    client reads ``MAIVN_BASE_URL`` and ``MAIVN_API_KEY`` on first use. The platform
    chooses the decision model; no model name or decision-model key is configured here.
    ``session_id`` links every decision to that run; without it, decisions link to the open
    :func:`maivn.decisions.decision_run` or to the agent run whose local tool is executing.
    The baseline must honor the caller's privacy policy.
    """

    def __init__(
        self,
        *,
        fallback: Callable[[DecisionRequest], Awaitable[DecisionAnswer]],
        config: DecisionConfig,
        authorize: Callable[[DecisionRequest], bool],
        client: Client | None = None,
        session_id: str | None = None,
    ) -> None:
        self._provider: DecisionBackend = PlatformDecisions(
            authorize=authorize, client=client, session_id=session_id
        )
        self._fallback = fallback
        self._config = DecisionConfig.model_validate(config)

    @classmethod
    def _from_backend(
        cls,
        backend: DecisionBackend,
        *,
        fallback: Callable[[DecisionRequest], Awaitable[DecisionAnswer]],
        config: DecisionConfig,
    ) -> DecisionClient:
        """Route through an in-memory backend instead of the platform (internal test seam)."""
        instance = cls.__new__(cls)
        instance._provider = backend  # noqa: SLF001 - own class construction.
        instance._fallback = fallback  # noqa: SLF001 - own class construction.
        instance._config = DecisionConfig.model_validate(config)  # noqa: SLF001 - initialize this class's internal test seam.
        return instance

    @property
    def native_batch_question_limit(self) -> int:
        """Return one platform call's question limit, or one for scalar backends."""
        if self._config.enabled and isinstance(self._provider, BatchDecisionBackend):
            return self._config.max_batch_questions
        return 1

    def with_fallback(
        self,
        fallback: Callable[[DecisionRequest], Awaitable[DecisionAnswer]],
    ) -> DecisionClient:
        """Share the platform route and configuration with a separate local baseline.

        The original client and its fallback remain unchanged. Optional wrappers
        can preserve their own existing baseline without invoking a discarded
        larger-model fallback.
        """
        return DecisionClient._from_backend(self._provider, fallback=fallback, config=self._config)

    def decide(self, request: DecisionRequest) -> DecisionResult:
        """Run one decision synchronously outside an async event loop."""
        return run_blocking(lambda: self.adecide(request))

    async def adecide(self, request: DecisionRequest) -> DecisionResult:
        """Attempt the primary and invoke the explicit baseline at most once."""
        request = DecisionRequest.model_validate(request)
        started = perf_counter()
        failure: ProviderError | None = None
        prediction: DecisionPrediction | None = None
        if request.privacy != 'public' or (request.images and request.private_data):
            status = DecisionStatus.BLOCKED
        elif not self._config.enabled:
            status = DecisionStatus.DISABLED
        else:
            try:
                prediction = await asyncio.wait_for(
                    self._provider.predict(request),
                    timeout=self._config.timeout_seconds,
                )
                prediction = DecisionPrediction.model_validate(prediction)
                if prediction.value not in {choice.key for choice in request.choices}:
                    status = DecisionStatus.INVALID
                else:
                    status = (
                        DecisionStatus.ACCEPTED
                        if prediction.confidence >= self._config.min_confidence
                        else DecisionStatus.LOW_CONFIDENCE
                    )
            except asyncio.TimeoutError:
                status = DecisionStatus.TIMEOUT
            except ProviderError as exc:
                failure = exc
                status = exc.status
            except ValidationError:
                prediction = None
                status = DecisionStatus.INVALID
            except Exception:  # noqa: BLE001 - provider failures route to the baseline.
                status = DecisionStatus.UNAVAILABLE
        primary = DecisionAttempt(
            status=status,
            latency_seconds=perf_counter() - started,
            model=prediction.model if prediction else failure.model if failure else None,
            provider=prediction.provider if prediction else failure.provider if failure else None,
            confidence=prediction.confidence if prediction else None,
            usage=prediction.usage if prediction else failure.usage if failure else None,
        )
        return await self._finish(request, primary, prediction)

    async def _finish(
        self,
        request: DecisionRequest,
        primary: DecisionAttempt,
        prediction: DecisionPrediction | None,
    ) -> DecisionResult:
        if primary.status == DecisionStatus.ACCEPTED and prediction is not None:
            return DecisionResult(value=prediction.value, source='primary', primary=primary)
        started = perf_counter()
        answer = await self._fallback(request)
        try:
            answer = DecisionAnswer.model_validate(answer)
        except ValidationError:
            message = 'Baseline returned an invalid decision answer'
            raise ValueError(message) from None
        if answer.value not in {choice.key for choice in request.choices}:
            message = 'Baseline returned an invalid decision answer'
            raise ValueError(message)
        fallback = DecisionAttempt(
            status=DecisionStatus.ACCEPTED,
            latency_seconds=perf_counter() - started,
            model=answer.model,
            provider=answer.provider,
            usage=answer.usage,
            token_category=answer.token_category,
        )
        return DecisionResult(
            value=answer.value, source='fallback', primary=primary, fallback=fallback
        )

    def batch(
        self,
        requests: Sequence[DecisionRequest],
        *,
        max_concurrency: int = 4,
        native_batch: bool = False,
    ) -> list[DecisionResult]:
        """Return ordered decisions with bounded max_concurrency."""
        return run_blocking(
            lambda: self.abatch(
                requests, max_concurrency=max_concurrency, native_batch=native_batch
            )
        )

    async def abatch(
        self,
        requests: Sequence[DecisionRequest],
        *,
        max_concurrency: int = 4,
        native_batch: bool = False,
    ) -> list[DecisionResult]:
        """Cancel and drain all outstanding work on failure or caller cancellation."""
        if (
            isinstance(max_concurrency, bool)
            or type(max_concurrency) is not int
            or max_concurrency < 1
        ):
            message = 'Decision max_concurrency must be a positive integer'
            raise ValueError(message)
        if type(native_batch) is not bool:
            message = 'Decision native_batch must be boolean'
            raise ValueError(message)
        if (
            native_batch
            and self._config.enabled
            and isinstance(self._provider, BatchDecisionBackend)
        ):
            return await self._native_batch(requests, max_concurrency=max_concurrency)
        semaphore = asyncio.Semaphore(max_concurrency)

        async def one(request: DecisionRequest) -> DecisionResult:
            async with semaphore:
                return await self.adecide(request)

        tasks = [asyncio.create_task(one(request)) for request in requests]
        try:
            return await asyncio.gather(*tasks)
        except BaseException:
            for task in tasks:
                task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
            raise

    async def _native_batch(
        self,
        requests: Sequence[DecisionRequest],
        *,
        max_concurrency: int,
    ) -> list[DecisionResult]:
        validated = [DecisionRequest.model_validate(request) for request in requests]
        groups: dict[tuple[object, ...], list[int]] = {}
        units: list[list[int]] = []
        for index, request in enumerate(validated):
            if request.privacy == 'public' and not (request.images and request.private_data):
                key = (request.state, request.private_data, request.images)
                groups.setdefault(key, []).append(index)
            else:
                units.append([index])
        size = self._config.max_batch_questions
        for indices in groups.values():
            units.extend(indices[offset : offset + size] for offset in range(0, len(indices), size))
        semaphore = asyncio.Semaphore(max_concurrency)

        async def unit(indices: list[int]) -> list[DecisionResult]:
            async with semaphore:
                if validated[indices[0]].privacy != 'public' or (
                    validated[indices[0]].images and validated[indices[0]].private_data
                ):
                    return [await self.adecide(validated[indices[0]])]
                return await self._native_unit([validated[index] for index in indices])

        tasks = [asyncio.create_task(unit(indices)) for indices in units]
        try:
            chunks = await asyncio.gather(*tasks)
        except BaseException:
            for task in tasks:
                task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
            raise
        ordered = {
            index: result
            for indices, chunk in zip(units, chunks, strict=True)
            for index, result in zip(indices, chunk, strict=True)
        }
        return [ordered[index] for index in range(len(validated))]

    async def _native_unit(self, requests: list[DecisionRequest]) -> list[DecisionResult]:  # noqa: C901 - preserve each independent outcome.
        provider = self._provider
        if not isinstance(provider, BatchDecisionBackend):
            message = 'Native batch capability unavailable'
            raise TypeError(message)
        started = perf_counter()
        try:
            outcomes = await asyncio.wait_for(
                provider.predict_batch(requests),
                timeout=self._config.timeout_seconds,
            )
            if len(outcomes) != len(requests):
                raise ProviderError(DecisionStatus.INVALID, provider=None)  # noqa: TRY301 - guard provider contract.
        except asyncio.TimeoutError:
            outcomes = [ProviderError(DecisionStatus.TIMEOUT, provider=None) for _ in requests]
        except ProviderError as exc:
            outcomes = [exc] + [
                ProviderError(exc.status, model=exc.model, provider=exc.provider)
                for _ in requests[1:]
            ]
        except Exception:  # noqa: BLE001 - provider failures route to the baseline.
            outcomes = [ProviderError(DecisionStatus.UNAVAILABLE, provider=None) for _ in requests]
        latency = perf_counter() - started
        results: list[DecisionResult] = []
        for index, (request, outcome) in enumerate(zip(requests, outcomes, strict=True)):
            prediction: DecisionPrediction | None = None
            if isinstance(outcome, ProviderError):
                status = outcome.status
                primary = DecisionAttempt(
                    status=status,
                    latency_seconds=latency,
                    model=outcome.model,
                    provider=outcome.provider,
                    usage=outcome.usage,
                    usage_scope='batch',
                    usage_batch_size=len(requests),
                    usage_owner=index == 0,
                )
            else:
                try:
                    prediction = DecisionPrediction.model_validate(outcome)
                except ValidationError:
                    status = DecisionStatus.INVALID
                else:
                    if prediction.value not in {choice.key for choice in request.choices}:
                        status = DecisionStatus.INVALID
                    else:
                        status = (
                            DecisionStatus.ACCEPTED
                            if prediction.confidence >= self._config.min_confidence
                            else DecisionStatus.LOW_CONFIDENCE
                        )
                primary = DecisionAttempt(
                    status=status,
                    latency_seconds=latency,
                    model=prediction.model if prediction else None,
                    provider=prediction.provider if prediction else None,
                    confidence=prediction.confidence if prediction else None,
                    usage=prediction.usage if prediction else None,
                    usage_scope='batch',
                    usage_batch_size=len(requests),
                    usage_owner=index == 0,
                )
            results.append(await self._finish(request, primary, prediction))
        return results
