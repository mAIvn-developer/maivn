"""Characterize small runtime support modules: ids, hooks, errors, diagnostics and limits."""

from __future__ import annotations

import asyncio
import uuid
from typing import TYPE_CHECKING, Any

import pytest
from maivn_contracts.tools import ErrorToolOutcome, OkToolOutcome

from maivn import MaivnHTTPError, PlanLimitExceededError, depends_on_tool
from maivn._internal.error_diagnostics import (
    attach_error_diagnostics,
    diagnostic_facts,
    error_action_hint,
    safe_error_message,
)
from maivn._internal.hooks import ComposedHook, HookBinding, compose_hooks, hook_bindings
from maivn._internal.ids import create_uuid
from maivn._internal.models import StreamEvent, ToolMetadata
from maivn._internal.plan_limits import is_plan_limit_error
from maivn._internal.schema_references import prune_unused_definitions
from maivn._internal.tool_runtime import LocalToolRuntime

if TYPE_CHECKING:
    from collections.abc import Mapping

# --- ids ---------------------------------------------------------------------------


def _named_function() -> None:
    """Module-level function whose identity is module plus qualname."""


class _Repr:
    def __repr__(self) -> str:
        return 'fixed-repr'


class _BrokenRepr:
    def __repr__(self) -> str:
        message = 'repr refused'
        raise RuntimeError(message)


class _NoModule:
    """Callable instance whose class hides its module for identity purposes."""

    __module__ = ''

    def __call__(self) -> None:
        """Do nothing."""


def _uuid5(identifier: str) -> str:
    return str(uuid.uuid5(uuid.NAMESPACE_DNS, identifier))


def test_create_uuid_without_object_is_a_fresh_random_v4() -> None:
    """No object means a random version-4 UUID that differs per call."""
    first = create_uuid()
    second = create_uuid()
    assert uuid.UUID(first).version == 4  # noqa: PLR2004 - UUID version number.
    assert first != second


def test_create_uuid_for_callables_uses_module_and_qualname() -> None:
    """Functions and classes hash their module-qualified name with UUIDv5/DNS."""
    module = __name__
    assert create_uuid(_named_function) == _uuid5(f'{module}._named_function')
    assert create_uuid(_Repr) == _uuid5(f'{module}._Repr')
    assert create_uuid(_named_function) == create_uuid(_named_function)


def test_create_uuid_falls_back_to_name_then_type_and_repr() -> None:
    """Missing qualname uses __name__; a moduleless callable uses its type and repr."""

    class _NameOnly:
        __module__ = 'pkg.mod'
        __name__ = 'only_name'

        def __call__(self) -> None:
            """Do nothing."""

    named = _NameOnly()
    assert create_uuid(named) == _uuid5('pkg.mod.only_name')
    no_module = _NoModule()
    assert create_uuid(no_module) == _uuid5(f'_NoModule:{no_module!r}')


def test_create_uuid_for_a_moduleless_class_uses_its_metaclass_and_repr() -> None:
    """A class without a module name is identified by type plus repr, not its qualname."""

    class _Anonymous:
        __module__ = ''

    assert create_uuid(_Anonymous) == _uuid5(f'type:{_Anonymous!r}')


def test_create_uuid_for_values_uses_type_and_repr_or_identity() -> None:
    """Plain values hash type name plus repr; a failing repr falls back to id()."""
    assert create_uuid('abc') == _uuid5("str:'abc'")
    assert create_uuid(_Repr()) == _uuid5('_Repr:fixed-repr')
    broken = _BrokenRepr()
    assert create_uuid(broken) == _uuid5(f'_BrokenRepr:{id(broken)}')


def test_create_uuid_for_callable_without_name_uses_fallback() -> None:
    """A module-bearing callable without qualname or name falls back to repr."""

    class _Anonymous:
        __module__ = 'pkg.mod'

        def __call__(self) -> None:
            """Do nothing."""

        def __repr__(self) -> str:
            return 'anon'

    assert create_uuid(_Anonymous()) == _uuid5('_Anonymous:anon')


# --- hooks -------------------------------------------------------------------------


class _CallableHook:
    def __init__(self, sink: list[str], label: str) -> None:
        self.sink = sink
        self.label = label

    def __call__(self, payload: dict[str, object]) -> None:
        self.sink.append(f'{self.label}:{payload["stage"]}')


def test_hook_binding_name_prefers_function_name_then_type_name() -> None:
    """Named callbacks report their __name__; callable objects report their class."""

    def audit(_payload: dict[str, object]) -> None:
        return None

    assert HookBinding(hook=audit, source='tool', target_type='tool').name == 'audit'
    instance = _CallableHook([], 'x')
    assert HookBinding(hook=instance, source='scope', target_type='agent').name == '_CallableHook'


def test_hook_binding_name_ignores_blank_names() -> None:
    """A whitespace-only __name__ is replaced by the callable's type name."""

    class _BlankNamed(_CallableHook):
        __name__ = '  '

    hook = _BlankNamed([], 'x')
    assert HookBinding(hook=hook, source='tool', target_type='tool').name == '_BlankNamed'


def test_composed_hook_runs_bindings_in_order_and_exposes_them() -> None:
    """Calling a composed hook fires every callback in registration order."""
    sink: list[str] = []
    first = HookBinding(hook=_CallableHook(sink, 'swarm'), source='swarm', target_type='swarm')
    second = HookBinding(
        hook=_CallableHook(sink, 'tool'), source='tool', target_type='tool', target_name='t'
    )
    composed = compose_hooks([first, second])
    assert isinstance(composed, ComposedHook)
    composed({'stage': 'before'})
    assert sink == ['swarm:before', 'tool:before']
    assert hook_bindings(composed, target_name='ignored') == (first, second)
    assert compose_hooks([]) is None


def test_hook_bindings_wraps_bare_callables_and_ignores_none() -> None:
    """A bare tool hook reports as one tool-sourced binding; None reports nothing."""

    def audit(_payload: dict[str, object]) -> None:
        return None

    assert hook_bindings(None, target_name='t') == ()
    assert hook_bindings(audit, target_name='lookup') == (
        HookBinding(hook=audit, source='tool', target_type='tool', target_name='lookup'),
    )


# --- errors ------------------------------------------------------------------------


def test_http_error_message_lists_only_usable_field_diagnostics() -> None:
    """Field entries need a non-empty message; a missing field name keeps bare text."""
    error = MaivnHTTPError(
        status_code=422,
        reason='Invalid request',
        code='validation_failed',
        fields=[
            {'field': 'name', 'message': 'is required'},
            {'message': 'bare problem'},
            {'field': '', 'message': 'blank field'},
            {'field': 'x', 'message': ''},
            {'field': 'y'},
            'not-a-dict',  # pyright: ignore[reportArgumentType]
        ],
    )
    assert str(error) == (
        'mAIvn API returned 422: Invalid request - name: is required; bare problem; blank field'
    )
    assert error.error_code == 'validation_failed'
    assert len(error.fields) == 6  # noqa: PLR2004 - all entries are retained verbatim.


def test_http_error_without_diagnostics_has_plain_message() -> None:
    """With no usable fields the message is the status and reason only."""
    error = MaivnHTTPError(status_code=500, reason='Server error', fields=[{'field': 'x'}])
    assert str(error) == 'mAIvn API returned 500: Server error'
    assert error.retry_after is None


# --- error diagnostics -------------------------------------------------------------


@pytest.mark.parametrize(
    ('code', 'hint'),
    [
        (
            'durable_event_refused',
            'Check private-data declarations and the refused field before submitting more work.',
        ),
        (
            'private_placeholder_unresolvable',
            'Check private-data declarations and the refused field before submitting more work.',
        ),
        ('structured_output_invalid_json', 'Check arguments and the expected output schema.'),
        ('invalid_arguments', 'Check arguments and the expected output schema.'),
        ('forbidden', 'Check configuration and permissions.'),
        ('configuration_error', 'Check configuration and permissions.'),
        ('settlement_pending', 'Wait for settlement and query session usage again.'),
        ('usage_pending', 'Wait for settlement and query session usage again.'),
        ('cancelled', 'Inspect the session for work completed before cancellation.'),
        ('session_cancelled', 'Inspect the session for work completed before cancellation.'),
        ('anything_else', 'Inspect the failed session before submitting more work.'),
        (None, 'Inspect the failed session before submitting more work.'),
    ],
)
def test_error_action_hint_maps_codes_to_fixed_guidance(code: str | None, hint: str) -> None:
    """Each code family maps to one fixed, replay-free hint."""
    assert error_action_hint(code) == hint


def test_diagnostic_facts_keep_first_bounded_tokens_and_non_negative_indexes() -> None:
    """Only token-shaped identities and non-boolean non-negative indexes survive."""
    facts = diagnostic_facts(
        {
            'error_code': 'bad code with spaces',
            'session_id': 'ses-1',
            'attempt_index': True,
            'call_id': 'x' * 161,
        },
        {'error_code': 'tool_failed', 'session_id': 'ses-2', 'attempt_index': -1},
        {'attempt_index': 2, 'run_id': 'run:1.a'},
    )
    assert facts == {
        'session_id': 'ses-1',
        'error_code': 'tool_failed',
        'attempt_index': 2,
        'run_id': 'run:1.a',
    }


def test_attach_error_diagnostics_fills_missing_identity_and_correlation() -> None:
    """Missing identities are set; the correlation map excludes the error code."""
    error = RuntimeError('boom')
    attach_error_diagnostics(
        error, {'error_code': 'tool_failed', 'session_id': 'ses-1', 'call_id': 'call-1'}
    )
    assert getattr(error, 'error_code', None) == 'tool_failed'
    assert getattr(error, 'session_id', None) == 'ses-1'
    assert getattr(error, 'correlation', None) == {'session_id': 'ses-1', 'call_id': 'call-1'}


def test_attach_error_diagnostics_keeps_a_foreign_correlation_value() -> None:
    """A non-mapping correlation attribute is left untouched."""

    class _CorrelatedError(RuntimeError):
        correlation: object = 'opaque'

    error = _CorrelatedError('boom')
    attach_error_diagnostics(error, {'session_id': 'ses-1'})
    assert getattr(error, 'correlation', None) == 'opaque'
    assert getattr(error, 'session_id', None) == 'ses-1'


def test_safe_error_message_collapses_whitespace_and_bounds_length() -> None:
    """Messages collapse whitespace and truncate to 512 characters with an ellipsis."""
    assert safe_error_message('a \n  b\tc') == 'a b c'
    bounded = safe_error_message('x' * 600)
    assert bounded == 'x' * 509 + '...'
    assert safe_error_message('y' * 512) == 'y' * 512


# --- plan limits -------------------------------------------------------------------


def test_plan_limit_describe_without_reset_or_upgrade() -> None:
    """A configured limit without reset/upgrade info is one sentence."""
    error = PlanLimitExceededError(
        status_code=429,
        reason='Plan limit reached',
        code='plan_limit_exceeded',
        detail={'meter': 'fires', 'plan': 'starter', 'limit': 500},
    )
    assert error.describe() == "Your starter plan's 500 monthly runs for this period are used up."
    assert is_plan_limit_error(error)


@pytest.mark.parametrize(
    ('raw', 'expected'),
    [
        (12.5, '12.5'),
        (0, '0'),
        (True, None),
        ('', None),
        (['list'], None),
        (None, None),
    ],
)
def test_plan_limit_detail_values_are_text_or_absent(raw: object, expected: str | None) -> None:
    """Numbers become text; booleans, empty strings and containers are absent."""
    error = PlanLimitExceededError(
        status_code=429,
        reason='Plan limit reached',
        detail={'limit': raw, 'used': raw, 'meter': 'spend'},
    )
    assert error.limit == expected
    assert error.used == expected
    if expected is None:
        assert error.describe() == (
            'Your current plan has no configured spend guardrail, so runs are refused. '
            'Contact support to set one.'
        )


def test_plan_limit_with_non_mapping_detail_has_no_typed_fields() -> None:
    """A non-dict detail leaves every typed field empty and an unknown meter label."""
    error = PlanLimitExceededError(status_code=429, reason='r', detail='free text')
    assert (error.meter, error.plan, error.limit, error.upgrade) == (None, None, None, None)
    assert error.describe() == (
        'Your current plan has no configured plan limit, so runs are refused. '
        'Contact support to set one.'
    )


# --- schema references -------------------------------------------------------------


def test_prune_keeps_mutually_recursive_definitions_and_drops_unused() -> None:
    """Shared and cyclic definitions reached from the root stay; unused ones go."""
    schema: dict[str, Any] = {
        'type': 'object',
        'properties': {'value': {'$ref': '#/$defs/X'}},
        '$defs': {
            'X': {
                'type': 'object',
                'properties': {'c': {'$ref': '#/$defs/C'}, 'd': {'$ref': '#/$defs/D'}},
            },
            'C': {'type': 'object', 'properties': {'d': {'$ref': '#/$defs/D'}}},
            'D': {'type': 'object', 'properties': {'c': {'$ref': '#/$defs/C'}}},
            'Unused': {'type': 'string'},
        },
    }
    pruned = prune_unused_definitions(schema)
    assert set(pruned['$defs']) == {'X', 'C', 'D'}
    assert pruned['properties'] == schema['properties']


def test_prune_declines_a_reference_to_a_missing_definition() -> None:
    """A dangling local reference returns the schema unchanged."""
    schema: dict[str, Any] = {
        'properties': {'value': {'$ref': '#/$defs/Missing'}},
        '$defs': {'Other': {'type': 'string'}},
    }
    assert prune_unused_definitions(schema) is schema


def test_prune_without_references_drops_every_definition() -> None:
    """A root that references nothing loses its $defs entirely."""
    schema: dict[str, Any] = {'type': 'string', '$defs': {'Other': {'type': 'string'}}}
    assert prune_unused_definitions(schema) == {'type': 'string'}


# --- retained dependencies ---------------------------------------------------------


def _retained_event(result: object) -> StreamEvent:
    return StreamEvent(
        position=1,
        event_type='system_tool_start',
        data={
            'payload': {
                'tool_call': {
                    'call_id': 'new-summary',
                    'spec_ref': {'tool_id': 'summary', 'namespace': 'sdk', 'version': 'v1'},
                    'arguments': {},
                    'lineage': {'session_id': 'new-session', 'invocation_id': 'new-invocation'},
                    'retained_dependencies': {
                        'name': {
                            'thread_id': 'thread-A',
                            'source_session_id': 'prior-session',
                            'source_call_id': 'prior-call',
                            'producer': {
                                'namespace': 'sdk',
                                'tool_id': 'collect_name',
                                'version': 'v1',
                            },
                            'result': result,
                        }
                    },
                }
            }
        },
    )


def _run_retained(result: object, private_data: Mapping[object, object] | None = None) -> object:
    executed: list[object] = []

    def collect_name() -> str:
        return 'fresh'

    @depends_on_tool('collect_name', 'name', result_scope='conversation')
    def summary(name: object) -> object:
        executed.append(name)
        return name

    runtime = LocalToolRuntime(
        [
            ToolMetadata(name='collect_name', target=collect_name),
            ToolMetadata(name='summary', target=summary),
        ],
        private_data=private_data,
        thread_id='thread-A',
    )
    outcome = asyncio.run(runtime.outcome_for_event(_retained_event(result)))
    if isinstance(outcome, ErrorToolOutcome):
        assert executed == []
        return (outcome.error.code, outcome.error.message)
    assert isinstance(outcome, OkToolOutcome)
    return outcome.result


@pytest.mark.parametrize(
    'result',
    [
        {'nested': {'deep': '{_{missing_key}_}'}},
        ['ok', ('x', 'prefix {_{missing_key}_} suffix')],
    ],
)
def test_unresolved_placeholder_nested_in_retained_result_is_unavailable(result: object) -> None:
    """A placeholder left anywhere inside a carried value refuses the consumer."""
    assert _run_retained(result) == (
        'sdk_retained_dependency_unavailable',
        'Retained dependency is unavailable',
    )


def test_resolved_nested_retained_result_reaches_the_consumer() -> None:
    """Placeholders resolved from private data inside containers are injected."""
    result = _run_retained({'nested': ['{_{owner}_}', 3, None]}, private_data={'owner': 'Taylor'})
    assert result == {'nested': ['Taylor', 3, None]}
