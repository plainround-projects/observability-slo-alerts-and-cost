"""Structured JSON logging with the trace and span ids on every line, to stdout and to OTLP."""

from __future__ import annotations

import json
import logging
import sys
from datetime import UTC, datetime
from typing import IO, Any

from opentelemetry import trace
from opentelemetry._logs import get_logger_provider
from opentelemetry.sdk._logs import LoggerProvider, LoggingHandler

# Attributes every LogRecord has; anything else on a record came from ``extra=`` and is emitted as a field.
_STANDARD_FIELDS = frozenset(vars(logging.makeLogRecord({}))) | {"message", "asctime", "taskName"}


class JsonFormatter(logging.Formatter):
    """Formats a record as one JSON object, adding the current span's ids as ``trace_id`` and ``span_id``."""

    def __init__(self, service_name: str) -> None:
        """Remember the service name that goes into every line."""
        super().__init__()
        self._service_name = service_name

    def format(self, record: logging.LogRecord) -> str:
        """Return the record as a single-line JSON document."""
        payload: dict[str, Any] = {
            "timestamp": datetime.fromtimestamp(record.created, tz=UTC).isoformat(timespec="milliseconds"),
            "level": record.levelname.lower(),
            "msg": record.getMessage(),
            "logger": record.name,
            "service": self._service_name,
        }
        context = trace.get_current_span().get_span_context()
        if context.is_valid:
            payload["trace_id"] = trace.format_trace_id(context.trace_id)
            payload["span_id"] = trace.format_span_id(context.span_id)
        payload.update({key: value for key, value in record.__dict__.items() if key not in _STANDARD_FIELDS})
        if record.exc_info:
            payload["exception"] = self.formatException(record.exc_info)
        return json.dumps(payload, default=str)


def configure_logging(level: str, service_name: str, stream: IO[str] | None = None) -> None:
    """Send JSON log lines to ``stream`` (stdout) and, when OTLP logging is on, to the Collector too.

    The OTLP handler uses the same formatter, so the log body stored in Loki is the same JSON line.
    """
    root = logging.getLogger()
    for handler in list(root.handlers):
        root.removeHandler(handler)
    root.setLevel(level)
    formatter = JsonFormatter(service_name)
    console = logging.StreamHandler(stream or sys.stdout)
    console.setFormatter(formatter)
    root.addHandler(console)
    provider = get_logger_provider()
    if isinstance(provider, LoggerProvider):
        otlp = LoggingHandler(level=logging.NOTSET, logger_provider=provider)
        otlp.setFormatter(formatter)
        root.addHandler(otlp)
