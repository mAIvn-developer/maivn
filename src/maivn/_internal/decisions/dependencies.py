# ruff: noqa: PLC0415 - lazy imports prevent client/scope initialization cycles.
"""Lazy decision dependencies for explicitly managed function calls."""

from __future__ import annotations

import asyncio
import inspect
from collections.abc import Awaitable, Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from types import MappingProxyType
from typing import TYPE_CHECKING, Literal, TypeAlias, TypeVar, cast, get_type_hints

from maivn._internal.api.async_stream import owning_tool_loop, run_blocking
from maivn._internal.compat.decorators import (
    INTERRUPT_DEPENDENCIES_ATTR,
    PRIVATE_DATA_DEPENDENCIES_ATTR,
    TOOL_DEPENDENCIES_ATTR,
)

if TYPE_CHECKING:
    from maivn._internal.decisions.flow_execution import DecisionStage
    from maivn._internal.decisions.flow_models import DecisionQuestion
    from maivn._internal.decisions.runtime import DecisionClient
    from maivn._internal.token_usage import TokenUsageByCategory


from maivn._internal.decisions.models import DecisionRequest, DecisionResult
from maivn._internal.decisions.usage import summarize_decision_usage
from maivn._internal.private_model_inputs import validate_private_models

DecisionResults: TypeAlias = Mapping[str, Mapping[str, DecisionResult]]
DECISION_DEPENDENCIES_ATTR = '__maivn_decision_dependencies__'
F = TypeVar('F', bound=Callable[..., object])


def _limit(value: int) -> None:
    if type(value) is not int or value < 1:
        message = 'max_concurrency must be a positive integer'
        raise ValueError(message)


@dataclass(frozen=True, slots=True)
class DecisionStep:
    """One immutable declaration; predecessors are local to its decorator group."""

    name: str
    questions: Mapping[str, DecisionQuestion] = field(kw_only=True, repr=False)
    after: str | tuple[str, ...] = field(default=(), kw_only=True)
    require: Mapping[str, str] | None = field(default=None, kw_only=True, repr=False)
    client: DecisionClient | None = field(default=None, kw_only=True, repr=False)
    max_concurrency: int = field(default=4, kw_only=True)

    def __post_init__(self) -> None:
        """Detach caller-owned declaration mappings."""
        object.__setattr__(self, 'questions', MappingProxyType(dict(self.questions)))
        if self.require is not None:
            object.__setattr__(self, 'require', MappingProxyType(dict(self.require)))


@dataclass(frozen=True, slots=True)
class DecisionDependency:
    """Validated decorator metadata retained on the original function."""

    stages: tuple[DecisionStage, ...] = field(repr=False)
    predecessors: Mapping[str, tuple[str, ...]] = field(repr=False)
    arg_name: str | None
    max_concurrency: int
    state: Callable[[Mapping[str, object]], object] | None = field(default=None, repr=False)


@dataclass(frozen=True, slots=True)
class DecisionCallResult:
    """Preserve native target values separately from local decisions."""

    status: Literal['completed', 'skipped']
    value: object = field(repr=False)
    decisions: DecisionResults = field(repr=False)
    skipped_by: str | None = None

    @property
    def token_usage_by_category(self) -> TokenUsageByCategory:
        """Report gate and fallback receipts; arbitrary target values are not usage."""
        return summarize_decision_usage(
            result for stage in self.decisions.values() for result in stage.values()
        )


def _group(  # noqa: PLR0913 - shared dependency declaration contract.
    client: DecisionClient,
    steps: Sequence[DecisionStep],
    arg_name: str | None,
    max_concurrency: int,
    state: Callable[[Mapping[str, object]], object] | None,
    *,
    native_batch: bool = False,
) -> DecisionDependency:
    from maivn._internal.decisions.flow import DecisionInvocationBuilder

    _limit(max_concurrency)
    if type(native_batch) is not bool:
        message = 'Decision native_batch must be boolean'
        raise ValueError(message)
    if state is not None and (not callable(state) or inspect.iscoroutinefunction(state)):
        message = 'Decision state selectors must be synchronous callables'
        raise TypeError(message)
    if not steps:
        message = 'Decision dependencies require at least one step'
        raise ValueError(message)
    builder = DecisionInvocationBuilder(None, client)
    predecessors: dict[str, tuple[str, ...]] = {}
    for step in steps:
        if not isinstance(cast('object', step), DecisionStep):
            message = 'Decision declarations must be DecisionStep records'
            raise TypeError(message)
        after = (step.after,) if isinstance(step.after, str) else step.after
        if not isinstance(cast('object', after), tuple) or any(
            type(name) is not str for name in after
        ):
            message = 'Decision predecessors must be a name or tuple of names'
            raise TypeError(message)
        predecessors[step.name] = after
        builder = builder.decide(
            step.name,
            client=step.client if step.client is not None else client,
            questions=step.questions,
            require=step.require,
            max_concurrency=step.max_concurrency,
            native_batch=native_batch,
        )
    remaining = set(predecessors)
    resolved: set[str] = set()
    while remaining:
        ready = {name for name in remaining if set(predecessors[name]) <= resolved}
        if not ready:
            message = 'Decision graph has missing predecessors or a cycle'
            raise ValueError(message)
        remaining -= ready
        resolved |= ready
    stage_attribute = '_stages'
    stages = cast('tuple[DecisionStage, ...]', getattr(builder, stage_attribute))
    return DecisionDependency(
        stages,
        MappingProxyType(predecessors),
        arg_name,
        max_concurrency,
        state,
    )


def validate_decision_dependencies(  # noqa: C901, PLR0912 - complete pure metadata validation.
    target: Callable[..., object],
) -> tuple[DecisionDependency, ...]:
    """Validate the entire plan without invoking factories, targets or providers."""
    metadata = cast('object', getattr(target, DECISION_DEPENDENCIES_ATTR, ()))
    if not isinstance(metadata, (tuple, list)):
        message = 'Decision dependency metadata must contain validated declarations'
        raise TypeError(message)
    values = cast('Sequence[object]', metadata)
    if any(not isinstance(value, DecisionDependency) for value in values):
        message = 'Decision dependency metadata must contain validated declarations'
        raise TypeError(message)
    groups = tuple(cast('Sequence[DecisionDependency]', values))
    if not groups:
        return ()
    if inspect.isclass(target) or inspect.isasyncgenfunction(target):
        message = 'Decision dependencies require an ordinary function target'
        raise TypeError(message)
    names: set[str] = set()
    args: set[str] = set()
    signature = inspect.signature(target)
    if any(
        parameter.kind
        in (
            inspect.Parameter.POSITIONAL_ONLY,
            inspect.Parameter.VAR_POSITIONAL,
            inspect.Parameter.VAR_KEYWORD,
        )
        for parameter in signature.parameters.values()
    ):
        message = 'Managed decision functions require explicit keyword-compatible parameters'
        raise TypeError(message)
    try:
        hints = get_type_hints(target, include_extras=True)
    except (NameError, TypeError):
        hints = {}
    collisions = {
        getattr(record, 'arg_name', None)
        for attr in (
            TOOL_DEPENDENCIES_ATTR,
            PRIVATE_DATA_DEPENDENCIES_ATTR,
            INTERRUPT_DEPENDENCIES_ATTR,
        )
        for record in cast('Iterable[object]', getattr(target, attr, ()))
    }
    for group in groups:
        for stage in group.stages:
            if stage.name in names:
                message = 'Decision stage names must be globally unique'
                raise ValueError(message)
            names.add(stage.name)
        arg = group.arg_name
        if arg is None:
            continue
        parameter = signature.parameters.get(arg)
        if parameter is None or parameter.kind not in (
            inspect.Parameter.POSITIONAL_OR_KEYWORD,
            inspect.Parameter.KEYWORD_ONLY,
        ):
            message = 'Decision injection requires an existing keyword-compatible argument'
            raise ValueError(message)
        annotation = hints.get(arg, parameter.annotation)
        if annotation != DecisionResults:
            message = 'Decision injection must be annotated as DecisionResults'
            raise TypeError(message)
        if arg in args or arg in collisions:
            message = 'Decision injection argument collides with another dependency'
            raise ValueError(message)
        args.add(arg)
    return groups


def dependency_arg_names(target: Callable[..., object]) -> frozenset[str]:
    """Return all locally injected parameter names."""
    return frozenset(
        group.arg_name
        for group in validate_decision_dependencies(target)
        if group.arg_name is not None
    )


def depends_on_decision(  # noqa: PLR0913 - explicit opt-in dependency policy.
    *,
    client: DecisionClient,
    decisions: Sequence[DecisionStep],
    arg_name: str | None = None,
    max_concurrency: int = 4,
    state: Callable[[Mapping[str, object]], object] | None = None,
    native_batch: bool = False,
) -> Callable[[F], F]:
    """Attach lazy metadata and optionally select local factory input.

    Factories receive a snapshot of the bound input mapping, including defaults.
    A synchronous state selector narrows that local snapshot once per factory
    stage; its result must support deep copying. Static requests need no input.
    Factories still explicitly construct requests before local authorization.
    """
    group = _group(client, decisions, arg_name, max_concurrency, state, native_batch=native_batch)

    def decorate(target: F) -> F:
        existing = list(
            cast('Sequence[DecisionDependency]', getattr(target, DECISION_DEPENDENCIES_ATTR, ()))
        )
        setattr(target, DECISION_DEPENDENCIES_ATTR, [*existing, group])
        try:
            validate_decision_dependencies(target)
        except BaseException:
            if existing:
                setattr(target, DECISION_DEPENDENCIES_ATTR, existing)
            else:
                delattr(target, DECISION_DEPENDENCIES_ATTR)
            raise
        return target

    return decorate


def _bind(target: Callable[..., object], arguments: Mapping[str, object]) -> dict[str, object]:
    from maivn._internal.decisions.flow_execution import immutable_results

    injected = dependency_arg_names(target)
    if injected.intersection(arguments):
        message = 'Caller cannot supply decision injection arguments'
        raise ValueError(message)
    signature = inspect.signature(target)
    if any(
        parameter.kind
        in (
            inspect.Parameter.POSITIONAL_ONLY,
            inspect.Parameter.VAR_POSITIONAL,
            inspect.Parameter.VAR_KEYWORD,
        )
        for parameter in signature.parameters.values()
    ):
        message = 'Managed decision functions require explicit keyword-compatible parameters'
        raise TypeError(message)
    bound = signature.bind(**dict(arguments), **{name: immutable_results({}) for name in injected})
    bound.apply_defaults()
    return {name: value for name, value in bound.arguments.items() if name not in injected}


async def execute_decision_call(  # noqa: C901 - one owned DAG scheduling loop.
    target: Callable[..., object],
    arguments: Mapping[str, object],
    call: Callable[[Mapping[str, object]], Awaitable[object]],
    operations: asyncio.Semaphore | None = None,
) -> DecisionCallResult:
    """Resolve the DAG and gates, then dispatch one validated local callback."""
    from maivn._internal.decisions.flow_execution import (
        context,
        drain,
        immutable_results,
        rejected,
        run_stage,
    )

    groups = validate_decision_dependencies(target)
    if not groups:
        if operations is None:
            value = await call(arguments)
        else:
            async with operations:
                value = await call(arguments)
        return DecisionCallResult('completed', value, immutable_results({}))
    bound = _bind(target, arguments)
    operations = (
        operations
        if operations is not None
        else asyncio.Semaphore(max((group.max_concurrency for group in groups), default=4))
    )
    stages = {stage.name: (stage, group) for group in groups for stage in group.stages}
    pending = set(stages)
    records: dict[str, Mapping[str, DecisionResult]] = {}
    running: dict[asyncio.Task[Mapping[str, DecisionResult]], str] = {}
    group_limits = {id(group): asyncio.Semaphore(group.max_concurrency) for group in groups}

    async def one(stage: DecisionStage, group: DecisionDependency) -> Mapping[str, DecisionResult]:
        selected: object = {}
        if any(not isinstance(question, DecisionRequest) for question in stage.questions.values()):
            selected = group.state(MappingProxyType(bound)) if group.state is not None else bound
        snapshot = context(
            selected, None, {name: records[name] for name in group.predecessors[stage.name]}
        )
        return await run_stage(
            stage, snapshot, operations, group_operations=group_limits[id(group)]
        )

    try:
        while pending or running:
            for name in tuple(pending):
                stage, group = stages[name]
                if set(group.predecessors[name]) <= records.keys():
                    running[asyncio.create_task(one(stage, group))] = name
                    pending.remove(name)
            done, _ = await asyncio.wait(running, return_when=asyncio.FIRST_COMPLETED)
            # Inspect every completion before scheduling any successors.
            skipped: str | None = None
            for task in done:
                name = running.pop(task)
                records[name] = task.result()
                if rejected(stages[name][0], records[name]):
                    skipped = name
            if skipped is not None:
                await drain(cast('list[asyncio.Task[object]]', list(running)))
                return DecisionCallResult('skipped', None, immutable_results(records), skipped)
        injected = dict(bound)
        for group in groups:
            if group.arg_name is not None:
                injected[group.arg_name] = immutable_results(
                    {stage.name: records[stage.name] for stage in group.stages}
                )
        async with operations:
            value = await call(MappingProxyType(injected))
        return DecisionCallResult('completed', value, immutable_results(records))
    except BaseException:
        await drain(cast('list[asyncio.Task[object]]', list(running)))
        raise


async def dispatch_decision_target(
    target: Callable[..., object], arguments: Mapping[str, object]
) -> object:
    """Dispatch a native target and retain worker ownership through cancellation."""
    if inspect.iscoroutinefunction(target):
        return await target(**arguments)
    with owning_tool_loop():
        worker = asyncio.create_task(asyncio.to_thread(target, **arguments))
        try:
            result = await asyncio.shield(worker)
        except asyncio.CancelledError:
            # A Python worker cannot be stopped; retain ownership until it finishes.
            while not worker.done():
                try:
                    await asyncio.shield(worker)
                except asyncio.CancelledError:  # noqa: PERF203 - cleanup survives repeated cancellation.
                    continue
                except Exception:  # noqa: BLE001 - retain caller cancellation after worker failure.
                    break
            if not worker.cancelled():
                worker.exception()
            raise
    return await cast('Awaitable[object]', result) if inspect.isawaitable(result) else result


@dataclass(frozen=True, slots=True)
class DecisionFunction:
    """Explicit managed entry point; direct function calls remain unchanged."""

    target: Callable[..., object] = field(repr=False)

    def __post_init__(self) -> None:
        """Validate declarations at managed entry point creation."""
        validate_decision_dependencies(self.target)

    def invoke(self, arguments: Mapping[str, object]) -> DecisionCallResult:
        """Execute a managed call outside an async loop."""
        return run_blocking(lambda: self.ainvoke(arguments))

    async def ainvoke(self, arguments: Mapping[str, object]) -> DecisionCallResult:
        """Execute decision work and preserve the native target value."""
        arguments = self._validated(arguments)
        return await execute_decision_call(
            self.target, arguments, lambda values: dispatch_decision_target(self.target, values)
        )

    def _validated(self, arguments: Mapping[str, object]) -> Mapping[str, object]:
        bound = _bind(self.target, arguments)
        hints = get_type_hints(self.target, include_extras=True)
        return {
            name: validate_private_models(hints[name], value, {}) if name in hints else value
            for name, value in bound.items()
        }

    def batch(
        self, inputs: Iterable[Mapping[str, object]], *, max_concurrency: int = 4
    ) -> list[DecisionCallResult]:
        """Return managed outcomes in input order."""
        return run_blocking(lambda: self.abatch(inputs, max_concurrency=max_concurrency))

    async def abatch(
        self, inputs: Iterable[Mapping[str, object]], *, max_concurrency: int = 4
    ) -> list[DecisionCallResult]:
        """Share one operation bound and drain every item on failure."""
        from maivn._internal.decisions.flow_execution import drain

        _limit(max_concurrency)
        prepared = [self._validated(item) for item in inputs]
        operations = asyncio.Semaphore(max_concurrency)
        tasks = [
            asyncio.create_task(
                execute_decision_call(
                    self.target,
                    item,
                    lambda values: dispatch_decision_target(self.target, values),
                    operations,
                )
            )
            for item in prepared
        ]
        try:
            return await asyncio.gather(*tasks)
        except BaseException:
            await drain(cast('list[asyncio.Task[object]]', tasks))
            raise


def decision_flow(target: Callable[..., object]) -> DecisionFunction:
    """Create an explicitly managed function entry point."""
    return DecisionFunction(target)
