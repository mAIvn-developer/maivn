# pyright: strict
"""Base reporter interface and shared default event handling."""

from __future__ import annotations

from abc import ABC

from .defaults import ReporterDefaultEventsMixin
from .interface import BaseReporterInterface


class BaseReporter(ReporterDefaultEventsMixin, BaseReporterInterface, ABC):
    """Abstract base class for terminal reporters."""


__all__ = ["BaseReporter"]
