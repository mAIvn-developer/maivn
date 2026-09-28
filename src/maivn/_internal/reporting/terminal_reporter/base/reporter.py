"""Shared interface for v2 SDK terminal reporters."""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from maivn._internal.models import InvokeResponse, StreamEvent


class BaseReporter(ABC):
    """Receive projected SDK events and render the terminal invocation summary."""

    def __init__(self, *, enabled: bool = True) -> None:
        """Set whether this reporter writes terminal output."""
        self.enabled = enabled

    @abstractmethod
    def report_event(self, event: StreamEvent) -> None:
        """Render one v1-visible stream event."""

    @abstractmethod
    def report_response(self, response: InvokeResponse) -> None:
        """Render the terminal response and v1 token summary."""
