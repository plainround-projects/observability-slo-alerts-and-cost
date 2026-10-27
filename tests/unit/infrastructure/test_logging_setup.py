import io
import json
import logging

from slo_lab.infrastructure.logging_setup import JsonFormatter, configure_logging


def _record(message="hello", **extra):
    record = logging.LogRecord("test.logger", logging.INFO, __file__, 1, message, None, None)
    record.__dict__.update(extra)
    return record


def test_every_line_is_one_json_object_with_the_standard_fields():
    payload = json.loads(JsonFormatter("back").format(_record("order assembled", order_id=5)))
    assert payload["msg"] == "order assembled"
    assert (payload["level"], payload["service"], payload["logger"]) == ("info", "back", "test.logger")
    assert payload["order_id"] == 5
    assert "trace_id" not in payload


def test_inside_a_span_the_line_carries_the_trace_and_span_ids(memory_telemetry):
    tracer = memory_telemetry.tracer_provider.get_tracer("test")
    with tracer.start_as_current_span("work") as span:
        payload = json.loads(JsonFormatter("back").format(_record()))
        context = span.get_span_context()
    assert payload["trace_id"] == f"{context.trace_id:032x}"
    assert payload["span_id"] == f"{context.span_id:016x}"


def test_exceptions_are_included_as_text():
    try:
        raise ValueError("boom")
    except ValueError:
        import sys

        record = _record()
        record.exc_info = sys.exc_info()
    assert "ValueError: boom" in json.loads(JsonFormatter("back").format(record))["exception"]


def test_configure_logging_replaces_handlers_and_writes_json_to_the_stream():
    stream = io.StringIO()
    configure_logging("INFO", "front", stream)
    configure_logging("INFO", "front", stream)
    logging.getLogger("x").info("once")
    lines = stream.getvalue().splitlines()
    assert len(lines) == 1
    assert json.loads(lines[0])["service"] == "front"
