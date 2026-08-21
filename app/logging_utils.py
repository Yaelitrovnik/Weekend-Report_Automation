from __future__ import annotations

import json
import logging
import os
import sys
import time
from typing import Any

_DEFAULT_RECORD_KEYS = frozenset(
    logging.LogRecord("", 0, "", 0, "", (), None).__dict__.keys()
)
_RESERVED_KEYS = _DEFAULT_RECORD_KEYS | {"message", "asctime"}

_CONFIGURED = False


class JsonLogFormatter(logging.Formatter):
    """Formats log records as single-line JSON objects.

    Standard LogRecord fields are mapped to a small fixed set of keys; any
    extra structured fields attached via `logger.info(msg, extra={...})` are
    merged in as additional top-level keys. This keeps log output
    greppable/parseable (e.g. `| jq 'select(.event=="module_finish")'`)
    without adding a dependency such as structlog or python-json-logger.
    """

    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "timestamp": self.formatTime(record, "%Y-%m-%dT%H:%M:%S"),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }
        if record.exc_info:
            payload["exception_trace"] = self.formatException(record.exc_info)
        for key, value in record.__dict__.items():
            if key in _RESERVED_KEYS:
                continue
            payload[key] = value
        return json.dumps(payload, default=str, sort_keys=True)


def configure_logging(*, level: str | None = None) -> None:
    """Configure the root logger for structured JSON output on stdout.

    Idempotent: safe to call from multiple entry points (web app, worker,
    tests) without attaching duplicate handlers. Level is read from
    WEEKEND_REPORT_LOG_LEVEL if not passed explicitly, defaulting to INFO.
    """
    global _CONFIGURED
    if _CONFIGURED:
        return
    configured_level = (
        level if level is not None else os.getenv("WEEKEND_REPORT_LOG_LEVEL", "INFO")
    )
    resolved_level = configured_level.strip().upper()
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(JsonLogFormatter())
    root = logging.getLogger()
    root.handlers = [handler]
    root.setLevel(resolved_level)
    _CONFIGURED = True


def get_logger(name: str) -> logging.Logger:
    """Return a module-level logger, configuring structured logging on first use."""
    configure_logging()
    return logging.getLogger(name)


def duration_ms(started: float) -> int:
    """Elapsed milliseconds since `started` (a time.monotonic() timestamp)."""
    return int((time.monotonic() - started) * 1000)