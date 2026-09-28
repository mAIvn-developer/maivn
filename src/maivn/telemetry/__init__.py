"""Stable, versioned telemetry hooks for customer-visible mAIvn run events.

Listeners run synchronously inline and in registration order. A listener must
return promptly because its latency is added directly to the run stream; heavy
work belongs on the listener's own bounded queue or worker. The SDK does not
create an implicit dispatch worker, buffer events, silently drop them, or retry
delivery. Listener exceptions are isolated from the run and later listeners.

Telemetry is metadata-only. The schema has no message, tool argument/result,
final-result, or raw-payload field, so arrival-form content never reaches a
listener.
"""

from ._models import (
    TELEMETRY_SCHEMA_VERSION,
    RunTelemetryEvent,
    RunTelemetryScope,
    TelemetryListener,
    TokenUsageTelemetry,
    ToolCallTelemetry,
)
from ._registry import TelemetrySubscription, register_listener

__all__ = [
    'TELEMETRY_SCHEMA_VERSION',
    'RunTelemetryEvent',
    'RunTelemetryScope',
    'TelemetryListener',
    'TelemetrySubscription',
    'TokenUsageTelemetry',
    'ToolCallTelemetry',
    'register_listener',
]
