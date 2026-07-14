"""Structured JSON logging for CloudWatch.

Every log line is a single JSON object so CloudWatch Logs Insights can query
fields directly. A correlation id (the ``job_id``) is bound once per invocation
and attached to every subsequent line, which makes it possible to trace a
single document through the API and the worker.
"""
from __future__ import annotations

import json
import logging
import os
import sys
from typing import Any


class JsonFormatter(logging.Formatter):
    """Render a log record as one line of JSON."""

    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }
        # Anything passed via `extra=` lands as an attribute on the record.
        for key, value in getattr(record, "context", {}).items():
            payload[key] = value
        if record.exc_info:
            payload["exception"] = self.formatException(record.exc_info)
        return json.dumps(payload, default=str)


def get_logger(name: str) -> logging.Logger:
    """Return a logger that emits structured JSON to stdout exactly once."""
    logger = logging.getLogger(name)
    if not getattr(logger, "_json_configured", False):
        handler = logging.StreamHandler(sys.stdout)
        handler.setFormatter(JsonFormatter())
        logger.handlers = [handler]
        logger.setLevel(os.environ.get("LOG_LEVEL", "INFO"))
        logger.propagate = False
        logger._json_configured = True  # type: ignore[attr-defined]
    return logger


class BoundLogger:
    """A thin wrapper that attaches a fixed context (e.g. job_id) to every line."""

    def __init__(self, logger: logging.Logger, **context: Any) -> None:
        self._logger = logger
        self._context = context

    def bind(self, **extra: Any) -> BoundLogger:
        merged = {**self._context, **extra}
        return BoundLogger(self._logger, **merged)

    def _log(self, level: int, message: str, **extra: Any) -> None:
        context = {**self._context, **extra}
        self._logger.log(level, message, extra={"context": context})

    def info(self, message: str, **extra: Any) -> None:
        self._log(logging.INFO, message, **extra)

    def warning(self, message: str, **extra: Any) -> None:
        self._log(logging.WARNING, message, **extra)

    def error(self, message: str, **extra: Any) -> None:
        self._log(logging.ERROR, message, **extra)

    def exception(self, message: str, **extra: Any) -> None:
        context = {**self._context, **extra}
        self._logger.error(message, exc_info=True, extra={"context": context})
