from __future__ import annotations

from maivn import logging as maivn_logging
from maivn import messages as maivn_messages


def test_logging_exports_are_available() -> None:
    assert "configure_logging" in maivn_logging.__all__
    assert "get_logger" in maivn_logging.__all__
    assert callable(maivn_logging.configure_logging)
    assert callable(maivn_logging.get_logger)


def test_messages_exports_are_available() -> None:
    for name in maivn_messages.__all__:
        assert hasattr(maivn_messages, name)
