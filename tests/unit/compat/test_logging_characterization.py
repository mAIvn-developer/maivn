"""Characterization tests for ``maivn.get_logger`` and ``maivn.configure_logging``.

The SDK logger is process-global and the first configured log file wins for the
life of the process, so every scenario runs in a fresh interpreter. The child
only imports the SDK and writes under ``tmp_path``; it never touches the network.
"""

from __future__ import annotations

import json
import subprocess
import sys
from textwrap import dedent
from typing import TYPE_CHECKING

import maivn

if TYPE_CHECKING:
    from pathlib import Path


def _run(script: str, tmp_path: Path) -> dict[str, object]:
    """Run ``script`` in a fresh interpreter and return the JSON it prints last."""
    completed = subprocess.run(  # noqa: S603 - fixed interpreter, test-authored script.
        [sys.executable, '-c', dedent(script), str(tmp_path)],
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )
    assert completed.returncode == 0, completed.stderr
    return json.loads(completed.stdout.strip().splitlines()[-1])


def test_get_logger_returns_the_debug_maivn_logger_with_a_null_handler() -> None:
    """Without a file the logger is ``maivn`` at DEBUG with at least one handler."""
    logger = maivn.get_logger()
    assert logger.name == 'maivn'
    assert logger.level == 10  # noqa: PLR2004 - logging.DEBUG
    assert logger.handlers
    assert maivn.get_logger() is logger
    assert maivn.configure_logging() is logger


def test_first_configured_file_receives_records_and_later_paths_are_ignored(
    tmp_path: Path,
) -> None:
    """``configure_logging`` creates parent folders; only the first file is attached."""
    result = _run(
        """
        import json
        import logging
        import sys
        from pathlib import Path

        import maivn

        root = Path(sys.argv[1])
        first = root / 'nested' / 'deeper' / 'sdk.log'
        second = root / 'other' / 'ignored.log'
        logger = maivn.configure_logging(first)
        again = maivn.configure_logging(str(second))
        same = maivn.get_logger(root / 'third.log')
        logger.debug('debug line %s', 1)
        logger.info('info line')
        file_handlers = [h for h in logger.handlers if isinstance(h, logging.FileHandler)]
        null_handlers = [h for h in logger.handlers if isinstance(h, logging.NullHandler)]
        for handler in file_handlers:
            handler.flush()
        print(json.dumps({
            'same': logger is again is same,
            'name': logger.name,
            'level': logger.level,
            'file_handlers': [
                [Path(h.baseFilename).name, h.level, h.encoding] for h in file_handlers
            ],
            'null_handlers': len(null_handlers),
            'first': first.read_text(encoding='utf-8'),
            'second_dir_exists': second.parent.exists(),
            'second_exists': second.exists(),
            'third_exists': (root / 'third.log').exists(),
        }))
        """,
        tmp_path,
    )
    lines = str(result['first']).splitlines()
    assert result['same'] is True
    assert result['name'] == 'maivn'
    assert result['level'] == 10  # noqa: PLR2004 - logging.DEBUG
    assert result['file_handlers'] == [['sdk.log', 10, 'utf-8']]
    assert result['null_handlers'] == 1
    assert len(lines) == 2  # noqa: PLR2004 - two records written
    assert lines[0].endswith(' DEBUG maivn debug line 1')
    assert lines[1].endswith(' INFO maivn info line')
    assert result['second_dir_exists'] is True
    assert result['second_exists'] is False
    assert result['third_exists'] is False


def test_get_logger_with_a_path_attaches_a_file_without_creating_folders(
    tmp_path: Path,
) -> None:
    """``get_logger(path)`` attaches the file directly; it does not mkdir."""
    result = _run(
        """
        import json
        import sys
        from pathlib import Path

        import maivn

        root = Path(sys.argv[1])
        logger = maivn.get_logger(str(root / 'direct.log'))
        logger.warning('careful')
        for handler in logger.handlers:
            handler.flush()
        print(json.dumps({'text': (root / 'direct.log').read_text(encoding='utf-8')}))
        """,
        tmp_path,
    )
    assert str(result['text']).rstrip('\n').endswith(' WARNING maivn careful')


def test_get_logger_into_a_missing_folder_raises(tmp_path: Path) -> None:
    """Without ``configure_logging`` a missing parent folder is an error."""
    result = _run(
        """
        import json
        import sys
        from pathlib import Path

        import maivn

        root = Path(sys.argv[1])
        try:
            maivn.get_logger(root / 'missing' / 'x.log')
        except FileNotFoundError:
            print(json.dumps({'raised': True}))
        else:
            print(json.dumps({'raised': False}))
        """,
        tmp_path,
    )
    assert result == {'raised': True}


def test_a_failed_log_file_does_not_disable_a_later_valid_one(tmp_path: Path) -> None:
    """A ``get_logger`` call on a missing directory leaves file logging configurable (U05-D8)."""
    result = _run(
        """
        import json
        import sys
        from pathlib import Path

        import maivn

        root = Path(sys.argv[1])
        try:
            maivn.get_logger(root / 'missing' / 'sdk.log')
            failed = False
        except OSError:
            failed = True
        valid = root / 'valid.log'
        logger = maivn.configure_logging(valid)
        logger.info('after the failure')
        for handler in logger.handlers:
            handler.flush()
        print(json.dumps({
            'failed': failed,
            'text': valid.read_text(encoding='utf-8') if valid.exists() else '',
        }))
        """,
        tmp_path,
    )

    assert result['failed'] is True
    assert str(result['text']).rstrip().endswith(' INFO maivn after the failure')
