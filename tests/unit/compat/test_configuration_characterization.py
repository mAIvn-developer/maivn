"""Characterization tests for the context-local v1 configuration accessors."""

from __future__ import annotations

import contextvars

from maivn import MaivnConfiguration, get_configuration
from maivn._internal.compat.configuration import set_configuration


def test_get_configuration_creates_one_default_per_context() -> None:
    """The first read stores a default; later reads in that context reuse it."""

    def read_twice() -> tuple[MaivnConfiguration, MaivnConfiguration]:
        return get_configuration(), get_configuration()

    first, again = contextvars.copy_context().run(read_twice)
    assert first is again
    assert first == MaivnConfiguration()
    other, _ = contextvars.copy_context().run(read_twice)
    assert other == first


def test_set_configuration_replaces_the_context_value_only() -> None:
    """A configuration set in one context is what that context reads back."""
    custom = MaivnConfiguration.from_dict({'server': {'project_id': 'prj-1'}})

    def set_then_read() -> MaivnConfiguration:
        set_configuration(custom)
        return get_configuration()

    assert contextvars.copy_context().run(set_then_read) is custom
    assert contextvars.copy_context().run(get_configuration) is not custom


def test_to_dict_dumps_defaults_and_drops_unset_values() -> None:
    """``to_dict`` is the JSON dump without None values; unknown keys are ignored."""
    config = MaivnConfiguration.from_dict({'server': {'project_id': 'prj-1'}, 'legacy': True})
    assert config.to_dict() == {
        'server': {
            'base_url': 'https://api.maivn.io',
            'mock_base_url': 'https://api.maivn.io',
            'timeout_seconds': 30.0,
            'max_retries': 3,
            'deployment_timezone': 'UTC',
            'project_id': 'prj-1',
        },
        'execution': {
            'default_timeout_seconds': 30.0,
            'pending_event_timeout_seconds': 30.0,
            'max_parallel_tools': 8,
            'enable_background_execution': False,
            'tool_execution_timeout_seconds': 30.0,
            'dependency_wait_timeout_seconds': 30.0,
        },
        'security': {},
        'logging': {'level': 'INFO', 'enable_timing_logs': False},
    }
