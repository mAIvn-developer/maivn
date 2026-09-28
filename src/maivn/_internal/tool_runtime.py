"""SDK-local function-tool execution driven by canonical SSE events."""

from __future__ import annotations

import asyncio
import inspect
import logging
import time
from collections.abc import Awaitable, Callable, Mapping, Sequence
from dataclasses import dataclass
from typing import TYPE_CHECKING, cast, get_type_hints

from maivn_contracts.artifacts import GeneratedFile, GeneratedFiles
from maivn_contracts.tools import (
    ErrorToolOutcome,
    OkToolOutcome,
    ToolCall,
    ToolOutcomeError,
    ToolOutcomeVariant,
)
from pydantic import BaseModel, ValidationError
from pydantic_core import to_jsonable_python

from maivn._internal.api.async_stream import owning_tool_loop
from maivn._internal.compat.decorators import (
    INTERRUPT_DEPENDENCIES_ATTR,
    PRIVATE_DATA_DEPENDENCIES_ATTR,
    TOOL_DEPENDENCIES_ATTR,
    InterruptDependency,
    PrivateDataDependency,
    ToolDependency,
)
from maivn._internal.compat.interrupts import typed_interrupt_answer
from maivn._internal.hooks import hook_bindings, safe_hook_error
from maivn._internal.private_model_inputs import validate_private_models
from maivn._internal.private_placeholders import resolve_private_placeholders
from maivn._internal.private_workspace import PrivateWorkspaceValues, workspace_scope
from maivn._internal.reporting.context import get_current_reporter
from maivn._internal.retained_dependencies import validated_retained_dependencies

if TYPE_CHECKING:
    from maivn_contracts.tools.model import RetainedToolResult

    from maivn._internal.models import StreamEvent, ToolMetadata

logger = logging.getLogger(__name__)

_REVISION_INVALID_LINEAGE = 'sdk_dependency_revision_invalid_lineage'
_REVISION_ROOT_NOT_FAILED = 'sdk_dependency_revision_root_not_failed'
_REVISION_PARENT_MISMATCH = 'sdk_dependency_revision_parent_mismatch'
_REVISION_UNSUPPORTED = 'sdk_dependency_revision_unsupported'
_REVISION_NOT_OPTED_IN = 'sdk_dependency_revision_not_opted_in'
_REVISION_STALE = 'sdk_dependency_revision_stale'
_REVISION_CONFLICT = 'sdk_dependency_revision_conflict'
_REVISION_NO_DEPENDENCY_CHANGE = 'sdk_dependency_revision_no_dependency_change'


@dataclass(frozen=True, slots=True)
class HookFiring:
    """One developer hook callback that ran around a local tool call.

    Names and outcome only: the payload the callback received is the
    developer's own data and never travels with the firing.
    """

    name: str
    stage: str
    """``"before"`` or ``"after"``."""
    status: str
    """``"completed"`` or ``"failed"``."""
    source: str
    """``"tool"`` / ``"scope"`` / ``"swarm"`` - which level registered the hook."""
    target_type: str
    """``"tool"`` / ``"agent"`` / ``"swarm"`` - which card the firing attaches to."""
    target_name: str | None
    error: str | None
    elapsed_ms: int


class DependencyRevisionError(RuntimeError):
    """Value-free rejection for an unauthorized dependency reconstruction."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.sdk_error_code = code


@dataclass(frozen=True, slots=True)
class _CallRecord:
    """Local provenance for one accepted SDK tool call."""

    call_id: str
    invocation_id: str
    tool_name: str
    accepted_arguments: dict[str, object]
    error_code: str | None


@dataclass(frozen=True, slots=True)
class _ResultBinding:
    """One immutable producer result and the exact call that supplied it."""

    call_id: str
    invocation_id: str
    result: object


@dataclass(frozen=True, slots=True)
class _RevisionContext:
    """The isolated result branch selected by an admitted revision call."""

    invocation_id: str
    root_call_id: str


class LocalToolRuntime:
    """Resolve invocation-local SDK callables and dependency arguments."""

    def __init__(
        self,
        tools: Sequence[ToolMetadata],
        *,
        private_data: Mapping[object, object] | None,
        canonical_interrupts: bool = False,
        thread_id: str | None = None,
    ) -> None:
        """Bind registered callables and private values for one invocation."""
        self._tools = {tool.name: tool for tool in tools}
        self._private_data = {str(key): value for key, value in (private_data or {}).items()}
        self._canonical_interrupts = canonical_interrupts
        self._thread_id = thread_id
        # Keys some other tool's dependency edge reads. An agent may call one tool many
        # times with different inputs, and `_results` is what gets INJECTED into a
        # consumer later - so an extra call must not replace the value the graph was
        # built on. Without this, calling a producer again before its consumer runs feeds
        # the consumer the last call's result instead of the planned one, silently.
        self._dependency_target_keys = frozenset(
            dependency.tool_name
            for tool in tools
            for dependency in _metadata_list(tool.target, TOOL_DEPENDENCIES_ATTR)
            if isinstance(dependency, ToolDependency)
        ) | frozenset(
            dependency.tool_id
            for tool in tools
            for dependency in _metadata_list(tool.target, TOOL_DEPENDENCIES_ATTR)
            if isinstance(dependency, ToolDependency)
        )
        self._results: dict[str, object] = {}
        self._calls: dict[str, _CallRecord] = {}
        self._base_bindings: dict[tuple[str, str], _ResultBinding] = {}
        self._revision_bindings: dict[tuple[str, str, str], _ResultBinding] = {}
        self._revision_claims: set[tuple[str, str, str]] = set()
        # Dependency-fed keys already spoken for by an in-flight or finished call.
        self._reserved_keys: set[str] = set()
        self._failures: dict[str, str] = {}
        self._completion_events: dict[str, asyncio.Event] = {}
        self._input_result_batches: dict[str, set[tuple[str, str]]] = {}
        self._serialized_locks: dict[int, asyncio.Lock] = {}
        self._workspace_values: dict[tuple[int, str | None], PrivateWorkspaceValues] = {}
        self._private_workspace_calls: set[str] = set()
        self._file_sources: dict[str, tuple[bytes | None, ...]] = {}
        self._file_output_models: dict[str, type[GeneratedFile | GeneratedFiles]] = {}

    def take_generated_file_sources(self, tool_call: ToolCall) -> tuple[bytes | None, ...]:
        """Transfer only this exact completed call's local source bytes to custody intake."""
        return self._file_sources.pop(tool_call.call_id, ())

    def private_data_keys_for_call(self, tool_call: ToolCall) -> tuple[str, ...]:
        """Return custody markers for generated outputs in this private invocation.

        A later render often receives only a handle to source authored earlier.
        It must inherit private custody even without a direct data dependency.
        """
        tool = self._tools.get(tool_call.spec_ref.tool_id)
        if tool is None:
            return ()
        return tuple(
            dict.fromkeys(
                (
                    *self._private_data,
                    *(
                        ('sdk_private_source',)
                        if tool_call.call_id in self._private_workspace_calls
                        else ()
                    ),
                    *(
                        dependency.data_key
                        for dependency in _metadata_list(
                            tool.target, PRIVATE_DATA_DEPENDENCIES_ATTR
                        )
                        if isinstance(dependency, PrivateDataDependency)
                    ),
                )
            )
        )

    def metadata_for_call(self, tool_call: ToolCall) -> ToolMetadata | None:
        """Return the registered local metadata for an SDK call without executing it."""
        if tool_call.spec_ref.namespace != 'sdk':
            return None
        tool = self._tools.get(tool_call.spec_ref.tool_id)
        output_model = self._file_output_models.get(tool_call.call_id)
        if tool is not None and output_model is not None:
            # Nominal local values require custody intake even when their callable
            # omitted a return annotation. This evidence belongs to this call only.
            return tool.model_copy(update={'output_schema': output_model.model_json_schema()})
        return tool

    def is_deferred_call(self, tool_call: ToolCall) -> bool:
        """Identify an interrupt placeholder whose arguments the server must bind."""
        tool = self.metadata_for_call(tool_call)
        return (
            self._canonical_interrupts
            and tool is not None
            and _has_missing_interrupt_argument(tool, tool_call.arguments)
        )

    async def outcome_for_event(  # noqa: C901, PLR0911, PLR0912, PLR0915 - dependency lifecycle owner.
        self,
        event: StreamEvent,
        *,
        on_started: Callable[[ToolCall], None] | None = None,
        on_completed: Callable[[ToolCall, ToolOutcomeVariant], None] | None = None,
        on_hook: Callable[[HookFiring], None] | None = None,
    ) -> ToolOutcomeVariant | None:
        """Execute an actionable SDK tool-start event and return its owned outcome.

        ``on_hook`` receives one :class:`HookFiring` per developer callback that
        runs around the call, in the order the callbacks fire.
        """
        if event.event_type != 'system_tool_start':
            return None
        raw_call = event.payload.get('tool_call')
        if not isinstance(raw_call, Mapping):
            return None
        tool_call = ToolCall.model_validate(raw_call)
        if tool_call.spec_ref.namespace != 'sdk':
            return None

        tool = self._tools.get(tool_call.spec_ref.tool_id)
        if tool is None:
            started = time.perf_counter()
            outcome = _error_outcome(
                tool_call.call_id,
                started=started,
                code='unknown_sdk_tool',
                message=f'SDK tool is not registered: {tool_call.spec_ref.tool_id}',
            )
            if on_completed is not None:
                on_completed(tool_call, outcome)
            return outcome
        if self.is_deferred_call(tool_call):
            return None
        revision_requested = tool_call.lineage.relation == 'dependency_revision'
        revision: _RevisionContext | None = None
        result_keys = tuple(dict.fromkeys((tool.name, tool_call.spec_ref.tool_id)))
        completion_events: tuple[asyncio.Event, ...] = ()
        # Reserved on ARRIVAL, not on completion. Checking `key in self._results` when the
        # call finishes picks whichever concurrent call returns first, and in a parallel
        # batch that can be an extra invocation rather than the producer the graph was
        # planned around. Arrival order is the same order the server bound its own
        # planned nodes in, so reserving here agrees with it. This runs to completion
        # without awaiting, so two calls in one batch cannot both win a key.
        owned_keys: list[str] = []
        started: float | None = None
        private_workspace = False
        arguments: dict[str, object] | None = None
        target_execution_started = False
        try:
            if revision_requested:
                revision = self._revision_context(tool_call, tool)
            else:
                self._begin_input_round(tool_call, tool, result_keys)
                completion_events = tuple(
                    self._completion_events.setdefault(key, asyncio.Event()) for key in result_keys
                )
                for key in result_keys:
                    if key not in self._dependency_target_keys:
                        owned_keys.append(key)
                    elif key not in self._reserved_keys:
                        self._reserved_keys.add(key)
                        owned_keys.append(key)
            retained = validated_retained_dependencies(
                tool_call,
                _tool_dependencies(tool),
                thread_id=self._thread_id,
                private_data=self._private_data,
            )
            arguments = await self._resolved_arguments(
                tool,
                tool_call.arguments,
                revision=revision,
                retained=retained,
            )
            if revision is not None:
                self._require_revised_root_dependency(tool_call, tool, arguments, revision)
            owner = getattr(tool.target, '__self__', None)
            workspace_check = getattr(owner, 'sdk_private_workspace', None)

            async def assess_workspace() -> None:
                nonlocal private_workspace
                if callable(workspace_check):
                    # Trusted callbacks may acquire a workspace RLock. Run them
                    # in a worker while retaining this toolset's execution lane.
                    prior_private = private_workspace
                    private_workspace = True
                    selected = await asyncio.to_thread(workspace_check, arguments)
                    private_workspace = (
                        prior_private or bool(self._private_data) or selected is True
                    )
                    if private_workspace:
                        self._private_workspace_calls.add(tool_call.call_id)

            started = time.perf_counter()
            target_execution_started = True
            if on_started is not None:
                on_started(tool_call)
            _fire_execution_hook(
                tool.before_execute,
                tool=tool,
                tool_id=tool_call.spec_ref.tool_id,
                stage='before',
                arguments=arguments,
                on_hook=on_hook,
            )
            raw_result = await self._call_target_in_order(tool, arguments, assess_workspace)
            _fire_execution_hook(
                tool.after_execute,
                tool=tool,
                tool_id=tool_call.spec_ref.tool_id,
                stage='after',
                arguments=arguments,
                result=raw_result,
                on_hook=on_hook,
            )
            if not revision_requested:
                for key in owned_keys:
                    # A successful retry supersedes the failure its earlier attempt
                    # recorded. Dependents check failures before results, so a stale
                    # entry would poison every consumer of this producer forever.
                    self._failures.pop(key, None)
                    self._results[key] = raw_result
                    self._base_bindings.setdefault(
                        (tool_call.lineage.invocation_id, key),
                        _ResultBinding(
                            call_id=tool_call.call_id,
                            invocation_id=tool_call.lineage.invocation_id,
                            result=raw_result,
                        ),
                    )
            else:
                branch = cast('_RevisionContext', revision)
                self._revision_bindings[(branch.invocation_id, branch.root_call_id, tool.name)] = (
                    _ResultBinding(
                        call_id=tool_call.call_id,
                        invocation_id=branch.invocation_id,
                        result=raw_result,
                    )
                )
            self._record_call(tool_call, tool, arguments, error_code=None)
            if isinstance(raw_result, (GeneratedFile, GeneratedFiles)):
                self._file_output_models[tool_call.call_id] = (
                    GeneratedFiles if isinstance(raw_result, GeneratedFiles) else GeneratedFile
                )
                files = (
                    raw_result.files if isinstance(raw_result, GeneratedFiles) else (raw_result,)
                )
                self._file_sources[tool_call.call_id] = tuple(
                    source
                    if isinstance(source := getattr(item, '_source_archive', None), bytes)
                    else None
                    for item in files
                )
            result = to_jsonable_python(raw_result)
            if private_workspace and not isinstance(raw_result, (GeneratedFile, GeneratedFiles)):
                result = self._source_values(
                    tool, result if workspace_scope(result) else arguments
                ).shield(result)
            outcome = OkToolOutcome(
                call_id=tool_call.call_id,
                duration_ms=_duration_ms(started),
                status='ok',
                result=result,
            )
            if on_completed is not None:
                on_completed(tool_call, outcome)
            return outcome  # noqa: TRY300 - callback must precede the successful return.
        except Exception as exc:  # noqa: BLE001 - callable failures cross the SDK boundary.
            message = str(exc) or type(exc).__name__
            error_code = 'sdk_tool_error'
            if private_workspace:
                message = 'Private workspace operation refused'
                error_code = 'sdk_private_workspace_error'
                if getattr(type(exc), 'sdk_error_code', None) == 'sdk_composition_reference_error':
                    error_code = 'sdk_composition_reference_error'
                    message = (
                        'Composition reference is unavailable in this workspace. '
                        'Call compose_artifact for text/tables in this toolset and workspace, '
                        'or compose_artifact_image for an attached image. '
                        'Retry with the returned reference.'
                    )
            sdk_error_code = getattr(exc, 'sdk_error_code', None)
            if isinstance(sdk_error_code, str):
                error_code = sdk_error_code
            if sdk_error_code == 'sdk_workbook_range_overlap':
                # This fixed recovery text is safe in ordinary and private custody.
                # Never carry the exception's range identifiers across the boundary.
                error_code = 'sdk_workbook_range_overlap'
                message = (
                    'The new range overlaps an existing range. Read the workbook, '
                    'update the existing range using its exact range_id and start_cell '
                    'with a replacement composition, or choose unused cells.'
                )
            if isinstance(exc, ValidationError):
                # Locations and rule types only: str(ValidationError) echoes the
                # offending input values (`input_value=...`), and this outcome
                # travels beyond the developer's process. The dedicated code marks
                # the message value-free so downstream projections may carry it.
                error_code = 'sdk_tool_validation_error'
                message = _validation_summary(exc)
                # PARALLEL-BENCHMARK-NOTE(2026-08-16): keep invalid structured-tool
                # calls diagnosable without logging arguments or Pydantic's input values.
                # The v2 Studio benchmark exposed a repeat loop that otherwise retained
                # only `sdk_tool_error`, making every retry indistinguishable.
                logger.warning(
                    'SDK tool validation failed tool=%s errors=%s',
                    tool.name,
                    [
                        {'location': tuple(item['loc']), 'type': item['type']}
                        for item in exc.errors(include_input=False, include_url=False)
                    ],
                )
            if target_execution_started:
                _fire_execution_hook(
                    tool.after_execute,
                    tool=tool,
                    tool_id=tool_call.spec_ref.tool_id,
                    stage='after',
                    arguments=tool_call.arguments,
                    error=message,
                    on_hook=on_hook,
                )
            if not revision_requested:
                for key in result_keys:
                    self._failures[key] = message
            # Release what this call reserved: it produced nothing, and a retry has to be
            # able to fill the key. Holding it would leave every consumer of this
            # producer permanently unfeedable.
            self._reserved_keys.difference_update(owned_keys)
            outcome = _error_outcome(
                tool_call.call_id,
                started=started if started is not None else time.perf_counter(),
                code=error_code,
                message=message,
                retryable=getattr(type(exc), 'sdk_retryable', False) is True,
            )
            if not revision_requested or tool_call.call_id not in self._calls:
                self._record_call(tool_call, tool, arguments, error_code=error_code)
            if on_completed is not None:
                on_completed(tool_call, outcome)
            return outcome
        finally:
            if revision is not None:
                self._revision_claims.discard(
                    (revision.invocation_id, revision.root_call_id, tool.name)
                )
            for completion_event in completion_events:
                completion_event.set()

    def _begin_input_round(self, call: ToolCall, tool: ToolMetadata, keys: Sequence[str]) -> None:
        """Replace an answer for a later model decision, never for a sibling call.

        Arrival still chooses one producer per assistant batch. Calls from a
        previously seen batch cannot rewind the selected answer, and an in-flight
        round keeps its reservation until it finishes.
        """
        message_id = call.lineage.message_id
        if message_id is None or not _metadata_list(tool.target, INTERRUPT_DEPENDENCIES_ATTR):
            return
        batch = (call.lineage.invocation_id, message_id)
        for key in keys:
            seen = self._input_result_batches.setdefault(key, set())
            if batch in seen:
                continue
            seen.add(batch)
            completion = self._completion_events.get(key)
            if completion is None or not completion.is_set():
                continue
            self._reserved_keys.discard(key)
            self._results.pop(key, None)
            self._failures.pop(key, None)
            self._completion_events[key] = asyncio.Event()

    async def _call_target_in_order(
        self,
        tool: ToolMetadata,
        arguments: Mapping[str, object],
        assess_workspace: Callable[[], Awaitable[None]],
    ) -> object:
        """Keep opted-in stateful toolsets ordered before entering worker threads."""

        async def execute() -> object:
            await assess_workspace()
            try:
                return await _call_target(tool, arguments, private_data=self._private_data)
            finally:
                # A call can promote custody itself, including before it fails.
                await assess_workspace()

        if tool.metadata.get('serialize_calls') is not True:
            return await execute()
        owner = getattr(tool.target, '__self__', tool.target)
        lock = self._serialized_locks.setdefault(id(owner), asyncio.Lock())
        async with lock:
            running = asyncio.create_task(execute())
            try:
                return await asyncio.shield(running)
            except asyncio.CancelledError:
                # A sync worker cannot be cancelled once started. Keep its lane
                # until it exits so a subsequent edit cannot race a hidden writer.
                while not running.done():
                    try:
                        await asyncio.shield(running)
                    except asyncio.CancelledError:  # noqa: PERF203 - repeated cancellation must not unlock a running writer.
                        continue
                    except Exception:  # noqa: BLE001 - original cancellation owns the result.
                        break
                if not running.cancelled():
                    running.exception()
                raise

    def _source_values(self, tool: ToolMetadata, payload: object) -> PrivateWorkspaceValues:
        owner = getattr(tool.target, '__self__', tool.target)
        key = (id(owner), workspace_scope(payload))
        return self._workspace_values.setdefault(key, PrivateWorkspaceValues())

    async def _resolved_arguments(
        self,
        tool: ToolMetadata,
        supplied: Mapping[str, object],
        *,
        revision: _RevisionContext | None,
        retained: Mapping[str, RetainedToolResult],
    ) -> dict[str, object]:
        arguments = cast(
            'dict[str, object]',
            resolve_private_placeholders(
                self._source_values(tool, supplied).resolve(dict(supplied)), self._private_data
            ),
        )
        for dependency in _metadata_list(tool.target, PRIVATE_DATA_DEPENDENCIES_ATTR):
            if not isinstance(dependency, PrivateDataDependency):
                continue
            if dependency.data_key not in self._private_data:
                message = f'private data is missing key {dependency.data_key!r}'
                raise KeyError(message)
            arguments[dependency.arg_name] = self._private_data[dependency.data_key]
        for dependency in _metadata_list(tool.target, INTERRUPT_DEPENDENCIES_ATTR):
            if not isinstance(dependency, InterruptDependency):
                continue
            if dependency.arg_name in arguments:
                continue
            arguments[dependency.arg_name] = await collect_interrupt_value(
                dependency,
                tool_name=tool.name,
            )
        # Agent dependencies are absent here on purpose: the SERVER delegates, runs the
        # sub-agent as a nested session, and binds its result into the call before the
        # tool_call event is published - so the value is already in `supplied`.
        for dependency in _metadata_list(tool.target, TOOL_DEPENDENCIES_ATTR):
            if isinstance(dependency, ToolDependency):
                arguments[dependency.arg_name] = await self._tool_dependency_result(
                    dependency,
                    invocation_id=revision.invocation_id if revision is not None else None,
                    root_call_id=revision.root_call_id if revision is not None else None,
                    retained=retained.get(dependency.arg_name),
                )
        return arguments

    async def _tool_dependency_result(  # noqa: C901 - base and branch lifecycle resolution.
        self,
        dependency: ToolDependency,
        *,
        invocation_id: str | None,
        root_call_id: str | None,
        retained: RetainedToolResult | None = None,
    ) -> object:
        if invocation_id is not None and root_call_id is not None:
            for key in (dependency.tool_id, dependency.tool_name):
                binding = self._revision_bindings.get((invocation_id, root_call_id, key))
                if binding is not None:
                    return binding.result
            for key in (dependency.tool_id, dependency.tool_name):
                binding = self._base_bindings.get((invocation_id, key))
                if binding is not None:
                    return binding.result
            message = f'tool dependency has not completed: {dependency.tool_name}'
            raise RuntimeError(message)
        dependency_key = _dependency_result_key(
            dependency,
            results=self._results,
            failures=self._failures,
            completion_events=self._completion_events,
        )
        if dependency_key is None:
            # Start events in one graph phase are emitted in topological order,
            # but their execution tasks begin on the next event-loop tick.
            await asyncio.sleep(0)
            dependency_key = _dependency_result_key(
                dependency,
                results=self._results,
                failures=self._failures,
                completion_events=self._completion_events,
            )
        if dependency_key is None:
            if retained is not None:
                return retained.result
            message = f'tool dependency has not completed: {dependency.tool_name}'
            raise RuntimeError(message)
        completion = self._completion_events.get(dependency_key)
        if completion is not None:
            await completion.wait()
        failure = self._failures.get(dependency_key)
        if failure is not None:
            message = f'tool dependency failed: {dependency.tool_name}: {failure}'
            raise RuntimeError(message)
        if dependency_key not in self._results:
            message = f'tool dependency has not completed: {dependency.tool_name}'
            raise RuntimeError(message)
        return self._results[dependency_key]

    def _revision_context(  # noqa: C901, PLR0912 - explicit lineage admission states.
        self,
        tool_call: ToolCall,
        tool: ToolMetadata,
    ) -> _RevisionContext:
        """Validate an explicitly opted-in reconstruction before it can execute."""
        lineage = tool_call.lineage
        root_call_id = lineage.root_call_id
        parent_call_id = lineage.parent_call_id
        if tool_call.call_id in self._calls:
            raise DependencyRevisionError(
                _REVISION_STALE,
                'dependency revision call id has already been accepted',
            )
        if root_call_id is None or parent_call_id is None:
            raise DependencyRevisionError(
                _REVISION_INVALID_LINEAGE,
                'dependency revision requires root and parent call ids',
            )
        root = self._calls.get(root_call_id)
        parent = self._calls.get(parent_call_id)
        if root is None or parent is None or root.invocation_id != lineage.invocation_id:
            raise DependencyRevisionError(
                _REVISION_INVALID_LINEAGE,
                'dependency revision lineage does not match this invocation',
            )
        if root.error_code != 'sdk_tool_validation_error':
            raise DependencyRevisionError(
                _REVISION_ROOT_NOT_FAILED,
                'dependency revision root was not a validation failure',
            )
        if parent.invocation_id != lineage.invocation_id or parent.tool_name != tool.name:
            raise DependencyRevisionError(
                _REVISION_PARENT_MISMATCH,
                'dependency revision parent does not identify this tool generation',
            )
        root_tool = self._tools.get(root.tool_name)
        if root_tool is None:
            raise DependencyRevisionError(
                _REVISION_UNSUPPORTED,
                'dependency revision root is not registered',
            )
        paths = self._revision_paths(tool, root_tool)
        edges: list[tuple[ToolMetadata, ToolDependency]] = []
        for path in paths:
            for edge in path:
                if edge not in edges:
                    edges.append(edge)
        participants = [tool, *(consumer for consumer, _dependency in edges)]
        if not all(_is_model_constructor(item) for item in participants):
            raise DependencyRevisionError(
                _REVISION_UNSUPPORTED,
                'dependency revision requires SDK Pydantic constructors',
            )
        if any(dependency.revision_policy != 'on_validation_error' for _, dependency in edges):
            raise DependencyRevisionError(
                _REVISION_NOT_OPTED_IN,
                'dependency revision is not explicitly authorized for this edge',
            )
        visible_call_id = self._visible_call_id(
            lineage.invocation_id,
            root_call_id,
            tool.name,
            initial_root_call_id=root_call_id if tool.name == root.tool_name else None,
        )
        if visible_call_id != parent_call_id:
            raise DependencyRevisionError(
                _REVISION_STALE,
                'dependency revision parent is not the current tool generation',
            )
        claim = (lineage.invocation_id, root_call_id, tool.name)
        if claim in self._revision_claims:
            raise DependencyRevisionError(
                _REVISION_CONFLICT,
                'dependency revision generation is already executing',
            )
        self._revision_claims.add(claim)
        return _RevisionContext(invocation_id=lineage.invocation_id, root_call_id=root_call_id)

    def _revision_paths(
        self,
        producer: ToolMetadata,
        root: ToolMetadata,
    ) -> list[list[tuple[ToolMetadata, ToolDependency]]]:
        """Find every declared producer-to-root path for a revision frontier."""
        if producer.name == root.name:
            return [[]]

        def search(
            current: ToolMetadata,
            visited: frozenset[str],
        ) -> list[list[tuple[ToolMetadata, ToolDependency]]]:
            paths: list[list[tuple[ToolMetadata, ToolDependency]]] = []
            for consumer in self._tools.values():
                if consumer.name in visited:
                    continue
                for dependency in _tool_dependencies(consumer):
                    if not _dependency_matches_tool(dependency, current):
                        continue
                    edge = (consumer, dependency)
                    if consumer.name == root.name:
                        paths.append([edge])
                    else:
                        paths.extend(
                            [[edge, *tail] for tail in search(consumer, visited | {consumer.name})]
                        )
            return paths

        paths = search(producer, frozenset({producer.name}))
        if not paths:
            raise DependencyRevisionError(
                _REVISION_UNSUPPORTED,
                'dependency revision requires a path to the failed root',
            )
        return paths

    def _visible_call_id(
        self,
        invocation_id: str,
        root_call_id: str,
        tool_name: str,
        *,
        initial_root_call_id: str | None = None,
    ) -> str | None:
        revision = self._revision_bindings.get((invocation_id, root_call_id, tool_name))
        if revision is not None:
            return revision.call_id
        binding = self._base_bindings.get((invocation_id, tool_name))
        if binding is not None:
            return binding.call_id
        return initial_root_call_id

    def _require_revised_root_dependency(
        self,
        tool_call: ToolCall,
        tool: ToolMetadata,
        arguments: Mapping[str, object],
        revision: _RevisionContext,
    ) -> None:
        """Refuse a marked root retry unless a reconstructed dependency changed it."""
        if tool_call.call_id == revision.root_call_id:
            raise DependencyRevisionError(
                _REVISION_PARENT_MISMATCH,
                'dependency revision must use a new call id',
            )
        root = self._calls[revision.root_call_id]
        if tool.name != root.tool_name:
            return
        dependency_names = {dependency.arg_name for dependency in _tool_dependencies(tool)}
        if not dependency_names or all(
            arguments.get(name) == root.accepted_arguments.get(name) for name in dependency_names
        ):
            raise DependencyRevisionError(
                _REVISION_NO_DEPENDENCY_CHANGE,
                'dependency revision root has no reconstructed dependency change',
            )

    def _record_call(
        self,
        tool_call: ToolCall,
        tool: ToolMetadata,
        arguments: Mapping[str, object] | None,
        *,
        error_code: str | None,
    ) -> None:
        """Keep only local provenance needed to validate later revision calls."""
        invocation_id = tool_call.lineage.invocation_id
        self._calls[tool_call.call_id] = _CallRecord(
            call_id=tool_call.call_id,
            invocation_id=invocation_id,
            tool_name=tool.name,
            accepted_arguments=dict(arguments or {}),
            error_code=error_code,
        )


def _dependency_result_key(
    dependency: ToolDependency,
    *,
    results: Mapping[str, object],
    failures: Mapping[str, str],
    completion_events: Mapping[str, asyncio.Event],
) -> str | None:
    for key in (dependency.tool_id, dependency.tool_name):
        if key in results or key in failures or key in completion_events:
            return key
    return None


def _tool_dependencies(tool: ToolMetadata) -> tuple[ToolDependency, ...]:
    """Return declared SDK tool dependencies without accepting foreign metadata."""
    return tuple(
        dependency
        for dependency in _metadata_list(tool.target, TOOL_DEPENDENCIES_ATTR)
        if isinstance(dependency, ToolDependency)
    )


def _dependency_matches_tool(dependency: ToolDependency, tool: ToolMetadata) -> bool:
    """Match compatibility metadata by either of its registered identifiers."""
    return tool.name in {dependency.tool_id, dependency.tool_name}


def _is_model_constructor(tool: ToolMetadata) -> bool:
    """Revision is safe only for SDK-registered Pydantic constructors."""
    return isinstance(tool.target, type) and issubclass(tool.target, BaseModel)


def _has_missing_interrupt_argument(
    tool: ToolMetadata,
    supplied: Mapping[str, object],
) -> bool:
    return any(
        isinstance(dependency, InterruptDependency) and dependency.arg_name not in supplied
        for dependency in _metadata_list(tool.target, INTERRUPT_DEPENDENCIES_ATTR)
    )


async def collect_interrupt_value(
    dependency: InterruptDependency,
    *,
    tool_name: str,
    prompt_override: str | None = None,
) -> object:
    """Ask the human for this argument, on the machine the human is sitting at.

    An interrupt argument is the one kind of dependency the server structurally cannot
    resolve: its input_handler is the developer's own callable, running in the developer's
    process. The server can announce that it needs the value; only the client can go and get
    it.

    The decorator has always recorded this dependency and nothing ever read it. The tool
    therefore declared an argument that only a human could supply, nobody asked the human,
    and - because interrupt args were also the one dependency kind still left in the
    model-facing schema - the model kept trying to invent one until the loop ran out of
    iterations.
    """
    prompt = prompt_override if prompt_override is not None else dependency.prompt
    prompt = prompt or f'{dependency.arg_name}: '
    if dependency.choices:
        prompt = f'{prompt}{list(dependency.choices)} '
    reporter = get_current_reporter()
    get_input = getattr(reporter, 'get_input', None)
    if callable(get_input):
        value = await asyncio.to_thread(
            get_input,
            prompt,
            input_type=dependency.input_type,
            choices=list(dependency.choices) or None,
            data_key=dependency.arg_name,
            arg_name=dependency.arg_name,
            tool_name=tool_name,
        )
    else:
        handler = dependency.input_handler
        value = await asyncio.to_thread(handler, prompt)
    if inspect.isawaitable(value):
        value = await cast('Awaitable[object]', value)
    return typed_interrupt_answer(value, dependency.input_type)


def _validation_summary(exc: ValidationError) -> str:
    """Summarize a validation failure from locations and rule types alone."""
    failures = '; '.join(
        '{location}: {rule}'.format(
            location='.'.join(str(part) for part in item['loc']) or '<root>',
            rule=item['type'],
        )
        for item in exc.errors(include_input=False, include_url=False)
    )
    return f'invalid arguments: {failures or "validation failed"}'


async def _call_target(
    tool: ToolMetadata,
    arguments: Mapping[str, object],
    *,
    private_data: Mapping[str, object],
) -> object:
    target = tool.target
    if inspect.isclass(target) and issubclass(target, BaseModel):
        return validate_private_models(target, arguments, private_data)
    try:
        hints = get_type_hints(target, include_extras=True)
    except (TypeError, NameError):
        hints = {}
    arguments = {
        name: validate_private_models(hints[name], value, private_data) if name in hints else value
        for name, value in arguments.items()
    }
    if inspect.iscoroutinefunction(target):
        return await target(**arguments)
    with owning_tool_loop():
        result = await asyncio.to_thread(target, **arguments)
    if inspect.isawaitable(result):
        return await cast('Awaitable[object]', result)
    return result


def _fire_execution_hook(  # noqa: PLR0913 - one keyword per payload field.
    hook: Callable[..., object] | None,
    *,
    tool: ToolMetadata,
    tool_id: str,
    stage: str,
    arguments: Mapping[str, object],
    result: object | None = None,
    error: str | None = None,
    on_hook: Callable[[HookFiring], None] | None = None,
) -> None:
    """Fire a developer execution hook without letting it break the tool call.

    Hooks observe; they do not gate. A hook that raises is a developer bug in
    observability code, and failing the underlying tool for it would turn a
    logging mistake into a run failure, so the exception is logged and dropped.
    A raising hook still ends its own chain - the callbacks composed inside it
    were registered to run around a hook that completed, and that is the
    behaviour this chain has always had.

    Each callback in the chain reports its own firing through ``on_hook`` so a
    trace can name it. Without that, a run that fired six callbacks looks
    identical to a run that registered none.
    """
    if hook is None:
        return
    payload: dict[str, object | None] = {
        'stage': stage,
        'tool': tool,
        'tool_id': tool_id,
        'tool_name': tool.name,
        'arguments': dict(arguments),
        'result': result,
        'error': error,
    }
    for binding in hook_bindings(hook, target_name=tool.name):
        started = time.perf_counter()
        hook_error: str | None = None
        try:
            binding.hook(payload)
        except Exception as exc:
            hook_error = safe_hook_error(exc)
            logger.exception('tool execution hook failed (stage=%s tool=%s)', stage, tool.name)
        if on_hook is not None:
            on_hook(
                HookFiring(
                    name=binding.name,
                    stage=stage,
                    status='failed' if hook_error is not None else 'completed',
                    source=binding.source,
                    target_type=binding.target_type,
                    target_name=binding.target_name or tool.name,
                    error=hook_error,
                    elapsed_ms=max(0, round((time.perf_counter() - started) * 1000)),
                )
            )
        if hook_error is not None:
            return


def _metadata_list(target: object, attr_name: str) -> list[object]:
    raw = getattr(target, attr_name, ())
    if isinstance(raw, list):
        return cast('list[object]', raw)
    return []


def _duration_ms(started: float) -> int:
    # A completed tool always consumed positive time. Preserve that invariant at
    # millisecond resolution so fast local callables do not become misleading
    # zero-duration spans in the canonical trace.
    return max(1, round((time.perf_counter() - started) * 1000))


def _error_outcome(
    call_id: str,
    *,
    started: float,
    code: str,
    message: str,
    retryable: bool = False,
) -> ErrorToolOutcome:
    return ErrorToolOutcome(
        call_id=call_id,
        duration_ms=_duration_ms(started),
        status='error',
        error=ToolOutcomeError(
            code=code,
            message=message[:2048],
            # Removing or resizing a conflicting range can make the same
            # placement valid. The loop still bounds failed decision rounds.
            retryable=retryable or code == 'sdk_workbook_range_overlap',
        ),
    )


__all__ = ['LocalToolRuntime', 'collect_interrupt_value']
