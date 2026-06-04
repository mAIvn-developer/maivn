# pyright: strict
from __future__ import annotations

from typing import cast

import pytest

from maivn._internal.core.services.interrupt_service import InterruptService, MockInterruptService
from maivn._internal.utils.reporting.terminal_reporter.base.interface import BaseReporterInterface


class _StubReporter:
    calls: list[dict[str, object]]

    def __init__(self) -> None:
        self.calls = []

    def get_input(
        self,
        prompt: str,
        *,
        input_type: str | None = None,
        choices: list[str] | None = None,
        data_key: str | None = None,
        arg_name: str | None = None,
    ) -> str:
        self.calls.append(
            {
                "prompt": prompt,
                "input_type": input_type,
                "choices": choices,
                "data_key": data_key,
                "arg_name": arg_name,
            }
        )
        return "reporter-value"


def _as_reporter(stub: _StubReporter) -> BaseReporterInterface:
    """Bridge the duck-typed reporter stub to BaseReporterInterface."""
    return cast(BaseReporterInterface, cast(object, stub))


def test_interrupt_service_uses_reporter_with_extra_args() -> None:
    reporter = _StubReporter()
    service = InterruptService(reporter=_as_reporter(reporter))

    value = service.get_user_input("Prompt", input_type="choice", choices=["a"])

    assert value == "reporter-value"
    assert reporter.calls[0]["input_type"] == "choice"
    assert reporter.calls[0]["choices"] == ["a"]


def test_interrupt_service_falls_back_to_handler() -> None:
    def handler(prompt: str) -> str:
        _ = prompt
        return "handler-value"

    service = InterruptService(input_handler=handler)
    assert service.get_user_input("Prompt") == "handler-value"


def test_interrupt_service_choice_defaults_on_error() -> None:
    responses = ["", "2"]

    def handler(prompt: str) -> str:
        _ = prompt
        return responses.pop(0)

    service = InterruptService(input_handler=handler)
    choice = service.get_user_choice("Pick", ["a", "b"], default_index=1)

    assert choice == "b"


def test_interrupt_service_confirmation_default_on_interrupt() -> None:
    def handler(prompt: str) -> str:
        _ = prompt
        raise KeyboardInterrupt

    service = InterruptService(input_handler=handler)
    assert service.get_user_confirmation("Continue", default=True) is True


def test_mock_interrupt_service_sequence() -> None:
    service = MockInterruptService(responses=["one", "two"])

    assert service.get_user_input("Prompt") == "one"
    assert service.get_user_input("Prompt") == "two"

    with pytest.raises(RuntimeError, match="response"):
        _ = service.get_user_input("Prompt")

    service.add_response("three")
    service.reset()
    assert service.get_user_input("Prompt") == "one"
