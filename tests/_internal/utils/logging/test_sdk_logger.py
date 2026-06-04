# pyright: strict
from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import cast

import pytest
from maivn_shared.infrastructure.logging.maivn_logger import MaivnLogger

import maivn._internal.utils.logging.sdk_logger as sdk_logger_module
from maivn._internal.utils.logging.sdk_logger import (
    MaivnSDKLogger,
    configure_logging,
    get_logger,
    get_optional_logger,
)

_WriteStructuredLog = Callable[..., None]


def _capture_structured_logs(
    monkeypatch: pytest.MonkeyPatch,
) -> list[dict[str, object]]:
    records: list[dict[str, object]] = []

    def _capture(
        _self: MaivnLogger,
        level: str,
        component: str,
        event: str,
        data: dict[str, object],
    ) -> None:
        records.append(
            {
                "level": level,
                "component": component,
                "event": event,
                "data": data,
            }
        )

    monkeypatch.setattr(MaivnLogger, "_write_structured_log", _capture)
    return records


def _record_data(records: list[dict[str, object]], index: int) -> dict[str, object]:
    """Return the ``data`` payload of a captured record as a typed mapping."""
    return cast(dict[str, object], records[index]["data"])


def _write_log(
    logger: MaivnSDKLogger,
    *,
    level: str,
    component: str,
    event: str,
    data: dict[str, object],
) -> None:
    """Invoke the overridden ``_write_structured_log`` without triggering reportPrivateUsage."""
    write_structured_log_attr = "_write_structured_log"
    writer = cast(_WriteStructuredLog, getattr(logger, write_structured_log_attr))
    writer(level=level, component=component, event=event, data=data)


def test_sdk_logger_adds_component_prefix_and_correlation_id(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    records = _capture_structured_logs(monkeypatch)
    logger = MaivnSDKLogger(console_level="OFF")
    records.clear()
    _ = logger.set_correlation_id("corr-1")

    _write_log(
        logger,
        level="INFO",
        component="SESSION",
        event="custom_event",
        data={"value": 1},
    )

    assert records[-1] == {
        "level": "INFO",
        "component": "MAIVN:SESSION",
        "event": "custom_event",
        "data": {"value": 1, "correlation_id": "corr-1"},
    }


def test_sdk_logger_preserves_existing_correlation_id(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    records = _capture_structured_logs(monkeypatch)
    logger = MaivnSDKLogger(console_level="OFF")
    records.clear()

    _write_log(
        logger,
        level="INFO",
        component="SESSION",
        event="custom_event",
        data={"correlation_id": "existing", "value": 1},
    )

    assert _record_data(records, -1)["correlation_id"] == "existing"


def test_sdk_logger_set_and_clear_correlation_id() -> None:
    logger = MaivnSDKLogger(console_level="OFF")

    generated = logger.set_correlation_id()
    assert generated
    assert logger.get_correlation_id() == generated

    logger.clear_correlation_id()
    assert logger.get_correlation_id() is None


def test_sdk_logger_logs_session_lifecycle_and_clears_session_context(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    records = _capture_structured_logs(monkeypatch)
    logger = MaivnSDKLogger(console_level="OFF")
    records.clear()

    logger.log_session_start("session-1", "assistant-1", "thread-1", mode="demo")
    logger.log_session_end("session-1", duration_ms=25, status="ok")

    assert records[0]["event"] == "session_start"
    start_data = _record_data(records, 0)
    assert start_data["assistant_id"] == "assistant-1"
    assert start_data["mode"] == "demo"
    assert records[1]["event"] == "session_end"
    end_data = _record_data(records, 1)
    assert end_data["duration_ms"] == 25
    assert end_data["status"] == "ok"
    context = logger.get_context()
    assert "session_id" not in context
    assert "thread_id" not in context


def test_sdk_logger_uses_error_level_for_failed_orchestration(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    records = _capture_structured_logs(monkeypatch)
    logger = MaivnSDKLogger(console_level="OFF")
    records.clear()

    logger.log_orchestration("failed", "compile", reason="boom")

    assert records[-1] == {
        "level": "ERROR",
        "component": "MAIVN:ORCHESTRATION",
        "event": "orchestration_failed",
        "data": {"operation": "compile", "reason": "boom"},
    }


def test_sdk_logger_logs_event_stream_keys_only_for_mapping_payloads(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    records = _capture_structured_logs(monkeypatch)
    logger = MaivnSDKLogger(console_level="OFF")
    records.clear()

    logger.log_event_stream("delta", {"a": 1, "b": 2}, source="sdk")
    logger.log_event_stream("delta", ["not", "a", "dict"])

    assert _record_data(records, 0) == {
        "event_type": "delta",
        "event_keys": ["a", "b"],
        "source": "sdk",
    }
    assert _record_data(records, 1) == {"event_type": "delta", "event_keys": None}


def test_get_logger_returns_singleton_instance(monkeypatch: pytest.MonkeyPatch) -> None:
    created: list[Path | str | None] = []

    def _factory(log_file_path: Path | str | None = None) -> MaivnSDKLogger:
        created.append(log_file_path)
        return MaivnSDKLogger(console_level="OFF")

    monkeypatch.setattr(sdk_logger_module, "_logger_instance", None)
    monkeypatch.setattr(sdk_logger_module, "_create_logger", _factory)

    first = get_logger("first.log")
    with pytest.warns(RuntimeWarning, match="already configured"):
        second = get_logger("second.log")

    assert first is second
    assert created == ["first.log"]


def test_get_optional_logger_falls_back_to_fresh_logger_when_singleton_creation_fails(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fallback_logger = MaivnSDKLogger(console_level="OFF")
    monkeypatch.setattr(
        sdk_logger_module, "get_logger", lambda: (_ for _ in ()).throw(RuntimeError())
    )
    monkeypatch.setattr(sdk_logger_module, "_create_logger", lambda: fallback_logger)

    logger = get_optional_logger()

    assert logger is fallback_logger


def test_configure_logging_creates_parent_directory_and_returns_logger(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    created: list[Path | str | None] = []

    def _factory(log_file_path: Path | str | None = None) -> MaivnSDKLogger:
        created.append(log_file_path)
        return MaivnSDKLogger(console_level="OFF")

    target = tmp_path / "logs" / "maivn.log"
    monkeypatch.setattr(sdk_logger_module, "_logger_instance", None)
    monkeypatch.setattr(sdk_logger_module, "_create_logger", _factory)

    logger = configure_logging(target)

    assert isinstance(logger, MaivnSDKLogger)
    assert target.parent.exists()
    assert created == [target]
