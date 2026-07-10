"""Structured logging for Spider Panel.

Config generation emits a detailed audit record (see `log_config_event`)
so operators can trace every value that flows into an Xray config. All logs
go to stdout (12-factor) and are JSON-ish when JSON_LOGS is set.
"""
from __future__ import annotations

import json
import logging
import sys
from datetime import datetime, timezone


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


class SpiderFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        return True


def setup_logging(level: str = "INFO") -> logging.Logger:
    logger = logging.getLogger("spider")
    logger.setLevel(getattr(logging, level.upper(), logging.INFO))
    logger.propagate = False
    if not logger.handlers:
        handler = logging.StreamHandler(sys.stdout)
        handler.setFormatter(_Formatter())
        logger.addHandler(handler)
    return logger


class _Formatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        extra = getattr(record, "payload", None)
        if extra:
            return f"{_now()} [{record.levelname}] {record.getMessage()} {json.dumps(extra, default=str)}"
        return f"{_now()} [{record.levelname}] {record.getMessage()}"


log = setup_logging()


def log_config_event(event: str, **fields) -> None:
    """Audit log for config generation / xray control."""
    log.info(f"config_event: {event}")
    if fields:
        log.info("", extra={"payload": {"event": event, **fields}})


def log_xray(action: str, **fields) -> None:
    log.info(f"xray: {action}")
    if fields:
        log.info("", extra={"payload": {"action": action, **fields}})
