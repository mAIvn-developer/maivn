"""Invocation-boundary execution hooks for Agent and Swarm scopes."""

from __future__ import annotations

import inspect
import logging
import time
from asyncio import CancelledError
from dataclasses import dataclass
from typing import TYPE_CHECKING
from uuid import uuid4

from maivn._internal.hooks import HookBinding, safe_hook_error
from maivn._internal.models import StreamEvent
from maivn._internal.tool_runtime import HookFiring

if TYPE_CHECKING:
    from collections.abc import (
        AsyncGenerator,
        AsyncIterator,
        Awaitable,
        Callable,
        Generator,
        Iterator,
        Sequence,
    )

    from maivn._internal.models import InvokeResponse

logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class ScopeExecutionHooks:
    """The ordered callbacks that surround one public scope invocation."""

    before: tuple[HookBinding, ...]
    after: tuple[HookBinding, ...]


def scope_execution_hooks(scope: object) -> ScopeExecutionHooks:
    """Collect ``scope``-mode callbacks outside-in for one invocation.

    Tool-mode callbacks remain attached to compiled tool metadata. A swarm's
    agent mode is intentionally handled at member-assignment boundaries, not
    by this top-level scope wrapper.
    """
    chain: list[object] = []
    current: object | None = scope
    while current is not None:
        chain.append(current)
        current = getattr(current, '_parent_hook_scope', None)
    chain.reverse()
    before = _scope_bindings(chain, 'before_execute')
    after = _scope_bindings(reversed(chain), 'after_execute')
    return ScopeExecutionHooks(before=before, after=after)


def _scope_bindings(
    scopes: Sequence[object] | Iterator[object],
    attribute: str,
) -> tuple[HookBinding, ...]:
    bindings: list[HookBinding] = []
    for scope in scopes:
        if getattr(scope, 'hook_execution_mode', 'tool') != 'scope':
            continue
        binding = _scope_binding(scope, attribute)
        if binding is not None:
            bindings.append(binding)
    return tuple(bindings)


def invoke_with_scope_hooks(
    invoke: Callable[[], InvokeResponse],
    hooks: ScopeExecutionHooks,
    *,
    scope: object,
    messages: object,
) -> InvokeResponse:
    """Run a non-streaming invocation while preserving scope hook cleanup."""
    payload = _payload(scope=scope, messages=messages)
    _fire_scope_hooks(hooks.before, payload, stage='before')
    try:
        result = invoke()
    except (CancelledError, GeneratorExit) as exc:
        payload.update(stage='after', result=None, error=exc)
        _fire_scope_hooks(hooks.after, payload, stage='after')
        raise
    except BaseException as exc:
        payload.update(stage='after', result=None, error=exc)
        _fire_scope_hooks(hooks.after, payload, stage='after')
        raise
    payload.update(stage='after', result=result, error=None)
    _fire_scope_hooks(hooks.after, payload, stage='after')
    return result


async def ainvoke_with_scope_hooks(  # noqa: C901, PLR0912 - preserves async generator cleanup.
    invoke: Callable[[], AsyncIterator[StreamEvent]],
    hooks: ScopeExecutionHooks,
    *,
    scope: object,
    messages: object,
) -> AsyncGenerator[StreamEvent, None]:
    """Wrap one async event stream and expose safe local hook firings."""
    payload = _payload(scope=scope, messages=messages)
    identity = f'local-hook-{uuid4().hex}'
    before = _fire_scope_hooks(hooks.before, payload, stage='before')
    ordinal = 0
    last_position = 0
    before_emitted = False
    after_fired = False
    source = invoke()
    try:
        async for event in source:
            last_position = event.position
            if not before_emitted:
                before_emitted = True
                if event.event_type != 'final':
                    yield event
                for firing in before:
                    yield _hook_event(firing, event=event, identity=identity, ordinal=ordinal)
                    ordinal += 1
                if event.event_type != 'final':
                    continue
            if event.event_type == 'final':
                payload.update(stage='after', result=event.payload, error=None)
                for firing in _fire_scope_hooks(hooks.after, payload, stage='after'):
                    yield _hook_event(firing, event=event, identity=identity, ordinal=ordinal)
                    ordinal += 1
                after_fired = True
            yield event
    except (CancelledError, GeneratorExit) as exc:
        payload.update(stage='after', result=None, error=exc)
        if not after_fired:
            _fire_scope_hooks(hooks.after, payload, stage='after')
        raise
    except BaseException as exc:
        payload.update(stage='after', result=None, error=exc)
        if not after_fired:
            for firing in _fire_scope_hooks(hooks.after, payload, stage='after'):
                yield _hook_event(
                    firing,
                    event=None,
                    identity=identity,
                    ordinal=ordinal,
                    position=last_position,
                )
                ordinal += 1
        raise
    else:
        if not after_fired:
            payload.update(stage='after', result=None, error=None)
            _fire_scope_hooks(hooks.after, payload, stage='after')
    finally:
        close = getattr(source, 'aclose', None)
        if callable(close):
            closing = close()
            if inspect.isawaitable(closing):
                await closing


async def async_invoke_with_scope_hooks(
    invoke: Callable[[], Awaitable[InvokeResponse]],
    hooks: ScopeExecutionHooks,
    *,
    scope: object,
    messages: object,
) -> InvokeResponse:
    """Run an awaitable result invocation while preserving scope cleanup."""
    payload = _payload(scope=scope, messages=messages)
    _fire_scope_hooks(hooks.before, payload, stage='before')
    try:
        result = await invoke()
    except BaseException as exc:
        payload.update(stage='after', result=None, error=exc)
        _fire_scope_hooks(hooks.after, payload, stage='after')
        raise
    payload.update(stage='after', result=result, error=None)
    _fire_scope_hooks(hooks.after, payload, stage='after')
    return result


def stream_with_scope_hooks(  # noqa: C901, PLR0912 - preserves generator cleanup.
    events: Iterator[StreamEvent],
    hooks: ScopeExecutionHooks,
    *,
    scope: object,
    messages: object,
) -> Generator[StreamEvent, None, None]:
    """Wrap one synchronous event stream with the same lifecycle semantics."""
    payload = _payload(scope=scope, messages=messages)
    identity = f'local-hook-{uuid4().hex}'
    before = _fire_scope_hooks(hooks.before, payload, stage='before')
    ordinal = 0
    last_position = 0
    before_emitted = False
    after_fired = False
    try:
        for event in events:
            last_position = event.position
            if not before_emitted:
                before_emitted = True
                if event.event_type != 'final':
                    yield event
                for firing in before:
                    yield _hook_event(firing, event=event, identity=identity, ordinal=ordinal)
                    ordinal += 1
                if event.event_type != 'final':
                    continue
            if event.event_type == 'final':
                payload.update(stage='after', result=event.payload, error=None)
                for firing in _fire_scope_hooks(hooks.after, payload, stage='after'):
                    yield _hook_event(firing, event=event, identity=identity, ordinal=ordinal)
                    ordinal += 1
                after_fired = True
            yield event
    except (CancelledError, GeneratorExit) as exc:
        payload.update(stage='after', result=None, error=exc)
        if not after_fired:
            _fire_scope_hooks(hooks.after, payload, stage='after')
        raise
    except BaseException as exc:
        payload.update(stage='after', result=None, error=exc)
        if not after_fired:
            for firing in _fire_scope_hooks(hooks.after, payload, stage='after'):
                yield _hook_event(
                    firing,
                    event=None,
                    identity=identity,
                    ordinal=ordinal,
                    position=last_position,
                )
                ordinal += 1
        raise
    else:
        if not after_fired:
            payload.update(stage='after', result=None, error=None)
            _fire_scope_hooks(hooks.after, payload, stage='after')
    finally:
        close = getattr(events, 'close', None)
        if callable(close):
            close()


def stream_with_member_hooks(
    events: Iterator[StreamEvent], swarm: object
) -> Generator[StreamEvent, None, None]:
    """Observe swarm ``agent`` and member ``scope`` hooks at assignments.

    Assignment ids are the runtime's invocation identity. Names select a
    developer descriptor only at the start boundary and are rejected when the
    authored roster is ambiguous; every later completion is keyed by that
    assignment id. The server has already admitted a remote member before its
    progress event reaches this SDK, so these member hooks are lifecycle
    observers and cannot gate that remote execution.
    """
    active: dict[str, tuple[object, ScopeExecutionHooks]] = {}
    identity = f'local-hook-{uuid4().hex}'
    ordinal = 0
    try:
        for event in events:
            assignment = _assignment(event)
            if assignment is not None:
                assignment_id, status, agent_name, result, error = assignment
                member = _member_for_assignment(swarm, agent_name)
                if status == 'executing' and assignment_id not in active and member is not None:
                    agent = member
                    hooks = _member_execution_hooks(swarm, agent)
                    active[assignment_id] = (agent, hooks)
                    payload = _payload(scope=agent, messages=None)
                    payload['tool'] = agent
                    for firing in _fire_scope_hooks(hooks.before, payload, stage='before'):
                        yield _hook_event(
                            firing,
                            event=event,
                            identity=identity,
                            ordinal=ordinal,
                            target_id=assignment_id,
                        )
                        ordinal += 1
                if status in {'completed', 'failed', 'cancelled'}:
                    active_entry = active.pop(assignment_id, None)
                    if active_entry is not None:
                        agent, hooks = active_entry
                        payload = _payload(scope=agent, messages=None)
                        payload.update(
                            stage='after',
                            tool=agent,
                            result=result if status == 'completed' else None,
                            error=error if status != 'completed' else None,
                        )
                        for firing in _fire_scope_hooks(hooks.after, payload, stage='after'):
                            yield _hook_event(
                                firing,
                                event=event,
                                identity=identity,
                                ordinal=ordinal,
                                target_id=assignment_id,
                            )
                            ordinal += 1
                yield event
                continue
            yield event
    finally:
        try:
            _close_member_hooks(active)
        finally:
            close = getattr(events, 'close', None)
            if callable(close):
                close()


async def astream_with_member_hooks(
    events: AsyncIterator[StreamEvent],
    swarm: object,
) -> AsyncGenerator[StreamEvent, None]:
    """Async counterpart of :func:`stream_with_member_hooks`."""
    active: dict[str, tuple[object, ScopeExecutionHooks]] = {}
    identity = f'local-hook-{uuid4().hex}'
    ordinal = 0
    try:
        async for event in events:
            assignment = _assignment(event)
            if assignment is not None:
                assignment_id, status, agent_name, result, error = assignment
                member = _member_for_assignment(swarm, agent_name)
                if status == 'executing' and assignment_id not in active and member is not None:
                    agent = member
                    hooks = _member_execution_hooks(swarm, agent)
                    active[assignment_id] = (agent, hooks)
                    payload = _payload(scope=agent, messages=None)
                    payload['tool'] = agent
                    for firing in _fire_scope_hooks(hooks.before, payload, stage='before'):
                        yield _hook_event(
                            firing,
                            event=event,
                            identity=identity,
                            ordinal=ordinal,
                            target_id=assignment_id,
                        )
                        ordinal += 1
                if status in {'completed', 'failed', 'cancelled'}:
                    active_entry = active.pop(assignment_id, None)
                    if active_entry is not None:
                        agent, hooks = active_entry
                        payload = _payload(scope=agent, messages=None)
                        payload.update(
                            stage='after',
                            tool=agent,
                            result=result if status == 'completed' else None,
                            error=error if status != 'completed' else None,
                        )
                        for firing in _fire_scope_hooks(hooks.after, payload, stage='after'):
                            yield _hook_event(
                                firing,
                                event=event,
                                identity=identity,
                                ordinal=ordinal,
                                target_id=assignment_id,
                            )
                            ordinal += 1
                yield event
                continue
            yield event
    finally:
        try:
            _close_member_hooks(active)
        finally:
            close = getattr(events, 'aclose', None)
            if callable(close):
                closing = close()
                if inspect.isawaitable(closing):
                    await closing


def _close_member_hooks(active: dict[str, tuple[object, ScopeExecutionHooks]]) -> None:
    for agent, hooks in active.values():
        payload = _payload(scope=agent, messages=None)
        error = RuntimeError('member invocation cancelled')
        payload.update(stage='after', tool=agent, error=error)
        _fire_scope_hooks(hooks.after, payload, stage='after')
    active.clear()


def _assignment(event: StreamEvent) -> tuple[str, str, str, object | None, object | None] | None:
    payload = event.payload
    if event.event_type == 'progress_update':
        if payload.get('action_type') != 'swarm_agent':
            return None
        assignment_id = payload.get('action_id')
        agent_name = payload.get('action_name')
    elif event.event_type == 'agent_assignment':
        # Already-normalized events remain accepted for compatibility with
        # user-provided stream adapters, but Client yields the raw branch above.
        assignment_id = payload.get('assignment_id')
        agent_name = payload.get('agent_name')
    else:
        return None
    status = _assignment_status(payload.get('status'))
    if (
        not isinstance(assignment_id, str)
        or not assignment_id
        or status is None
        or not isinstance(agent_name, str)
        or not agent_name
    ):
        return None
    error = payload.get('error')
    if status == 'failed' and not isinstance(error, str):
        # The API intentionally withholds remote exception text from
        # parent traces. Keep a stable failure marker for the local callback.
        error = 'swarm_member_failed'
    return assignment_id, status, agent_name, payload.get('result'), error


def _assignment_status(value: object) -> str | None:
    """Map raw and normalized assignment statuses to the lifecycle states used here."""
    if not isinstance(value, str):
        return None
    normalized = value.strip().lower()
    if normalized in {'executing', 'in_progress', 'running'}:
        return 'executing'
    if normalized in {'completed', 'done', 'finished', 'success'}:
        return 'completed'
    if normalized in {'failed', 'error'}:
        return 'failed'
    if normalized == 'cancelled':
        return 'cancelled'
    return None


def _member_for_assignment(swarm: object, agent_name: str) -> object | None:
    matches = [
        member
        for member in getattr(swarm, 'agents', ())
        if getattr(member, 'name', None) == agent_name
    ]
    return matches[0] if len(matches) == 1 else None


def _member_execution_hooks(swarm: object, member: object) -> ScopeExecutionHooks:
    before: list[HookBinding] = []
    after: list[HookBinding] = []
    if getattr(swarm, 'hook_execution_mode', 'tool') == 'agent':
        if binding := _scope_binding(swarm, 'before_execute'):
            before.append(binding)
        if binding := _scope_binding(swarm, 'after_execute'):
            after.append(binding)
    if getattr(member, 'hook_execution_mode', 'tool') == 'scope':
        if binding := _scope_binding(member, 'before_execute'):
            before.append(binding)
        if binding := _scope_binding(member, 'after_execute'):
            after.insert(0, binding)
    return ScopeExecutionHooks(before=tuple(before), after=tuple(after))


def has_member_execution_hooks(swarm: object) -> bool:
    """Return whether a swarm needs assignment-stream lifecycle observation."""
    if getattr(swarm, 'hook_execution_mode', 'tool') == 'agent' and (
        callable(getattr(swarm, 'before_execute', None))
        or callable(getattr(swarm, 'after_execute', None))
    ):
        return True
    return any(
        getattr(member, 'hook_execution_mode', 'tool') == 'scope'
        and (
            callable(getattr(member, 'before_execute', None))
            or callable(getattr(member, 'after_execute', None))
        )
        for member in getattr(swarm, 'agents', ())
    )


def _scope_binding(scope: object, attribute: str) -> HookBinding | None:
    hook = getattr(scope, attribute, None)
    if not callable(hook):
        return None
    is_swarm = isinstance(getattr(scope, 'agents', None), list)
    name = getattr(scope, 'name', None)
    return HookBinding(
        hook=hook,
        source='swarm' if is_swarm else 'scope',
        target_type='swarm' if is_swarm else 'agent',
        target_name=name if isinstance(name, str) and name else None,
    )


def _payload(*, scope: object, messages: object) -> dict[str, object | None]:
    return {
        'stage': 'before',
        'tool': None,
        'tool_id': None,
        'arguments': None,
        'args': None,
        'result': None,
        'error': None,
        # These remain inside the developer callback; the event projection
        # below intentionally publishes only HookFiring's safe fields.
        'scope': scope,
        'messages': messages,
    }


def _fire_scope_hooks(
    bindings: Sequence[HookBinding],
    payload: dict[str, object | None],
    *,
    stage: str,
) -> list[HookFiring]:
    firings: list[HookFiring] = []
    for binding in bindings:
        started = time.perf_counter()
        hook_error: str | None = None
        try:
            binding.hook(payload)
        except Exception as exc:
            hook_error = safe_hook_error(exc)
            logger.exception('scope execution hook failed (stage=%s)', stage)
        firings.append(
            HookFiring(
                name=binding.name,
                stage=stage,
                status='failed' if hook_error is not None else 'completed',
                source=binding.source,
                target_type=binding.target_type,
                target_name=binding.target_name,
                error=hook_error,
                elapsed_ms=max(0, round((time.perf_counter() - started) * 1000)),
            )
        )
        if hook_error is not None:
            break
    return firings


def _hook_event(  # noqa: PLR0913 - canonical trace envelope fields are explicit.
    firing: HookFiring,
    *,
    event: StreamEvent | None,
    identity: str,
    ordinal: int,
    position: int | None = None,
    target_id: str | None = None,
) -> StreamEvent:
    """Create a local observed event using only identities supplied by the stream."""
    envelope: dict[str, object] = {}
    if event is not None:
        for key in ('session_id', 'parent_session_id', 'root_event_id', 'correlation_id'):
            value = event.data.get(key)
            if isinstance(value, str) and value:
                envelope[key] = value
    return StreamEvent(
        position=event.position if event is not None else (position or 0),
        event_type='hook_fired',
        data={
            **envelope,
            'event_id': f'{identity}:{ordinal}',
            'type': 'hook_fired',
            'payload': {
                'name': firing.name,
                'stage': firing.stage,
                'status': firing.status,
                'source': firing.source,
                'target_type': firing.target_type,
                'target_id': target_id or firing.target_name,
                'target_name': firing.target_name,
                'error': firing.error,
                'elapsed_ms': firing.elapsed_ms,
            },
        },
    )


__all__ = [
    'ScopeExecutionHooks',
    'ainvoke_with_scope_hooks',
    'astream_with_member_hooks',
    'async_invoke_with_scope_hooks',
    'has_member_execution_hooks',
    'invoke_with_scope_hooks',
    'scope_execution_hooks',
    'stream_with_member_hooks',
    'stream_with_scope_hooks',
]
