"""Convenience helpers for pymod's logging.

The library is silent by default — both `pymod` and `pymod.wire` start
with `NullHandler` attached, so library users pay nothing unless they opt
in.

This module provides a one-line `configure()` for users who want quick
visibility, plus a `JsonFormatter` for structured output. Power users who
already have a logging setup can simply attach their own handlers to the
`pymod` and `pymod.wire` loggers and skip this module entirely.

Examples
--------
::

    import pymod.logging
    pymod.logging.configure(level="INFO")                         # human
    pymod.logging.configure(level="DEBUG", json=True)             # JSON
    pymod.logging.configure(level="INFO", wire_trace=True)        # + hex dumps
"""

from __future__ import annotations

import json as _json
import logging
import sys
from typing import IO, Any, Literal, Union

LogLevel = Union[int, Literal["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"]]

# stdlib LogRecord attributes that we don't want to copy verbatim into a
# JSON record. Anything outside this set is treated as user-supplied extra.
_RESERVED_LOGRECORD_FIELDS = frozenset({
    "args",
    "asctime",
    "created",
    "exc_info",
    "exc_text",
    "filename",
    "funcName",
    "levelname",
    "levelno",
    "lineno",
    "message",
    "module",
    "msecs",
    "msg",
    "name",
    "pathname",
    "process",
    "processName",
    "relativeCreated",
    "stack_info",
    "taskName",
    "thread",
    "threadName",
})


class JsonFormatter(logging.Formatter):
    """One log record per line, JSON-encoded. Suitable for log aggregators."""

    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "timestamp": self.formatTime(record, "%Y-%m-%dT%H:%M:%S"),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }
        if record.exc_info:
            payload["exception"] = self.formatException(record.exc_info)
        for key, value in record.__dict__.items():
            if key in _RESERVED_LOGRECORD_FIELDS or key.startswith("_"):
                continue
            try:
                _json.dumps(value)
                payload[key] = value
            except (TypeError, ValueError):
                payload[key] = repr(value)
        return _json.dumps(payload)


def _attach_handler(
    logger: logging.Logger,
    handler: logging.Handler,
) -> None:
    # Drop the NullHandler installed by `pymod.__init__`; install our own
    # only if no real handler is already attached.
    logger.handlers = [
        h for h in logger.handlers if not isinstance(h, logging.NullHandler)
    ]
    if not any(
        isinstance(h, logging.StreamHandler) and h is not handler
        for h in logger.handlers
    ):
        logger.addHandler(handler)


def configure(
    *,
    level: LogLevel = "INFO",
    json: bool = False,
    wire_trace: bool = False,
    stream: IO[str] | None = None,
) -> None:
    """Attach a handler to the `pymod` logger.

    Parameters
    ----------
    level : log level for the main `pymod` logger.
    json : emit structured JSON instead of human-readable lines.
    wire_trace : also enable the `pymod.wire` logger at DEBUG (hex dumps
                 of every PDU sent and received).
    stream : destination; defaults to ``sys.stderr``.
    """
    handler = logging.StreamHandler(stream or sys.stderr)
    if json:
        handler.setFormatter(JsonFormatter())
    else:
        handler.setFormatter(
            logging.Formatter("%(asctime)s %(name)s %(levelname)s: %(message)s")
        )

    pymod_logger = logging.getLogger("pymod")
    _attach_handler(pymod_logger, handler)
    pymod_logger.setLevel(level)

    if wire_trace:
        wire_logger = logging.getLogger("pymod.wire")
        _attach_handler(wire_logger, handler)
        wire_logger.setLevel(logging.DEBUG)
