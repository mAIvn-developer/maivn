"""Characterize local presentation inspection custody, enrollment and hint admission."""

# pyright: reportPrivateUsage=false

from __future__ import annotations

import asyncio
import hashlib
import sys
import threading
import types
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, cast

import pytest
from maivn_contracts.messages import RedactionConfig
from maivn_contracts.tools import OkToolOutcome
from pydantic import BaseModel

from maivn._internal.inspection import (
    InspectionCall,
    InspectionSource,
    _enroll_offline_reviewer,
    _SemanticObservation,
    _SemanticReview,
    admitted_hints,
    capture_invoke_inspection,
    digest,
    freeze,
    inspection_call_seal,
    inspection_worker,
    presentation_owner,
    thaw,
)
from maivn._internal.models import StreamEvent, ToolMetadata
from maivn._internal.tool_runtime import LocalToolRuntime
from maivn.messages import HumanMessage

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable, Mapping

_OWNER_MODULE = 'maivn_tools.connectors.presentations.toolset'
_HANDLE = {'presentation_id': 'deck-1'}
_INTENT_HINT = (
    'Local semantic review advisory: an explicit intent obligation may be violated; '
    'review this occurrence against the original request.'
)
_AMBIGUOUS_HINT = (
    'Local semantic review advisory: this occurrence is ambiguous; review it '
    'against the original request.'
)
_STYLE_HINT = (
    'Local semantic review advisory: an explicit style obligation may be weak; '
    'review this occurrence against the original request.'
)


@dataclass
class _Dispatched:
    calls: list[InspectionCall] = field(default_factory=list[InspectionCall])
    arguments: list[dict[str, object]] = field(default_factory=list[dict[str, object]])


class _Owner:
    """Shape of the nominal presentation owner installed under the toolset module name."""

    def __init__(self) -> None:
        self._semantic_review_composition: object = None
        self.layout_calls = 0
        self.seen = _Dispatched()

    def inspect_layout(self, presentation: Mapping[str, object]) -> list[str]:
        """Return deterministic layout issues for the presentation."""
        self.layout_calls += 1
        return [f'deterministic:{presentation["presentation_id"]}']

    def other_tool(self, presentation: Mapping[str, object]) -> list[str]:
        """Another owner method that is not the inspection entry point."""
        return [f'other:{presentation["presentation_id"]}']

    async def _sdk_inspection_dispatch(
        self,
        *,
        call: InspectionCall,
        arguments: Mapping[str, object],
        dispatch: Callable[[], Awaitable[object]],
    ) -> object:
        self.seen.calls.append(call)
        self.seen.arguments.append(dict(arguments))
        return {'reviewed': await dispatch()}


def _install_owner_type(
    monkeypatch: pytest.MonkeyPatch, base: type[_Owner] | None = None
) -> type[_Owner]:
    owner_type = type('PresentationsToolSet', (base or _Owner,), {})
    assert issubclass(owner_type, _Owner)
    module = types.ModuleType(_OWNER_MODULE)
    setattr(module, 'PresentationsToolSet', owner_type)  # noqa: B010 - dynamic module export.
    monkeypatch.setitem(sys.modules, _OWNER_MODULE, module)
    return owner_type


async def _offline_review(_source: InspectionSource, _call: InspectionCall) -> object:
    return None


def _event(*, session_id: str = 'ses-1', tool_id: str = 'inspect') -> StreamEvent:
    return StreamEvent(
        position=1,
        event_type='system_tool_start',
        data={
            'payload': {
                'tool_call': {
                    'call_id': 'call-1',
                    'spec_ref': {'tool_id': tool_id, 'namespace': 'sdk', 'version': 'v1'},
                    'arguments': {'presentation': dict(_HANDLE)},
                    'lineage': {'session_id': session_id, 'invocation_id': 'inv-1'},
                }
            }
        },
    )


def _run_inspection(  # noqa: PLR0913 - each knob is one enrollment precondition.
    owner: _Owner,
    *,
    messages: str = 'Build the quarterly deck.',
    captured_private: Mapping[object, object] | None = None,
    runtime_private: Mapping[object, object] | None = None,
    thread_id: str | None = None,
    accepted_session: str | None = 'ses-1',
    event_session: str = 'ses-1',
    target: Callable[..., object] | None = None,
) -> object:
    context = capture_invoke_inspection(
        messages, private_data=captured_private, thread_id=thread_id, project_id='prj-1'
    )[1]
    assert context is not None
    if accepted_session is not None:
        context = context.accepted(accepted_session)
    runtime = LocalToolRuntime(
        [ToolMetadata(name='inspect', target=target or owner.inspect_layout)],
        private_data=runtime_private,
        inspection_context=context,
    )
    outcome = asyncio.run(runtime.outcome_for_event(_event(session_id=event_session)))
    assert outcome is not None
    assert outcome.status == 'ok'
    return outcome.result


def _source() -> InspectionSource:
    manifest = {
        'slides': [
            {
                'slide_id': 's1',
                'elements': [
                    {'element_id': 'e1', 'element': {'composition': {'composition_id': 'c1'}}},
                    {'element_id': 'e2', 'element': {'composition': {'composition_id': 'c2'}}},
                ],
            }
        ]
    }
    return InspectionSource(
        cast('Mapping[str, object]', freeze(manifest)),
        cast('Mapping[str, object]', freeze(_HANDLE)),
        None,
        (),
        'ordinary',
        object(),
    )


def _observation(
    criterion: object = 'source_fidelity',
    level: object = 0,
    slide_id: object = 's1',
    element_id: object = 'e1',
    composition_id: object = 'c1',
) -> _SemanticObservation:
    return _SemanticObservation(
        cast('str', criterion),
        cast('int', level),
        cast('str', slide_id),
        cast('str', element_id),
        cast('str', composition_id),
    )


def _review(*observations: _SemanticObservation) -> _SemanticReview:
    return _SemanticReview('presentation-semantic-advisory-v1', observations)


class _Model(BaseModel):
    name: str
    tags: list[str]


# --- freeze / thaw / digest -------------------------------------------------------


def test_freeze_detaches_models_mappings_and_sequences_into_read_only_values() -> None:
    """Models dump to JSON, mapping keys become strings and sequences become tuples."""
    frozen = freeze({1: [_Model(name='n', tags=['a']), (None, 2.5, True)], 'x': 'y'})
    frozen_type = type(frozen)
    assert frozen_type is types.MappingProxyType
    assert thaw(frozen) == {'1': [{'name': 'n', 'tags': ['a']}, [None, 2.5, True]], 'x': 'y'}
    inner = cast('Mapping[str, object]', frozen)['1']
    assert inner == ({'name': 'n', 'tags': ('a',)}, (None, 2.5, True))
    with pytest.raises(TypeError):
        cast('dict[str, object]', frozen)['x'] = 'changed'


@pytest.mark.parametrize('value', [{1, 2}, b'bytes', object()])
def test_freeze_refuses_unsupported_custody_values(value: object) -> None:
    """Sets, bytes and arbitrary objects are refused with one fixed message."""
    with pytest.raises(TypeError, match=r'^unsupported local inspection custody value$'):
        freeze({'nested': [value]})


def test_thaw_leaves_scalars_and_lists_untouched() -> None:
    """Only mappings and tuples are converted back to mutable JSON shapes."""
    original = [1, 2]
    assert thaw(original) is original
    assert thaw('text') == 'text'


def test_digest_is_sorted_compact_utf8_sha256() -> None:
    """The digest hashes canonical compact JSON with unescaped UTF-8."""
    expected = hashlib.sha256('{"a":[1,2],"b":"é"}'.encode()).hexdigest()
    assert digest(freeze({'b': 'é', 'a': (1, 2)})) == expected
    assert digest({'a': [1, 2], 'b': 'é'}) == expected


def test_digest_refuses_non_finite_numbers() -> None:
    """NaN cannot be canonicalized and raises instead of hashing."""
    with pytest.raises(ValueError, match='Out of range float values are not JSON compliant'):
        digest({'value': float('nan')})


# --- capture_invoke_inspection ----------------------------------------------------


def test_capture_binds_a_digest_that_tracks_session_acceptance() -> None:
    """Acceptance stamps the session and recomputes a digest that stays current."""
    context = capture_invoke_inspection(
        'Build slides', private_data={'k': 'v'}, thread_id=None, project_id='prj'
    )[1]
    assert context is not None
    assert context.complete_intent is True
    assert context.session_id is None
    assert context.project_id == 'prj'
    assert context.current()
    expected = digest((context.messages, {'k': 'v'}, None, 'prj', True, None))
    assert context.intent_digest == expected
    accepted = context.accepted('ses-9')
    assert accepted.session_id == 'ses-9'
    assert accepted.current()
    assert accepted.intent_digest == digest(
        (context.messages, {'k': 'v'}, None, 'prj', True, 'ses-9')
    )
    object.__setattr__(accepted, 'project_id', 'other')
    assert not accepted.current()


@pytest.mark.parametrize(
    ('thread_id', 'complete_history', 'expected'),
    [
        (None, None, True),
        ('thr', None, False),
        ('thr', True, True),
        (None, False, False),
    ],
)
def test_capture_complete_intent_follows_thread_freshness(
    thread_id: str | None, *, complete_history: bool | None, expected: bool
) -> None:
    """A continued thread is incomplete unless the caller asserts complete history."""
    context = capture_invoke_inspection(
        'Build slides',
        private_data=None,
        thread_id=thread_id,
        project_id=None,
        complete_history=complete_history,
    )[1]
    assert context is not None
    assert context.complete_intent is expected
    assert context.thread_id == thread_id


def test_capture_without_messages_is_never_complete_intent() -> None:
    """An empty intake cannot prove complete original intent."""
    normalized, context = capture_invoke_inspection(
        [], private_data=None, thread_id=None, project_id=None
    )
    assert normalized == []
    assert context is not None
    assert context.complete_intent is False


def test_capture_named_known_values_keep_first_name_and_explicit_values_override() -> None:
    """Unnamed and plain-string known values are skipped; explicit private data wins."""
    message = HumanMessage(
        content='hello',
        redaction=RedactionConfig.model_validate(
            {
                'known_pii_values': [
                    'plain-value',
                    {'value': 'unnamed-value'},
                    {'name': 'first', 'value': 'FIRST'},
                    {'name': 'first', 'value': 'SECOND'},
                    {'name': 'override', 'value': 'FROM-MESSAGE'},
                ]
            }
        ),
    )
    context = capture_invoke_inspection(
        [message], private_data={'override': 'EXPLICIT'}, thread_id=None, project_id=None
    )[1]
    assert context is not None
    assert thaw(context.private_data) == {'first': 'FIRST', 'override': 'EXPLICIT'}


def test_capture_with_unfreezable_private_data_returns_no_context() -> None:
    """Private values that cannot be held as JSON custody disable inspection entirely."""
    normalized, context = capture_invoke_inspection(
        'hello', private_data={'blob': b'raw'}, thread_id=None, project_id=None
    )
    assert context is None
    assert [message.content for message in normalized] == ['hello']


# --- presentation_owner and enrollment --------------------------------------------


def test_presentation_owner_requires_the_installed_nominal_type(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Only the exact owner type's inspect_layout identifies the presentation owner."""
    monkeypatch.delitem(sys.modules, _OWNER_MODULE, raising=False)
    owner_type = _install_owner_type(monkeypatch)
    owner = owner_type()
    assert presentation_owner(owner.inspect_layout) is owner
    assert presentation_owner(owner.other_tool) is None
    assert presentation_owner(_Owner().inspect_layout) is None
    subclass_owner = type('Sub', (owner_type,), {})()
    assert presentation_owner(subclass_owner.inspect_layout) is None
    assert presentation_owner(types.SimpleNamespace(__self__=owner, __func__=5)) is None
    assert presentation_owner(None) is None


def test_presentation_owner_is_none_without_the_toolset_module(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Without maivn-tools loaded no target can be a presentation owner."""
    monkeypatch.delitem(sys.modules, _OWNER_MODULE, raising=False)
    assert presentation_owner(_Owner().inspect_layout) is None


def test_enrollment_refuses_unowned_targets_and_sync_callbacks(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Enrollment names the nominal owner and an async callback in fixed messages."""
    owner_type = _install_owner_type(monkeypatch)
    with pytest.raises(
        TypeError, match=r'^local inspection requires the nominal presentation owner$'
    ):
        _enroll_offline_reviewer(_Owner(), _offline_review)

    def sync_review(_source: InspectionSource, _call: InspectionCall) -> object:
        return None

    owner = owner_type()
    with pytest.raises(TypeError, match=r'^local inspection requires an async offline callback$'):
        _enroll_offline_reviewer(owner, cast('Callable[..., Awaitable[object]]', sync_review))
    assert _run_inspection(owner) == ['deterministic:deck-1']
    assert owner.seen.calls == []


# --- owner dispatch through LocalToolRuntime --------------------------------------


def test_enrolled_owner_receives_a_current_sealed_call(monkeypatch: pytest.MonkeyPatch) -> None:
    """A complete, accepted, unchanged invocation routes through the owner dispatch."""
    owner = _install_owner_type(monkeypatch)()
    _enroll_offline_reviewer(owner, _offline_review)
    result = _run_inspection(owner, captured_private={'k': 'v'}, runtime_private={'k': 'v'})
    assert result == {'reviewed': ['deterministic:deck-1']}
    assert owner.seen.arguments == [{'presentation': _HANDLE}]
    (call,) = owner.seen.calls
    assert call.current()
    assert thaw(call.arguments) == {'presentation': _HANDLE}
    assert cast('dict[str, object]', thaw(call.tool_call))['call_id'] == 'call-1'
    assert call.invoke.session_id == 'ses-1'
    assert call.binding_digest == digest(
        (call.tool_call, call.arguments, call.invoke.intent_digest)
    )
    signature, references = inspection_call_seal(call)
    assert len(signature) == len(hashlib.sha256().hexdigest())
    assert references[:2] == (call.invoke, call.composition)
    assert inspection_call_seal(call)[0] == signature


def test_sealed_call_detects_field_substitution(monkeypatch: pytest.MonkeyPatch) -> None:
    """Substituting arguments or the digest invalidates both the seal and currency."""
    owner = _install_owner_type(monkeypatch)()
    _enroll_offline_reviewer(owner, _offline_review)
    _run_inspection(owner)
    (call,) = owner.seen.calls
    signature = inspection_call_seal(call)[0]
    object.__setattr__(call, 'arguments', freeze({'presentation': {'presentation_id': 'x'}}))
    assert not call.current()
    assert inspection_call_seal(call)[0] != signature


def test_sealed_call_detects_callback_replacement(monkeypatch: pytest.MonkeyPatch) -> None:
    """Replacing the composition callback after binding makes the call stale."""
    owner = _install_owner_type(monkeypatch)()
    _enroll_offline_reviewer(owner, _offline_review)
    _run_inspection(owner)
    (call,) = owner.seen.calls

    async def replacement(_source: InspectionSource, _call: InspectionCall) -> object:
        return 'replaced'

    object.__setattr__(call.composition, 'callback', replacement)
    assert not call.current()


@pytest.mark.parametrize(
    'case',
    [
        'not_enrolled',
        'not_accepted',
        'session_mismatch',
        'continued_thread',
        'private_changed',
        'private_unfreezable',
        'other_method',
    ],
)
def test_owner_dispatch_is_skipped_unless_every_binding_holds(
    monkeypatch: pytest.MonkeyPatch, case: str
) -> None:
    """Any missing precondition falls back to the plain tool call without the owner hook."""
    owner = _install_owner_type(monkeypatch)()
    if case != 'not_enrolled':
        _enroll_offline_reviewer(owner, _offline_review)
    kwargs: dict[str, object] = {}
    expected: object = ['deterministic:deck-1']
    if case == 'not_accepted':
        kwargs['accepted_session'] = None
    elif case == 'session_mismatch':
        kwargs['event_session'] = 'ses-other'
    elif case == 'continued_thread':
        kwargs['thread_id'] = 'thr-1'
    elif case == 'private_changed':
        kwargs.update(captured_private={'k': 'v'}, runtime_private={'k': 'changed'})
    elif case == 'private_unfreezable':
        kwargs.update(captured_private={'k': 'v'}, runtime_private={'k': {1, 2}})
    elif case == 'other_method':
        kwargs['target'] = owner.other_tool
        expected = ['other:deck-1']
    result = _run_inspection(owner, **kwargs)  # pyright: ignore[reportArgumentType]
    assert result == expected
    assert owner.seen.calls == []


class _HangingOwner(_Owner):
    """Owner whose review waits until cancelled, recording the cancellation."""

    def __init__(self) -> None:
        super().__init__()
        self.entered = asyncio.Event()
        self.cancelled = False

    async def _sdk_inspection_dispatch(
        self,
        *,
        call: InspectionCall,
        arguments: Mapping[str, object],  # noqa: ARG002 - owner dispatch signature.
        dispatch: Callable[[], Awaitable[object]],
    ) -> object:
        self.seen.calls.append(call)
        self.entered.set()
        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            self.cancelled = True
            raise
        return await dispatch()


def test_cancelling_a_serialized_owner_review_cancels_the_review(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A started async review receives cancellation and the lane is then free again."""
    owner = cast('_HangingOwner', _install_owner_type(monkeypatch, _HangingOwner)())
    _enroll_offline_reviewer(owner, _offline_review)
    context = capture_invoke_inspection(
        'Build the deck.', private_data=None, thread_id=None, project_id=None
    )[1]
    assert context is not None
    runtime = LocalToolRuntime(
        [
            ToolMetadata(
                name='inspect', target=owner.inspect_layout, metadata={'serialize_calls': True}
            ),
            ToolMetadata(name='other', target=owner.other_tool, metadata={'serialize_calls': True}),
        ],
        private_data=None,
        inspection_context=context.accepted('ses-1'),
    )

    async def scenario() -> object:
        task = asyncio.create_task(runtime.outcome_for_event(_event()))
        await owner.entered.wait()
        task.cancel()
        # Bounded: a review that never receives cancellation must fail, not hang.
        _ = await asyncio.wait({task}, timeout=5.0)
        assert task.cancelled()
        follow_up = await runtime.outcome_for_event(_event(tool_id='other'))
        assert isinstance(follow_up, OkToolOutcome)
        return follow_up.result

    assert asyncio.run(scenario()) == ['other:deck-1']
    assert owner.cancelled is True
    assert owner.layout_calls == 0


# --- inspection_worker -------------------------------------------------------------


def test_inspection_worker_returns_and_raises_worker_results() -> None:
    """The worker result or exception surfaces unchanged to the awaiting caller."""
    assert asyncio.run(inspection_worker(lambda: 'done')) == 'done'

    def fail() -> object:
        message = 'worker failed'
        raise RuntimeError(message)

    with pytest.raises(RuntimeError, match=r'^worker failed$'):
        asyncio.run(inspection_worker(fail))


@pytest.mark.parametrize('worker_fails', [False, True])
def test_inspection_worker_drains_the_thread_before_propagating_cancellation(
    *, worker_fails: bool
) -> None:
    """Repeated cancellation waits for the owned worker to finish, then re-raises."""
    started = threading.Event()
    release = threading.Event()
    finished: list[str] = []

    def work() -> object:
        started.set()
        release.wait(5)
        finished.append('done')
        if worker_fails:
            message = 'late failure'
            raise RuntimeError(message)
        return 'ignored'

    async def scenario() -> bool:
        task = asyncio.create_task(inspection_worker(work))
        await asyncio.to_thread(started.wait, 5)
        task.cancel()
        await asyncio.sleep(0.01)
        task.cancel()
        await asyncio.sleep(0.01)
        still_running = not task.done()
        release.set()
        with pytest.raises(asyncio.CancelledError):
            await task
        return still_running

    assert asyncio.run(scenario()) is True
    assert finished == ['done']


# --- admitted_hints ----------------------------------------------------------------


def test_admitted_hints_maps_levels_to_fixed_advisories() -> None:
    """Levels 0-2 produce fixed advisories in order; level 3 is supported and silent."""
    review = _review(
        _observation('source_fidelity', 0),
        _observation('intent_completeness', 1),
        _observation('prose_presentation', 2, element_id='e2', composition_id='c2'),
        _observation('prose_presentation', 3),
    )
    assert admitted_hints(review, _source()) == [
        {
            'slide_id': 's1',
            'element_id': 'e1',
            'code': 'semantic_review_source_fidelity',
            'message': _INTENT_HINT,
        },
        {
            'slide_id': 's1',
            'element_id': 'e1',
            'code': 'semantic_review_intent_completeness',
            'message': _AMBIGUOUS_HINT,
        },
        {
            'slide_id': 's1',
            'element_id': 'e2',
            'code': 'semantic_review_prose_presentation',
            'message': _STYLE_HINT,
        },
    ]


def test_admitted_hints_with_no_observations_is_empty() -> None:
    """A valid review with no observations admits nothing."""
    assert admitted_hints(_review(), _source()) == []


@pytest.mark.parametrize(
    'review',
    [
        pytest.param(None, id='not-a-review'),
        pytest.param(
            _SemanticReview('presentation-semantic-advisory-v2', (_observation(),)), id='rubric'
        ),
        pytest.param(_SemanticReview(cast('str', 1), (_observation(),)), id='rubric-not-string'),
        pytest.param(
            _SemanticReview(
                'presentation-semantic-advisory-v1',
                cast('tuple[_SemanticObservation, ...]', [_observation()]),
            ),
            id='observations-list',
        ),
        pytest.param(
            _review(cast('_SemanticObservation', {'criterion': 'source_fidelity'})),
            id='observation-type',
        ),
        pytest.param(_review(_observation(slide_id=1)), id='slide-not-string'),
        pytest.param(_review(_observation(criterion=None)), id='criterion-not-string'),
        pytest.param(_review(_observation(composition_id=b'c1')), id='composition-not-string'),
        pytest.param(_review(_observation(criterion='tone')), id='unknown-criterion'),
        pytest.param(_review(_observation(level=True)), id='bool-level'),
        pytest.param(_review(_observation(level=4)), id='level-above-range'),
        pytest.param(_review(_observation(level=-1)), id='level-below-range'),
        pytest.param(_review(_observation(element_id='e9')), id='unknown-element'),
        pytest.param(
            _review(_observation(element_id='e1', composition_id='c2')), id='wrong-occurrence'
        ),
        pytest.param(_review(_observation(), _observation(level=1)), id='duplicate-key'),
        pytest.param(_review(_observation(), _observation(criterion='tone')), id='late-invalid'),
    ],
)
def test_admitted_hints_is_all_or_nothing(review: object) -> None:
    """Any malformed review or observation discards every hint."""
    assert admitted_hints(review, _source()) == []
