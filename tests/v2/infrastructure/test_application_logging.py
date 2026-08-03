from __future__ import annotations

import logging
from pathlib import Path

from mediaflow.infrastructure.application_logging import (
    LOG_BACKUP_COUNT,
    LOG_MAX_BYTES,
    configure_application_logging,
    shutdown_application_logging,
)


def test_application_log_persists_the_user_visible_error_reference(
    tmp_path: Path,
) -> None:
    log_path = configure_application_logging(tmp_path)
    reference = "ui-reference-42"
    logging.getLogger("mediaflow.desktop.controllers.project_controller").error(
        "UI operation failed [%s]: %s",
        reference,
        "test failure",
    )
    for handler in logging.getLogger("mediaflow").handlers:
        handler.flush()

    try:
        content = log_path.read_text(encoding="utf-8")
        assert reference in content
        assert "test failure" in content
        assert "pid=" in content
        assert "thread=" in content
        assert LOG_MAX_BYTES == 5 * 1024 * 1024
        assert LOG_BACKUP_COUNT == 5
    finally:
        shutdown_application_logging()


def test_application_logging_shutdown_closes_only_owned_handlers(
    tmp_path: Path,
) -> None:
    logger = logging.getLogger("mediaflow")
    sentinel = logging.NullHandler()
    original_level = logger.level
    logger.addHandler(sentinel)
    configure_application_logging(tmp_path)

    shutdown_application_logging()

    assert logger.level == original_level
    assert logger.handlers == [sentinel]
    logger.removeHandler(sentinel)
