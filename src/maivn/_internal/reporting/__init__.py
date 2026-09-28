"""Internal stream compatibility projection and verbose terminal reporting."""

from __future__ import annotations

from maivn._internal.reporting.stream_events import StreamEventProjector
from maivn._internal.reporting.terminal_reporter.factory import create_reporter

__all__ = ['StreamEventProjector', 'create_reporter']
