"""Characterize the user-facing error sanitizer that keeps internals out of messages."""

from __future__ import annotations

import pytest

from maivn._internal.sanitize import sanitize_user_facing_error_message

_INTERNAL = 'An internal error occurred. Please try again.'


@pytest.mark.parametrize(
    'message',
    [
        'failed reading /srv/app/config',
        'escaped path \\\\server\\share',
        'see README.md for details',
        'MAIVN_RUNTIME exploded',
        'ImportLib could not load the plugin',
        'LangGraph node crashed',
        'Traceback (most recent call last)',
        'File "worker" line 3',
    ],
)
def test_every_leak_marker_replaces_the_whole_message(message: str) -> None:
    """A message carrying any leak marker, in any letter case, is replaced wholesale."""
    assert sanitize_user_facing_error_message(message) == _INTERNAL


@pytest.mark.parametrize('message', ['Cannot open C:\\Users\\dev', 'drive z:\\data is gone'])
def test_windows_drive_paths_are_replaced(message: str) -> None:
    """A single-backslash Windows drive path is caught by the drive-letter pattern."""
    assert sanitize_user_facing_error_message(message) == _INTERNAL


def test_clean_message_is_returned_unchanged() -> None:
    """Ordinary text without leak markers passes through untouched."""
    message = 'The quota for this project is exhausted.'
    assert sanitize_user_facing_error_message(message) == message


def test_private_data_breach_message_is_explicit_even_with_markers() -> None:
    """The private-data refusal is kept verbatim, including any path it mentions."""
    message = 'LLM payload contains private data values at /payload/messages/0'
    assert sanitize_user_facing_error_message(message) == message


def test_agent_failure_prefix_is_stripped_to_its_reason() -> None:
    """An agent-failure wrapper reports only the reason after its first colon."""
    assert (
        sanitize_user_facing_error_message('Agent execution failed:  Budget: exhausted  ')
        == 'Budget: exhausted'
    )


def test_agent_failure_prefix_is_matched_case_insensitively() -> None:
    """The wrapper is recognized regardless of its capitalization."""
    assert sanitize_user_facing_error_message('AGENT EXECUTION FAILED: Bad input') == 'Bad input'


def test_agent_failure_reason_is_still_screened_for_markers() -> None:
    """A stripped reason that leaks internals is replaced like any other message."""
    assert (
        sanitize_user_facing_error_message('agent execution failed: open /etc/hosts') == _INTERNAL
    )


def test_agent_failure_screening_ignores_markers_before_the_colon() -> None:
    """Only the reason after the first colon is screened once the prefix is stripped."""
    assert (
        sanitize_user_facing_error_message('agent execution failed in maivn_core: Denied')
        == 'Denied'
    )


@pytest.mark.parametrize('message', ['agent execution failed:   ', 'agent execution failed'])
def test_agent_failure_without_a_reason_is_kept(message: str) -> None:
    """A wrapper with no reason after it is returned as written."""
    assert sanitize_user_facing_error_message(message) == message
