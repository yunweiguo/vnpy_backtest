from __future__ import annotations

import json
import logging
from logging.handlers import RotatingFileHandler
from pathlib import Path
from typing import Optional

_CONFIGURED = False

_BASE_RECORD_KEYS = {
    "name",
    "msg",
    "args",
    "levelname",
    "levelno",
    "pathname",
    "filename",
    "module",
    "exc_info",
    "exc_text",
    "stack_info",
    "lineno",
    "funcName",
    "created",
    "msecs",
    "relativeCreated",
    "thread",
    "threadName",
    "process",
    "processName",
    "message",
    "asctime",
}


class ExtraFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        base = super().format(record)
        extras = {
            key: value
            for key, value in record.__dict__.items()
            if key not in _BASE_RECORD_KEYS
        }
        if extras:
            try:
                extra_json = json.dumps(extras, ensure_ascii=False)
            except TypeError:
                extra_json = str(extras)
            return f"{base} | extra={extra_json}"
        return base


def configure_logging(
    level: str,
    file_path: Optional[str] = None,
    console: bool = True,
) -> None:
    global _CONFIGURED
    if _CONFIGURED:
        return

    log_level = getattr(logging, level.upper(), logging.INFO)
    handlers = []

    fmt = "%(asctime)s [%(levelname)s] %(name)s - %(message)s"
    datefmt = "%Y-%m-%d %H:%M:%S"

    if file_path:
        path = Path(file_path).expanduser()
        path.parent.mkdir(parents=True, exist_ok=True)
        file_handler = RotatingFileHandler(path, maxBytes=5 * 1024 * 1024, backupCount=5, encoding="utf-8")
        file_handler.setFormatter(ExtraFormatter(fmt=fmt, datefmt=datefmt))
        handlers.append(file_handler)

    if console:
        console_handler = logging.StreamHandler()
        console_handler.setFormatter(ExtraFormatter(fmt=fmt, datefmt=datefmt))
        handlers.append(console_handler)

    if not handlers:
        logging.basicConfig(level=log_level, format=fmt, datefmt=datefmt)
    else:
        logging.basicConfig(level=log_level, handlers=handlers)

    _CONFIGURED = True


def get_logger(name: str) -> logging.Logger:
    return logging.getLogger(name)
