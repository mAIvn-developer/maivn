from __future__ import annotations

import threading
import time
from concurrent.futures import ThreadPoolExecutor
from typing import Any

from maivn._internal.utils.reporting.terminal_reporter.event_router import EventRouterReporter


class _LegacyEnrichmentReporter:
    def __init__(self) -> None:
        self.enabled = True
        self.calls: list[dict[str, str]] = []

    def report_enrichment(self, *, phase: str, message: str) -> None:
        self.calls.append({"phase": phase, "message": message})


class _ScopeAwareLegacyMemoryReporter:
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
    def __init__(self) -> None:
        self.enabled = True
        self.calls: list[dict[str, Any]] = []

    def report_enrichment(
        self,
        *,
        phase: str,
        message: str,
        scope_id: str | None = None,
        scope_name: str | None = None,
        scope_type: str | None = None,
        memory: dict[str, Any] | None = None,
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
    def __init__(self) -> None:
        self.enabled = True
        self.calls: list[dict[str, Any]] = []

    def report_model_tool_complete(self, tool_name: str, **kwargs: Any) -> None:
        self.calls.append({"tool_name": tool_name, **kwargs})


class _PhaseReporter:
    def __init__(self) -> None:
        self.enabled = True
        self.calls: list[str] = []

    def report_phase_change(self, phase: str) -> None:
        self.calls.append(phase)


class _InputReporter:
    def __init__(self) -> None:
        self.enabled = True
        self.calls: list[dict[str, Any]] = []

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


def test_event_router_enrichment_falls_back_for_legacy_reporter() -> None:
    reporter = _LegacyEnrichmentReporter()
    sink_payloads: list[dict[str, Any]] = []
    router = EventRouterReporter(reporter, event_sink=sink_payloads.append)

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
    assert sink_payloads[0]["category"] == "enrichment"
    assert sink_payloads[0]["payload"]["memory"] == {"mode": "retrieve", "hit_count": 3}


def test_event_router_enrichment_preserves_scope_when_memory_is_unsupported() -> None:
    reporter = _ScopeAwareLegacyMemoryReporter()
    sink_payloads: list[dict[str, Any]] = []
    router = EventRouterReporter(reporter, event_sink=sink_payloads.append)

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
    assert sink_payloads[0]["payload"]["scope_id"] == "scope-1"
    assert sink_payloads[0]["payload"]["memory"] == {"mode": "retrieve", "hit_count": 3}


def test_event_router_enrichment_preserves_scope_and_memory_when_redaction_is_unsupported() -> None:
    reporter = _ScopeAwareMemoryReporter()
    sink_payloads: list[dict[str, Any]] = []
    router = EventRouterReporter(reporter, event_sink=sink_payloads.append)

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
    assert sink_payloads[0]["payload"]["redaction"] == {
        "inserted_keys": ["pii_email_1"],
        "redacted_value_count": 1,
    }


def test_event_router_model_event_id_mapping_is_cleaned() -> None:
    reporter = _ModelReporter()
    router = EventRouterReporter(reporter)

    router.report_model_tool_complete("Summary", event_id="model-123", result={"ok": True})

    assert reporter.calls
    assert router._tool_category_by_event_id == {}


def test_event_router_sink_emission_is_serialized() -> None:
    reporter = _PhaseReporter()
    state_lock = threading.Lock()
    active_callbacks = 0
    max_active_callbacks = 0

    def sink(_payload: dict[str, Any]) -> None:
        nonlocal active_callbacks, max_active_callbacks
        with state_lock:
            active_callbacks += 1
            max_active_callbacks = max(max_active_callbacks, active_callbacks)
        try:
            time.sleep(0.005)
        finally:
            with state_lock:
                active_callbacks -= 1

    router = EventRouterReporter(reporter, event_sink=sink)

    with ThreadPoolExecutor(max_workers=8) as executor:
        futures = [executor.submit(router.report_phase_change, "planning") for _ in range(32)]
        for future in futures:
            future.result()

    assert len(reporter.calls) == 32
    assert max_active_callbacks == 1


def test_event_router_forwards_extended_get_input_kwargs() -> None:
    reporter = _InputReporter()
    router = EventRouterReporter(reporter)

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
