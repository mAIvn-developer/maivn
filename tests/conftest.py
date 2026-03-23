from __future__ import annotations

import pytest

import maivn._internal.utils.logging.sdk_logger as sdk_logger_module
from maivn._internal.core.services import interrupt_service as interrupt_service_module
from maivn._internal.core.services.interrupt_service import InterruptService
from maivn._internal.utils.reporting.context import set_current_reporter


@pytest.fixture(autouse=True)
def _reset_sdk_global_state() -> None:
    original_interrupt_service = interrupt_service_module.get_interrupt_service()
    sdk_logger_module._logger_instance = None
    interrupt_service_module.set_interrupt_service(InterruptService())
    set_current_reporter(None)

    yield

    sdk_logger_module._logger_instance = None
    interrupt_service_module.set_interrupt_service(original_interrupt_service)
    set_current_reporter(None)
