"""Centralized logging setup with NDJSON file output only."""

import json
import logging
from datetime import datetime, timezone
from logging.handlers import RotatingFileHandler
from pathlib import Path


class NDJSONFormatter(logging.Formatter):
    """Format log records as newline-delimited JSON."""

    def format(self, record: logging.LogRecord) -> str:
        log_entry = {
            "timestamp": datetime.fromtimestamp(
                record.created, tz=timezone.utc
            ).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "module": record.module,
            "function": record.funcName,
            "line": record.lineno,
            "message": record.getMessage(),
        }
        if record.exc_info and record.exc_info[0]:
            log_entry["exception"] = self.formatException(record.exc_info)
        return json.dumps(log_entry, default=str)


def setup_logging(
    log_path: str = "logs/backend.ndjson",
    max_bytes: int = 10 * 1024 * 1024,
    backup_count: int = 5,
) -> None:
    """Configure root logger with an NDJSON file handler only.

    No console/stderr handler is attached: the app is a Textual TUI, and any
    stray stdout/stderr writes corrupt the terminal rendering.

    Args:
        log_path: Path to the NDJSON log file.
        max_bytes: Maximum size in bytes before rotating.
        backup_count: Number of rotated backup files to keep.
    """
    path = Path(log_path)
    path.parent.mkdir(parents=True, exist_ok=True)

    root_logger = logging.getLogger()
    root_logger.setLevel(logging.DEBUG)

    # Suppress noisy third-party loggers
    for noisy in (
        "markdown_it",
        "hpack",
        "h2",
        "httpcore",
        "hickory_resolver",
        "hickory_net",
        "cookie_store",
        "asyncio",
        "rustls",
        "hyper_util",
        "reqwest",
        "primp",
    ):
        logging.getLogger(noisy).setLevel(logging.WARNING)

    file_handler = RotatingFileHandler(
        str(path), maxBytes=max_bytes, backupCount=backup_count, encoding="utf-8"
    )
    file_handler.setLevel(logging.DEBUG)
    file_handler.setFormatter(NDJSONFormatter())
    root_logger.addHandler(file_handler)
