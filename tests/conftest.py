# pyright: strict
from __future__ import annotations

import os
from collections.abc import Iterator
from pathlib import Path

import pytest

# Route any file-backed logging during this suite to a repo-local ``test_logs/``
# directory so the pytest run never pollutes the shared ``logs/`` that real runs
# write to. Set before importing ``maivn`` so it precedes any logger init.
os.environ["MAIVN_LOG_DIR"] = str(Path(__file__).resolve().parent / "test_logs")

from maivn._internal.core.services import (  # noqa: E402 -- must follow MAIVN_LOG_DIR setup above
    interrupt_service as interrupt_service_module,
)
from maivn._internal.core.services.interrupt_service import InterruptService  # noqa: E402
from maivn._internal.utils.logging.sdk_logger import (  # noqa: E402
    reset_for_tests as reset_sdk_logger,
)
from maivn._internal.utils.reporting.context import set_current_reporter  # noqa: E402


@pytest.fixture(autouse=True)
def reset_sdk_global_state() -> Iterator[None]:
    original_interrupt_service = interrupt_service_module.get_interrupt_service()
    reset_sdk_logger()
    interrupt_service_module.set_interrupt_service(InterruptService())
    set_current_reporter(None)

    yield

    reset_sdk_logger()
    interrupt_service_module.set_interrupt_service(original_interrupt_service)
    set_current_reporter(None)
