from __future__ import annotations

import logging
from logging.handlers import RotatingFileHandler
from pathlib import Path

LOG_FILE_NAME = "mediaflow.log"
LOG_MAX_BYTES = 5 * 1024 * 1024
LOG_BACKUP_COUNT = 5
_HANDLER_MARKER = "_mediaflow_application_log"


def configure_application_logging(runtime_dir: str | Path) -> Path:
    """Attach one bounded UTF-8 log file to every MediaFlow Pro logger."""

    log_dir = Path(runtime_dir).expanduser().resolve() / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)
    log_path = log_dir / LOG_FILE_NAME
    mediaflow_logger = logging.getLogger("mediaflow")
    mediaflow_logger.setLevel(logging.INFO)
    for handler in list(mediaflow_logger.handlers):
        if not isinstance(handler, RotatingFileHandler) or not getattr(
            handler,
            _HANDLER_MARKER,
            False,
        ):
            continue
        if Path(handler.baseFilename) == log_path:
            return log_path
        mediaflow_logger.removeHandler(handler)
        handler.close()
    handler = RotatingFileHandler(
        log_path,
        maxBytes=LOG_MAX_BYTES,
        backupCount=LOG_BACKUP_COUNT,
        encoding="utf-8",
    )
    setattr(handler, _HANDLER_MARKER, True)
    handler.setFormatter(
        logging.Formatter(
            "%(asctime)s %(levelname)s pid=%(process)d thread=%(threadName)s %(name)s: %(message)s"
        )
    )
    mediaflow_logger.addHandler(handler)
    return log_path
