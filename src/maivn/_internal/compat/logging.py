"""V1-compatible SDK logging entry points backed by stdlib logging."""

from __future__ import annotations

import logging
import threading
from pathlib import Path
from typing import Final

_LOGGER_NAME: Final[str] = 'maivn'
_logger_lock: Final[threading.Lock] = threading.Lock()
_configured_log_file_paths: Final[list[Path]] = []


def get_logger(log_file_path: Path | str | None = None) -> logging.Logger:
    """Get the process-global mAIvn SDK logger."""
    with _logger_lock:
        logger = logging.getLogger(_LOGGER_NAME)
        logger.setLevel(logging.DEBUG)
        if not logger.handlers:
            logger.addHandler(logging.NullHandler())
        if log_file_path is not None and not _configured_log_file_paths:
            path = Path(log_file_path)
            _configured_log_file_paths.append(path)
            _attach_file_handler(logger, path)
        return logger


def configure_logging(log_file_path: Path | str | None = None) -> logging.Logger:
    """Configure mAIvn SDK logging before other SDK components are imported."""
    if log_file_path is not None:
        path = Path(log_file_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        return get_logger(path)
    return get_logger()


def _attach_file_handler(logger: logging.Logger, path: Path) -> None:
    formatter = logging.Formatter(
        '%(asctime)s %(levelname)s %(name)s %(message)s',
    )
    handler = logging.FileHandler(path, encoding='utf-8')
    handler.setLevel(logging.DEBUG)
    handler.setFormatter(formatter)
    logger.addHandler(handler)


__all__ = ['configure_logging', 'get_logger']
