"""Original intake is private custody, never a duck-typed tool hook protocol."""

# pyright: reportPrivateUsage=false

from __future__ import annotations

import asyncio

from maivn_contracts.messages import ImageContentBlock, RedactionConfig, TextContentBlock

from maivn._internal.inspection import capture_invoke_inspection
from maivn._internal.models import StreamEvent, ToolMetadata
from maivn._internal.tool_runtime import LocalToolRuntime
from maivn.messages import HumanMessage


def test_capture_includes_named_private_resolution_before_dispatch() -> None:
    """Known PII belongs to the same original local map as explicit private values."""
    messages = [
        HumanMessage(
            content='LOCAL-ORIGINAL',
            redaction=RedactionConfig.model_validate(
                {'known_pii_values': [{'name': 'owner', 'value': 'LOCAL-OWNER'}]}
            ),
        )
    ]
    normalized, context = capture_invoke_inspection(
        messages, private_data={'explicit': 'LOCAL-EXPLICIT'}, thread_id=None, project_id=None
    )
    assert context is not None
    assert context.private_data == {'owner': 'LOCAL-OWNER', 'explicit': 'LOCAL-EXPLICIT'}
    messages[0] = messages[0].model_copy(update={'content': 'changed caller input'})
    assert normalized[0].content == 'LOCAL-ORIGINAL'
    assert context.messages[0]['content'] == 'LOCAL-ORIGINAL'
    assert 'LOCAL-' not in repr(context)


def test_arbitrary_tool_hook_never_receives_original_intake() -> None:
    """An unowned method with the same name does not enroll a private reviewer."""

    class OtherTool:
        def __init__(self) -> None:
            self.calls = 0

        def inspect_layout(self) -> list[object]:
            self.calls += 1
            return []

        async def _sdk_inspection_dispatch(self, **_kwargs: object) -> object:
            message = 'unowned hook received private custody'
            raise AssertionError(message)

    owner = OtherTool()
    context = capture_invoke_inspection(
        'LOCAL-ORIGINAL', private_data={'secret': 'LOCAL-SECRET'}, thread_id=None, project_id=None
    )[1]
    assert context is not None
    runtime = LocalToolRuntime(
        [ToolMetadata(name='other', target=owner.inspect_layout)],
        private_data=None,
        inspection_context=context.accepted('session'),
    )
    event = StreamEvent(
        position=1,
        event_type='system_tool_start',
        data={
            'payload': {
                'tool_call': {
                    'call_id': 'call',
                    'spec_ref': {'tool_id': 'other', 'namespace': 'sdk', 'version': 'v1'},
                    'arguments': {},
                    'lineage': {'session_id': 'session', 'invocation_id': 'invocation'},
                }
            }
        },
    )
    result = asyncio.run(runtime.outcome_for_event(event))
    assert result is not None
    assert result.status == 'ok'
    assert owner.calls == 1


def test_inline_visual_reference_does_not_prove_complete_original_intent() -> None:
    """Visual source bytes are not held merely because an inline image has an ID."""
    message = HumanMessage(
        content=[
            TextContentBlock(type='text', text='Reproduce the labels from this image.'),
            ImageContentBlock(type='image', attachment_id='previous-upload', mime_type='image/png'),
        ]
    )
    normalized, context = capture_invoke_inspection(
        [message], private_data=None, thread_id=None, project_id='project'
    )
    assert normalized[0].attachments is None
    assert context is not None
    assert not context.complete_intent
