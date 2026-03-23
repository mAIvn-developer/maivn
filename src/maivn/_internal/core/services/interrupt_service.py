"""Interrupt service for handling user interrupt dependencies.

This service provides default implementations for interrupt handling
and can be extended for different interaction modes.
"""

from __future__ import annotations

import sys
import threading
from collections.abc import Callable
from typing import Any

# MARK: - InterruptService


class InterruptService:
    """Service for handling interrupt requests."""

    def __init__(
        self,
        input_handler: Callable[[str], str] | None = None,
        reporter: Any = None,
    ) -> None:
        """Initialize interrupt service.

        Args:
            input_handler: Optional custom input handler function
            reporter: Optional terminal reporter for coordinated interrupt collection
        """
        self._input_handler = input_handler or self._default_terminal_input
        self._reporter = reporter
        self._input_lock = threading.Lock()

    # MARK: - Public Methods

    def get_user_input(
        self,
        prompt: str,
        *,
        input_type: str = "text",
        choices: list[str] | None = None,
        data_key: str | None = None,
        arg_name: str | None = None,
    ) -> str:
        """Get user input using the configured handler.

        Args:
            prompt: Prompt to display to user
            input_type: Input type hint for reporters (text, choice, boolean, etc.)
            choices: Optional choices for choice/literal input types
            data_key: Optional data key for interrupt storage
            arg_name: Optional argument name in the tool signature

        Returns:
            User input string

        Raises:
            RuntimeError: If input is interrupted or cancelled
        """
        with self._input_lock:
            try:
                # Resolve reporter: first check self._reporter, then context variable
                reporter = self._resolve_reporter()

                if reporter and hasattr(reporter, "get_input"):
                    return self._call_reporter_get_input(
                        prompt,
                        input_type=input_type,
                        choices=choices,
                        data_key=data_key,
                        arg_name=arg_name,
                        reporter=reporter,
                    )
                return self._input_handler(prompt)
            except (EOFError, KeyboardInterrupt) as e:
                raise RuntimeError(f"Interrupt request cancelled: {e}") from e

    def _resolve_reporter(self) -> Any:
        """Resolve the reporter to use for interrupt collection.

        Checks self._reporter first, then falls back to current_reporter context variable.

        Returns:
            Reporter instance or None
        """
        if self._reporter is not None:
            return self._reporter

        # Fallback to context variable for environments like Studio
        try:
            from maivn._internal.utils.reporting.context import get_current_reporter

            return get_current_reporter()
        except ImportError:
            return None

    def get_user_confirmation(
        self,
        prompt: str,
        default: bool = False,
        *,
        data_key: str | None = None,
        arg_name: str | None = None,
    ) -> bool:
        """Get yes/no confirmation from user.

        Args:
            prompt: Confirmation prompt
            default: Default value if user just presses enter

        Returns:
            True for yes, False for no
        """
        default_text = " (Y/n)" if default else " (y/N)"
        full_prompt = f"{prompt}{default_text}: "

        while True:
            try:
                response = (
                    self.get_user_input(
                        full_prompt,
                        input_type="boolean",
                        data_key=data_key,
                        arg_name=arg_name,
                    )
                    .lower()
                    .strip()
                )

                if not response:
                    return default
                if response in ("y", "yes", "true", "1"):
                    return True
                if response in ("n", "no", "false", "0"):
                    return False

                print("Please answer 'y' or 'n'", file=sys.stderr)
            except RuntimeError:
                return default

    def get_user_choice(
        self,
        prompt: str,
        choices: list[str],
        default_index: int = 0,
        *,
        data_key: str | None = None,
        arg_name: str | None = None,
    ) -> str:
        """Get user choice from a list of options.

        Args:
            prompt: Prompt to display
            choices: List of valid choices
            default_index: Index of default choice

        Returns:
            Selected choice string

        Raises:
            ValueError: If choices list is empty or default_index is out of range
        """
        if not choices:
            raise ValueError("Choices list cannot be empty")
        if not 0 <= default_index < len(choices):
            raise ValueError("Default index out of range")

        self._display_choices(prompt, choices, default_index)

        while True:
            try:
                response = self.get_user_input(
                    f"Choose (1-{len(choices)}): ",
                    input_type="choice",
                    choices=choices,
                    data_key=data_key,
                    arg_name=arg_name,
                ).strip()

                if not response:
                    return choices[default_index]

                index = self._parse_choice_index(response, len(choices))
                if index is not None:
                    return choices[index]
            except RuntimeError:
                return choices[default_index]

    def set_reporter(self, reporter: Any) -> None:
        """Set the terminal reporter for coordinated interrupt collection.

        Args:
            reporter: Terminal reporter to use
        """
        self._reporter = reporter

    # MARK: - Private Methods

    def _default_terminal_input(self, prompt: str) -> str:
        """Default terminal input handler.

        Args:
            prompt: Prompt to display

        Returns:
            User input from terminal
        """
        return input(prompt)

    def _display_choices(self, prompt: str, choices: list[str], default_index: int) -> None:
        """Display choice options to user.

        Args:
            prompt: Prompt to display
            choices: List of choices
            default_index: Index of default choice
        """
        print(f"\n{prompt}")
        for i, choice in enumerate(choices):
            marker = " (default)" if i == default_index else ""
            print(f"  {i + 1}. {choice}{marker}")

    def _parse_choice_index(self, response: str, num_choices: int) -> int | None:
        """Parse user response into a valid choice index.

        Args:
            response: User response string
            num_choices: Number of available choices

        Returns:
            Valid index or None if invalid
        """
        try:
            index = int(response) - 1
            if 0 <= index < num_choices:
                return index
            print(f"Please choose a number between 1 and {num_choices}", file=sys.stderr)
        except ValueError:
            print("Please enter a number", file=sys.stderr)
        return None

    def _call_reporter_get_input(
        self,
        prompt: str,
        *,
        input_type: str,
        choices: list[str] | None,
        data_key: str | None,
        arg_name: str | None,
        reporter: Any | None = None,
    ) -> str:
        """Call reporter.get_input with optional extended args when supported."""
        resolved_reporter = reporter if reporter is not None else self._reporter
        if resolved_reporter is None:
            return self._input_handler(prompt)

        try:
            import inspect

            sig = inspect.signature(resolved_reporter.get_input)
            params = sig.parameters
            kwargs: dict[str, Any] = {}

            if "input_type" in params:
                kwargs["input_type"] = input_type
            if "choices" in params:
                kwargs["choices"] = choices or []
            if "data_key" in params:
                kwargs["data_key"] = data_key
            if "arg_name" in params:
                kwargs["arg_name"] = arg_name

            return resolved_reporter.get_input(prompt, **kwargs)
        except Exception:
            return resolved_reporter.get_input(prompt)


# MARK: - MockInterruptService


class MockInterruptService(InterruptService):
    """Mock interrupt service for testing."""

    def __init__(self, responses: list[str] | None = None) -> None:
        """Initialize mock service with predefined responses.

        Args:
            responses: List of responses to return in order
        """
        super().__init__()
        self._responses = responses or []
        self._response_index = 0

    def get_user_input(
        self,
        prompt: str,
        *,
        input_type: str = "text",
        choices: list[str] | None = None,
        data_key: str | None = None,
        arg_name: str | None = None,
    ) -> str:
        """Return the next mock response.

        Args:
            prompt: Prompt (logged but not used)
            input_type: Input type hint for reporters (unused)
            choices: Optional choices (unused)
            data_key: Optional data key (unused)
            arg_name: Optional argument name (unused)

        Returns:
            Next mock response

        Raises:
            RuntimeError: If no more responses available
        """
        if self._response_index >= len(self._responses):
            raise RuntimeError(f"No mock response available for prompt: {prompt}")

        _ = (input_type, choices, data_key, arg_name)
        response = self._responses[self._response_index]
        self._response_index += 1
        return response

    def add_response(self, response: str) -> None:
        """Add a response to the mock service.

        Args:
            response: Response to add
        """
        self._responses.append(response)

    def reset(self) -> None:
        """Reset the response index."""
        self._response_index = 0


# MARK: - Module-Level Functions

_default_interrupt_service = InterruptService()


def get_interrupt_service() -> InterruptService:
    """Get the default interrupt service instance.

    Returns:
        Default interrupt service
    """
    return _default_interrupt_service


def set_interrupt_service(service: InterruptService) -> None:
    """Set the global interrupt service.

    Args:
        service: Interrupt service to use globally
    """
    global _default_interrupt_service
    _default_interrupt_service = service


def default_terminal_interrupt(prompt: str) -> str:
    """Default terminal interrupt function that can be used directly.

    Args:
        prompt: Prompt to display to user

    Returns:
        User input string
    """
    return _default_interrupt_service.get_user_input(prompt)


# MARK: - Exports

__all__ = [
    "InterruptService",
    "MockInterruptService",
    "get_interrupt_service",
    "set_interrupt_service",
    "default_terminal_interrupt",
]
