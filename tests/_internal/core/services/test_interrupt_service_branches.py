# pyright: strict
from __future__ import annotations

import builtins
import inspect
from collections.abc import Callable
from typing import cast

import pytest

from maivn._internal.core.services.interrupt_service import (
    InterruptService,
    default_terminal_interrupt,
    get_interrupt_service,
    set_interrupt_service,
)
from maivn._internal.utils.reporting.context import set_current_reporter
from maivn._internal.utils.reporting.terminal_reporter import BaseReporter
from maivn._internal.utils.reporting.terminal_reporter.base.interface import (
    BaseReporterInterface,
)

# The fakes below intentionally implement only the surface InterruptService
# touches; we cast at the boundary to satisfy the abstract reporter types.


class _PromptOnlyReporter:
    def __init__(self) -> None:
        self.prompts: list[str] = []

    def get_input(self, prompt: str) -> str:
        self.prompts.append(prompt)
        return "reporter-prompt-only"


class _SignatureBreakingReporter:
    def __init__(self) -> None:
        self.prompts: list[str] = []

    def get_input(self, prompt: str) -> str:
        self.prompts.append(prompt)
        return "reporter-fallback"


class _ContextReporter:
    def get_input(self, prompt: str, **_: object) -> str:
        return f"context:{prompt}"


def _as_reporter_interface(reporter: object) -> BaseReporterInterface:
    return cast(BaseReporterInterface, reporter)


def _as_base_reporter(reporter: object) -> BaseReporter:
    return cast(BaseReporter, reporter)


def _default_terminal_input(service: InterruptService, prompt: str) -> str:
    """Invoke the protected ``_default_terminal_input`` indirectly via getattr."""
    default_input_attr = "_default_terminal_input"
    handler = cast(Callable[[str], str], getattr(service, default_input_attr))
    return handler(prompt)


_CallReporterGetInput = Callable[..., str]


def _call_reporter_get_input(
    service: InterruptService,
    prompt: str,
    *,
    input_type: str,
    choices: list[str] | None,
    data_key: str | None,
    arg_name: str | None,
    reporter: BaseReporterInterface | None = None,
) -> str:
    """Invoke the protected ``_call_reporter_get_input`` via getattr."""
    reporter_input_attr = "_call_reporter_get_input"
    target = cast(_CallReporterGetInput, getattr(service, reporter_input_attr))
    return target(
        prompt,
        input_type=input_type,
        choices=choices,
        data_key=data_key,
        arg_name=arg_name,
        reporter=reporter,
    )


def test_interrupt_service_default_terminal_input_uses_builtin_input(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service = InterruptService()

    def _fake_input(prompt: object = "") -> str:
        return f"typed:{prompt}"

    monkeypatch.setattr(builtins, "input", _fake_input)

    assert _default_terminal_input(service, "Prompt") == "typed:Prompt"


def test_interrupt_service_uses_context_reporter_when_instance_reporter_is_missing() -> None:
    set_current_reporter(_as_base_reporter(_ContextReporter()))
    service = InterruptService()

    result = service.get_user_input("Prompt")

    assert result == "context:Prompt"


def test_interrupt_service_supports_prompt_only_reporter_signature() -> None:
    reporter = _PromptOnlyReporter()
    service = InterruptService(reporter=_as_reporter_interface(reporter))

    result = service.get_user_input("Prompt", input_type="choice", choices=["a", "b"])

    assert result == "reporter-prompt-only"
    assert reporter.prompts == ["Prompt"]


def test_interrupt_service_falls_back_when_signature_introspection_raises(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    reporter = _SignatureBreakingReporter()
    service = InterruptService(reporter=_as_reporter_interface(reporter))

    def _raise(_: object) -> inspect.Signature:
        raise ValueError("bad")

    monkeypatch.setattr("inspect.signature", _raise)

    result = _call_reporter_get_input(
        service,
        "Prompt",
        input_type="choice",
        choices=["a", "b"],
        data_key="payload",
        arg_name="answer",
        reporter=_as_reporter_interface(reporter),
    )

    assert result == "reporter-fallback"
    assert reporter.prompts == ["Prompt"]


def test_interrupt_service_reporter_input_falls_back_to_handler() -> None:
    service = InterruptService(input_handler=lambda prompt: f"handled:{prompt}")

    result = _call_reporter_get_input(
        service,
        "Prompt",
        input_type="text",
        choices=None,
        data_key=None,
        arg_name=None,
    )

    assert result == "handled:Prompt"


def test_interrupt_service_choice_validates_inputs() -> None:
    service = InterruptService(input_handler=lambda prompt: "1")

    with pytest.raises(ValueError, match="Choices list cannot be empty"):
        _ = service.get_user_choice("Pick", [])

    with pytest.raises(ValueError, match="Default index out of range"):
        _ = service.get_user_choice("Pick", ["a"], default_index=1)


def test_interrupt_service_choice_retries_until_response_is_valid(
    capsys: pytest.CaptureFixture[str],
) -> None:
    responses = iter(["bad", "3", "2"])
    service = InterruptService(input_handler=lambda prompt: next(responses))

    result = service.get_user_choice("Pick", ["a", "b"])

    assert result == "b"
    stderr = capsys.readouterr().err
    assert "Please enter a number" in stderr
    assert "Please choose a number between 1 and 2" in stderr


def test_interrupt_service_choice_returns_default_when_input_is_interrupted() -> None:
    def _interrupt(_: str) -> str:
        raise KeyboardInterrupt

    service = InterruptService(input_handler=_interrupt)

    assert service.get_user_choice("Pick", ["a", "b"], default_index=1) == "b"


def test_interrupt_service_confirmation_handles_yes_no_and_blank_defaults() -> None:
    responses = iter(["yes", "no", ""])
    service = InterruptService(input_handler=lambda prompt: next(responses))

    assert service.get_user_confirmation("Continue", default=False) is True
    assert service.get_user_confirmation("Continue", default=True) is False
    assert service.get_user_confirmation("Continue", default=True) is True


def test_interrupt_service_confirmation_retries_until_boolean_response_is_valid(
    capsys: pytest.CaptureFixture[str],
) -> None:
    responses = iter(["maybe", "y"])
    service = InterruptService(input_handler=lambda prompt: next(responses))

    assert service.get_user_confirmation("Continue", default=False) is True
    assert "Please answer 'y' or 'n'" in capsys.readouterr().err


def test_interrupt_service_set_and_get_global_service() -> None:
    service = InterruptService(input_handler=lambda prompt: "global-response")

    set_interrupt_service(service)

    assert get_interrupt_service() is service
    assert default_terminal_interrupt("Prompt") == "global-response"


def test_interrupt_service_set_reporter_overrides_existing_behavior() -> None:
    reporter = _PromptOnlyReporter()
    service = InterruptService(input_handler=lambda prompt: "handler")

    service.set_reporter(_as_reporter_interface(reporter))

    assert service.get_user_input("Prompt") == "reporter-prompt-only"
