# Python logging (standard library)

## What it is

`logging` is the module of Python's standard library for writing **log lines**: short messages that say what a program is doing. A log line has a **level** (how serious it is: `DEBUG`, `INFO`, `WARNING`, `ERROR`, `CRITICAL`), a message, a time and the name of the **logger** that wrote it. A **logger** is a named object you ask for with `logging.getLogger("name")` and write to with `logger.info("text")`. A **handler** decides where lines go (the screen, a file, the network). A **formatter** decides how a line is written as text. Loggers have a tree of names separated by dots: a logger named `slo_lab.application.assemble_order` hands its records up to its parent loggers, and finally to the **root logger**, where the handlers are attached.

## Why this repository uses it

Logs are one of the two places where this lesson's personal data leaks: `PlaceCheckout` writes the customer's card number into a log line on purpose (the values are test values), and the gateway's redaction must remove it before Loki stores it. The formatter writes one JSON object per line, adds the **trace id** and **span id** of the request being handled, and sends the same line to the console and, through the OpenTelemetry handler, to the gateway as OTLP. The code that writes the lines (`_log.info(...)`) does not know about OpenTelemetry.

## Where it appears

| Path | What it does with logging |
| --- | --- |
| `src/slo_lab/infrastructure/logging_setup.py` | The JSON formatter and the function that wires the root logger. |
| `src/slo_lab/application/place_checkout.py` | Writes the payment line that contains the test card number, the "checkout completed" line and the error line. |
| `src/slo_lab/interfaces/service_app.py`, `server.py` | Write the unhandled-error line, and the "listening" and "stopped" lines. |
| `src/slo_lab/interfaces/service_main.py`, `pager_main.py` | Call `configure_logging` at start-up. |
| `tests/unit/infrastructure/test_logging_setup.py` | Tests the formatter. |

## How it works underneath

When code calls `logger.info("text", extra={"order_id": 5})`, the logger first compares the level with its own threshold; if the level is too low the call ends. Otherwise it builds a **LogRecord** object: message, level, time (`record.created`, seconds since 1970), logger name, and every key of `extra` set as an attribute on the record. Then it passes the record to its own handlers and up the tree to the parents' handlers. Each handler calls its formatter's `format(record)` to get a string and writes it.

The OpenTelemetry SDK has its own `LoggingHandler`. Attached to the root logger, it turns each record into an OTLP log record and queues it for export. When a record is created while a span is active, the SDK reads the current span from the context and stores its trace id and span id with the record.

Standard attributes of a record (`name`, `msg`, `levelname`, `created`, ...) are always present. Anything else on `record.__dict__` was added by `extra=`, and the formatter in this repository copies those into the JSON output.

## Walkthrough of this repository's use

```python
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
```

The imports: `json` writes JSON text; `logging` is the module being explained; `sys.stdout` is the standard output stream; `datetime` and `UTC` format the time; `IO` and `Any` are only type hints. The three imports from `opentelemetry` are the trace API (to read the current span), the function that returns the logger provider the SDK installed, and the SDK's `LoggerProvider` and `LoggingHandler`.

```python
# Attributes every LogRecord has; anything else on a record came from ``extra=`` and is emitted as a field.
_STANDARD_FIELDS = frozenset(vars(logging.makeLogRecord({}))) | {"message", "asctime", "taskName"}
```

`logging.makeLogRecord({})` builds an empty record; `vars(...)` is its dictionary of attributes; `frozenset(...)` makes the keys an unchangeable set. `| {"message", "asctime", "taskName"}` adds three more names that the logging machinery may add later (the result of `getMessage()`, the formatted time, the name of an asyncio task on Python 3.12). Everything that is **not** in this set came from `extra=`.

```python
class JsonFormatter(logging.Formatter):
    """Formats a record as one JSON object, adding the current span's ids as ``trace_id`` and ``span_id``."""

    def __init__(self, service_name: str) -> None:
        """Remember the service name that goes into every line."""
        super().__init__()
        self._service_name = service_name

    def format(self, record: logging.LogRecord) -> str:
```

`JsonFormatter` inherits from `logging.Formatter` and replaces its `format` method. The constructor calls `super().__init__()` and remembers the service name, which goes in every line so a log store can tell the front from the back.

```python
    def format(self, record: logging.LogRecord) -> str:
        """Return the record as a single-line JSON document."""
        payload: dict[str, Any] = {
            "timestamp": datetime.fromtimestamp(record.created, tz=UTC).isoformat(timespec="milliseconds"),
            "level": record.levelname.lower(),
            "msg": record.getMessage(),
            "logger": record.name,
            "service": self._service_name,
        }
```

`format` starts a dictionary `payload` with the standard fields. `datetime.fromtimestamp(record.created, tz=UTC)` converts the record's time to a time-zone-aware date; `.isoformat(timespec="milliseconds")` writes it like `2026-10-27T01:55:48.813+00:00`. `record.levelname.lower()` gives `info`. `record.getMessage()` returns the message with any `%`-arguments filled in. `record.name` is the logger name.

```python
        context = trace.get_current_span().get_span_context()
        if context.is_valid:
            payload["trace_id"] = trace.format_trace_id(context.trace_id)
            payload["span_id"] = trace.format_span_id(context.span_id)
```

`trace.get_current_span()` returns the span active right now (an invalid, do-nothing span when none is active). `get_span_context()` returns its identifiers. `context.is_valid` is true for a real span. Then `trace.format_trace_id` writes the 128-bit id as 32 hexadecimal characters, and `format_span_id` the 64-bit span id as 16. They go in as `trace_id` and `span_id`, the exact names the Grafana and Loki configuration look for.

```python
        payload.update({key: value for key, value in record.__dict__.items() if key not in _STANDARD_FIELDS})
        if record.exc_info:
            payload["exception"] = self.formatException(record.exc_info)
        return json.dumps(payload, default=str)
```

`payload.update({...})` adds every `extra` field. The dictionary comprehension walks `record.__dict__.items()` and keeps entries whose key is not a standard field. If an exception is being handled (`record.exc_info`), `self.formatException(...)` produces the traceback text. `json.dumps(payload, default=str)` writes one line of JSON; `default=str` makes any value JSON cannot encode (a `Path`, a `datetime`) fall back to its text form instead of raising an error.

```python
def configure_logging(level: str, service_name: str, stream: IO[str] | None = None) -> None:
    """Send JSON log lines to ``stream`` (stdout) and, when OTLP logging is on, to the Collector too.

    The OTLP handler uses the same formatter, so the log body stored in Loki is the same JSON line.
    """
```

`configure_logging(level, service_name, stream=None)` is called once at start-up. `IO[str] | None` is the type: any text stream, or nothing.

```python
    root = logging.getLogger()
    for handler in list(root.handlers):
        root.removeHandler(handler)
    root.setLevel(level)
    formatter = JsonFormatter(service_name)
    console = logging.StreamHandler(stream or sys.stdout)
    console.setFormatter(formatter)
    root.addHandler(console)
```

It gets the root logger, removes every handler already installed (so calling it twice never doubles the lines; a unit test checks that), sets the level, creates one formatter and one `StreamHandler` writing to `stream` or to standard output. Writing to standard output is the container convention: Docker collects it with `docker logs`.

```python
    provider = get_logger_provider()
    if isinstance(provider, LoggerProvider):
        otlp = LoggingHandler(level=logging.NOTSET, logger_provider=provider)
        otlp.setFormatter(formatter)
        root.addHandler(otlp)
```

`get_logger_provider()` returns whatever provider is installed. When the process was started with `opentelemetry-instrument` and `OTEL_LOGS_EXPORTER=otlp`, it is the SDK's `LoggerProvider`, so `isinstance(provider, LoggerProvider)` is true and a second handler, the `LoggingHandler`, is attached with the **same formatter**. In a plain unit test the provider is the do-nothing default, so only the console handler is added. Because both handlers use the same formatter, the log body stored in Loki is the same JSON line you see on the console.

## Try it yourself

```bash
.venv/bin/python -c "
import logging
from slo_lab.infrastructure.logging_setup import configure_logging
configure_logging('INFO', 'demo')
logging.getLogger('x').info('hello', extra={'order_id': 5})"
```

Expected: one line of JSON with `"msg": "hello"`, `"service": "demo"` and `"order_id": 5`, and no `trace_id` (no span is active).

```bash
.venv/bin/python -m pytest tests/unit/infrastructure/test_logging_setup.py -q
```

Expected: `4 passed`.

```bash
docker compose logs checkout | tail -3
```

Expected (needs `make up`): JSON lines from the checkout service, each with `trace_id` and `span_id` when written inside a request.

## Mistakes people make

- **Building the message with an f-string inside the call** (`logger.info(f"order {id}")`). Prefer a fixed message plus `extra={...}`: fixed messages are easy to search for, and each field becomes a separate key. This repository does that.
- **Logging secrets or personal data.** None of the fields here are sensitive.
- **Adding handlers every time a function runs**, which duplicates lines. `configure_logging` removes the old ones first.
- **Using `print`.** It has no level, no time and no structure.
- **Configuring logging in a library.** Only the entry points call `configure_logging`.

## Check yourself

1. What is the difference between a logger and a handler? *A logger is what code writes to; a handler decides where the record goes.*
2. How do `extra={"order_id": 5}` values reach the JSON? *They become attributes on the record; the formatter copies every attribute that is not a standard field.*
3. Why is `trace_id` written as hex? *It is a 128-bit number; hex text is the standard form for ids and is what Loki and Tempo expect.*
4. Why does `configure_logging` remove existing handlers first? *So repeated calls do not write each line more than once.*
5. Why do both handlers share one formatter? *So the line in Loki is identical to the line on the console.*

## Further reading

- Python 3.12 `logging`: https://docs.python.org/3.12/library/logging.html
- Logging HOWTO: https://docs.python.org/3.12/howto/logging.html
- `LogRecord` attributes: https://docs.python.org/3.12/library/logging.html#logrecord-attributes
- OpenTelemetry Python logs: https://opentelemetry.io/docs/languages/python/instrumentation/#logs
