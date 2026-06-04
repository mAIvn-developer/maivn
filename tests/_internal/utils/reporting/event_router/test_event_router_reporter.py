# pyright: strict
from __future__ import annotations

import threading
import time
from concurrent.futures import Future, ThreadPoolExecutor
from typing import cast

from maivn._internal.utils.reporting.terminal_reporter.base import BaseReporter
from maivn._internal.utils.reporting.terminal_reporter.event_router import EventRouterReporter


class _LegacyEnrichmentReporter:
    """Stub reporter exposing only the legacy 2-argument `report_enrichment` surface.

    Used to exercise `EventRouterReporter`'s fallback path for reporters that do
    not implement extended kwargs (`scope_id`, `memory`, etc.).
    """

    enabled: bool

    def __init__(self) -> None:
        self.enabled = True
        self.calls: list[dict[str, str]] = []

    def report_enrichment(self, *, phase: str, message: str) -> None:
        self.calls.append({"phase": phase, "message": message})


class _ScopeAwareLegacyMemoryReporter:
    """Stub reporter supporting scope kwargs but not `memory`."""

    enabled: bool

    def __init__(self) -> None:
        self.enabled = True
        self.calls: list[dict[str, str | None]] = []

    def report_enrichment(
        self,
        *,
        phase: str,
        message: str,
        scope_id: str | None = None,
        scope_name: str | None = None,
        scope_type: str | None = None,
    ) -> None:
        self.calls.append(
            {
                "phase": phase,
                "message": message,
                "scope_id": scope_id,
                "scope_name": scope_name,
                "scope_type": scope_type,
            }
        )


class _ScopeAwareMemoryReporter:
    """Stub reporter supporting scope kwargs and `memory` but not `redaction`."""

    enabled: bool

    def __init__(self) -> None:
        self.enabled = True
        self.calls: list[dict[str, object]] = []

    def report_enrichment(
        self,
        *,
        phase: str,
        message: str,
        scope_id: str | None = None,
        scope_name: str | None = None,
        scope_type: str | None = None,
        memory: dict[str, object] | None = None,
    ) -> None:
        self.calls.append(
            {
                "phase": phase,
                "message": message,
                "scope_id": scope_id,
                "scope_name": scope_name,
                "scope_type": scope_type,
                "memory": memory,
            }
        )


class _ModelReporter:
    enabled: bool

    def __init__(self) -> None:
        self.enabled = True
        self.calls: list[dict[str, object]] = []

    def report_model_tool_complete(self, tool_name: str, **kwargs: object) -> None:
        entry: dict[str, object] = {"tool_name": tool_name, **kwargs}
        self.calls.append(entry)


class _PhaseReporter:
    enabled: bool

    def __init__(self) -> None:
        self.enabled = True
        self.calls: list[str] = []

    def report_phase_change(self, phase: str) -> None:
        self.calls.append(phase)


class _InputReporter:
    enabled: bool

    def __init__(self) -> None:
        self.enabled = True
        self.calls: list[dict[str, object]] = []

    def get_input(
        self,
        prompt: str,
        *,
        input_type: str = "text",
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
        return "blue"


def _as_base_reporter(stub: object) -> BaseReporter:
    """Cast a duck-typed stub to BaseReporter for EventRouterReporter construction.

    Pattern 2: the EventRouterReporter only accesses the methods each stub exposes,
    so casting through `object` is the policy-approved boundary cast.
    """
    return cast(BaseReporter, stub)


def test_event_router_enrichment_falls_back_for_legacy_reporter() -> None:
    reporter = _LegacyEnrichmentReporter()
    sink_payloads: list[dict[str, object]] = []
    router = EventRouterReporter(_as_base_reporter(reporter), event_sink=sink_payloads.append)

    router.report_enrichment(
        phase="planning",
        message="Planning actions...",
        scope_id="scope-1",
        scope_name="coordinator",
        scope_type="agent",
        memory={"mode": "retrieve", "hit_count": 3},
    )

    assert reporter.calls == [{"phase": "planning", "message": "Planning actions..."}]
    assert sink_payloads
    first = sink_payloads[0]
    assert first["category"] == "enrichment"
    payload = cast(dict[str, object], first["payload"])
    assert payload["memory"] == {"mode": "retrieve", "hit_count": 3}


def test_event_router_enrichment_preserves_scope_when_memory_is_unsupported() -> None:
    reporter = _ScopeAwareLegacyMemoryReporter()
    sink_payloads: list[dict[str, object]] = []
    router = EventRouterReporter(_as_base_reporter(reporter), event_sink=sink_payloads.append)

    router.report_enrichment(
        phase="planning",
        message="Planning actions...",
        scope_id="scope-1",
        scope_name="coordinator",
        scope_type="agent",
        memory={"mode": "retrieve", "hit_count": 3},
    )

    assert reporter.calls == [
        {
            "phase": "planning",
            "message": "Planning actions...",
            "scope_id": "scope-1",
            "scope_name": "coordinator",
            "scope_type": "agent",
        }
    ]
    assert sink_payloads
    payload = cast(dict[str, object], sink_payloads[0]["payload"])
    assert payload["scope_id"] == "scope-1"
    assert payload["memory"] == {"mode": "retrieve", "hit_count": 3}


def test_event_router_enrichment_preserves_scope_and_memory_when_redaction_is_unsupported() -> None:
    reporter = _ScopeAwareMemoryReporter()
    sink_payloads: list[dict[str, object]] = []
    router = EventRouterReporter(_as_base_reporter(reporter), event_sink=sink_payloads.append)

    router.report_enrichment(
        phase="redaction_previewed",
        message="Redaction preview completed.",
        scope_id="scope-1",
        scope_name="coordinator",
        scope_type="agent",
        memory={"mode": "retrieve", "hit_count": 3},
        redaction={"inserted_keys": ["pii_email_1"], "redacted_value_count": 1},
    )

    assert reporter.calls == [
        {
            "phase": "redaction_previewed",
            "message": "Redaction preview completed.",
            "scope_id": "scope-1",
            "scope_name": "coordinator",
            "scope_type": "agent",
            "memory": {"mode": "retrieve", "hit_count": 3},
        }
    ]
    assert sink_payloads
    payload = cast(dict[str, object], sink_payloads[0]["payload"])
    assert payload["redaction"] == {
        "inserted_keys": ["pii_email_1"],
        "redacted_value_count": 1,
    }


def test_event_router_model_event_id_mapping_is_cleaned() -> None:
    reporter = _ModelReporter()
    router = EventRouterReporter(_as_base_reporter(reporter))

    router.report_model_tool_complete("Summary", event_id="model-123", result={"ok": True})

    assert reporter.calls
    # Accessing the protected mapping is intentional for this regression
    # assertion. ``cast(..., getattr(...))`` keeps the read out of
    # ``reportPrivateUsage`` AND ``reportAny``.
    mapping = cast(dict[str, str], getattr(router, "_tool_category_by_event_id"))  # noqa: B009
    assert mapping == {}


def test_event_router_sink_emission_is_serialized() -> None:
    reporter = _PhaseReporter()
    state_lock = threading.Lock()
    active_callbacks = 0
    max_active_callbacks = 0

    def sink(_payload: dict[str, object]) -> None:
        nonlocal active_callbacks, max_active_callbacks
        with state_lock:
            active_callbacks += 1
            max_active_callbacks = max(max_active_callbacks, active_callbacks)
        try:
            time.sleep(0.005)
        finally:
            with state_lock:
                active_callbacks -= 1

    router = EventRouterReporter(_as_base_reporter(reporter), event_sink=sink)

    with ThreadPoolExecutor(max_workers=8) as executor:
        futures: list[Future[None]] = [
            executor.submit(router.report_phase_change, "planning") for _ in range(32)
        ]
        for future in futures:
            future.result()

    assert len(reporter.calls) == 32
    assert max_active_callbacks == 1


def test_event_router_forwards_extended_get_input_kwargs() -> None:
    reporter = _InputReporter()
    router = EventRouterReporter(_as_base_reporter(reporter))

    result = router.get_input(
        "Color?",
        input_type="choice",
        choices=["blue", "green"],
        data_key="favorite_color",
        arg_name="favorite_color",
    )

    assert result == "blue"
    assert reporter.calls == [
        {
            "prompt": "Color?",
            "input_type": "choice",
            "choices": ["blue", "green"],
            "data_key": "favorite_color",
            "arg_name": "favorite_color",
        }
    ]


class _HookReporter:
    enabled: bool

    def __init__(self) -> None:
        self.enabled = True
        self.calls: list[dict[str, object]] = []

    def report_hook_fired(self, **kwargs: object) -> None:
        self.calls.append(dict(kwargs))


def test_event_router_forwards_hook_fired_to_wrapped_reporter() -> None:
    """``report_hook_fired`` must reach the wrapped reporter and the sink.

    Regression for a bug where Studio's ``EventRouterReporter`` wrapper
    silently dropped hook events because it inherited the no-op default
    from ``BaseReporter`` instead of forwarding to the wrapped reporter.
    """
    reporter = _HookReporter()
    sink_payloads: list[dict[str, object]] = []
    router = EventRouterReporter(_as_base_reporter(reporter), event_sink=sink_payloads.append)

    router.report_hook_fired(
        name="audit_log",
        stage="before",
        status="completed",
        target_type="tool",
        target_id="evt-1",
        target_name="my_tool",
        source="tool",
        error=None,
        elapsed_ms=4,
    )

    assert reporter.calls == [
        {
            "name": "audit_log",
            "stage": "before",
            "status": "completed",
            "target_type": "tool",
            "target_id": "evt-1",
            "target_name": "my_tool",
            "source": "tool",
            "error": None,
            "elapsed_ms": 4,
        }
    ]
    assert sink_payloads == [
        {
            "category": "lifecycle",
            "event": "hook_fired",
            "payload": {
                "name": "audit_log",
                "stage": "before",
                "status": "completed",
                "target_type": "tool",
                "target_id": "evt-1",
                "target_name": "my_tool",
                "source": "tool",
                "error": None,
                "elapsed_ms": 4,
            },
        }
    ]


def test_event_router_skips_hook_fired_when_lifecycle_excluded() -> None:
    """``exclude={"lifecycle"}`` drops hook firings before they reach the wrapped reporter."""
    reporter = _HookReporter()
    sink_payloads: list[dict[str, object]] = []
    router = EventRouterReporter(
        _as_base_reporter(reporter),
        exclude={"lifecycle"},
        event_sink=sink_payloads.append,
    )

    router.report_hook_fired(
        name="ignored",
        stage="before",
        status="completed",
        target_type="agent",
        target_id="a-1",
    )

    assert reporter.calls == []
    assert sink_payloads == []
